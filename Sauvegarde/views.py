# ── IMPORTS ───────────────────────────────────────────────────────────────────
import json
from datetime import datetime
from pathlib import Path

from django.core.cache import cache
from django.http import JsonResponse, HttpResponse, FileResponse
from django.shortcuts import redirect
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

from Registration.models import Token, Utilisateur, verifier_droit_admin, enregistrer_audit

from .models import ConfigurationSauvegarde, HistoriqueSauvegarde, obtenir_configuration
from .services.export_service import executer_sauvegarde
from .services.restauration_service import (
    analyser as analyser_restauration,
    confirmer as confirmer_restauration,
    ErreurRestauration,
)
from .services.chiffrement_service import chiffrer_texte
from .services import google_drive_service


def _get_user_from_token(request):
    """
    Résout le token du header Authorization: Token <cle> en un objet Utilisateur.
    Même logique que Registration/views.py et Produits/views/_auth.py —
    dupliquée ici (pas de module d'auth partagé dans ce projet).
    """
    auth = request.headers.get('Authorization', '')
    if not auth:
        return None

    token_key = auth.replace('Token ', '')

    try:
        token = Token.objects.select_related('utilisateur').get(cle=token_key)
        return token.utilisateur
    except Token.DoesNotExist:
        return None


# ── SÉRIALISEURS ──────────────────────────────────────────────────────────────

def _serialiseConfiguration(config):
    return {
        'active':                     config.active,
        'frequence':                  config.frequence,
        'heure_declenchement':        config.heure_declenchement.strftime('%H:%M'),
        'jour_semaine':               config.jour_semaine,
        'jour_mois':                  config.jour_mois,
        'type_sauvegarde':            config.type_sauvegarde,
        'destination':                config.destination,
        'google_drive_connecte':      config.google_drive_connecte,
        'derniere_execution_reussie': config.derniere_execution_reussie.isoformat() if config.derniere_execution_reussie else None,
        'date_maj':                   config.date_maj.isoformat(),
    }


def _serialiseHistorique(h):
    return {
        'id':                      h.id,
        'date_execution':          h.date_execution.isoformat(),
        'type_sauvegarde':         h.type_sauvegarde,
        'destination':             h.destination,
        'declenche_par_nom':       f"{h.declenche_par.prenom} {h.declenche_par.nom}" if h.declenche_par_id else None,
        'statut':                  h.statut,
        'message_erreur':          h.message_erreur,
        'taille_octets':           h.taille_octets,
        'nombre_enregistrements':  h.nombre_enregistrements,
        'est_sauvegarde_securite': h.est_sauvegarde_securite,
        'telechargeable':          h.statut == 'succes',
    }


# ── CONFIGURATION DE LA PLANIFICATION ─────────────────────────────────────────
@csrf_exempt
def configuration(request):
    """GET : lit la configuration courante. PUT : la modifie (fréquence, heure,
    type, destination...) — réservé aux admins ayant le droit gestion_sauvegardes."""
    if request.method not in ('GET', 'PUT'):
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_sauvegardes'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_sauvegardes'}}, status=403)

    config = obtenir_configuration()

    if request.method == 'GET':
        return JsonResponse({'configuration': _serialiseConfiguration(config)}, status=200)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    if 'frequence' in data and data['frequence'] not in dict(ConfigurationSauvegarde.FREQUENCES):
        return JsonResponse({'error': 'frequence invalide'}, status=400)
    if 'type_sauvegarde' in data and data['type_sauvegarde'] not in dict(ConfigurationSauvegarde.TYPES_SAUVEGARDE):
        return JsonResponse({'error': 'type_sauvegarde invalide'}, status=400)
    if 'destination' in data and data['destination'] not in dict(ConfigurationSauvegarde.DESTINATIONS):
        return JsonResponse({'error': 'destination invalide'}, status=400)

    # 'heure_declenchement' arrive en "HH:MM" (voir _serialiseConfiguration,
    # symétrique) : setattr() avec la chaîne brute laisse l'attribut EN
    # MÉMOIRE comme un str après config.save() (la conversion en `time` ne se
    # produit que côté SQL, pas sur l'instance Python) — _serialiseConfiguration
    # appelée juste après avec ce même objet plantait alors sur .strftime()
    # (AttributeError: 'str' object has no attribute 'strftime', constaté en
    # conditions réelles). Converti explicitement ici pour rester un vrai
    # datetime.time sur l'instance retournée.
    if 'heure_declenchement' in data:
        try:
            data['heure_declenchement'] = datetime.strptime(data['heure_declenchement'], '%H:%M').time()
        except (ValueError, TypeError):
            return JsonResponse({'error': "heure_declenchement invalide (attendu HH:MM)", 'error_code': 'INVALID_TIME'}, status=400)

    champs_modifiables = ['active', 'frequence', 'heure_declenchement', 'jour_semaine', 'jour_mois', 'type_sauvegarde', 'destination']
    for champ in champs_modifiables:
        if champ in data:
            setattr(config, champ, data[champ])
    config.modifie_par = utilisateur
    config.save()

    enregistrer_audit(utilisateur, 'sauvegarde.configurer', "A modifié la configuration des sauvegardes planifiées")

    return JsonResponse({'message': 'Configuration mise à jour avec succès', 'configuration': _serialiseConfiguration(config)}, status=200)


# ── HISTORIQUE DES EXÉCUTIONS ─────────────────────────────────────────────────
@csrf_exempt
def historique(request):
    """Liste toutes les exécutions passées (manuelles, planifiées, sauvegardes
    de sécurité), les plus récentes en premier — réservé à gestion_sauvegardes."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_sauvegardes'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_sauvegardes'}}, status=403)

    entrees = HistoriqueSauvegarde.objects.select_related('declenche_par').all()

    return JsonResponse({'historique': [_serialiseHistorique(h) for h in entrees]}, status=200)


# pas de suppression d'historique ici (retirée intentionnellement, demande
# explicite) : contrairement aux historiques support/signalements (voir
# Messagerie/Produits views.py, masquage personnel via historique_masque_pour),
# l'historique des sauvegardes reste toujours COMPLET et INTACT pour tous les
# admins gestion_sauvegardes — affiché en lecture seule dans un modal dédié
# (bouton "Voir l'historique", AdminDashboard.jsx), aucune action de retrait
# possible, ni personnelle ni globale.


# ── DÉCLENCHEMENT MANUEL ───────────────────────────────────────────────────────
@csrf_exempt
def declencher(request):
    """
    Lance une sauvegarde immédiatement (synchrone — le volume de données de ce
    projet reste rapide à traiter), indépendamment de la planification. Type et
    destination reprennent la configuration courante, sauf s'ils sont fournis
    explicitement dans le corps de la requête (essai ponctuel).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_sauvegardes'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_sauvegardes'}}, status=403)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    config = obtenir_configuration()
    type_sauvegarde = data.get('type_sauvegarde') or config.type_sauvegarde
    destination = data.get('destination') or config.destination

    if type_sauvegarde not in dict(ConfigurationSauvegarde.TYPES_SAUVEGARDE):
        return JsonResponse({'error': 'type_sauvegarde invalide'}, status=400)
    if destination not in dict(ConfigurationSauvegarde.DESTINATIONS):
        return JsonResponse({'error': 'destination invalide'}, status=400)

    resultat = executer_sauvegarde(type_sauvegarde=type_sauvegarde, destination=destination, declenche_par=utilisateur)

    enregistrer_audit(
        utilisateur, 'sauvegarde.declencher',
        f"A déclenché une sauvegarde {resultat.type_sauvegarde} vers {resultat.destination} — statut : {resultat.statut}"
    )

    if resultat.statut != 'succes':
        return JsonResponse({'error': resultat.message_erreur or 'La sauvegarde a échoué', 'historique': _serialiseHistorique(resultat)}, status=500)

    return JsonResponse({'message': 'Sauvegarde effectuée avec succès', 'historique': _serialiseHistorique(resultat)}, status=201)


# ── TÉLÉCHARGER UNE SAUVEGARDE ─────────────────────────────────────────────────
@csrf_exempt
def historique_telecharger(request, id):
    """
    Renvoie le fichier .rhtbackup chiffré d'une exécution réussie, que la
    destination soit locale ou Google Drive (proxy transparent pour Drive —
    l'admin n'a pas besoin d'accès direct au compte Drive connecté). Le
    contenu renvoyé reste chiffré : aucun déchiffrement n'a lieu ici.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_sauvegardes'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_sauvegardes'}}, status=403)

    try:
        h = HistoriqueSauvegarde.objects.get(id=id)
    except HistoriqueSauvegarde.DoesNotExist:
        return JsonResponse({'error': 'Sauvegarde introuvable', 'error_code': 'BACKUP_NOT_FOUND'}, status=404)

    if h.statut != 'succes':
        return JsonResponse({'error': "Cette sauvegarde n'a pas réussi, aucun fichier n'est disponible"}, status=400)

    nom_fichier = f"Sauvegarde-RekoltHt-{h.date_execution:%Y%m%d-%H%M%S}.rhtbackup"

    try:
        if h.destination == 'google_drive':
            # flux (pas .content) : une sauvegarde "complète" inclut tous les
            # médias uploadés et peut facilement dépasser 300 Mo (constaté en
            # conditions réelles) — tout charger en mémoire d'un coup avant de
            # répondre est ce qui cassait le téléchargement (connexion coupée
            # côté navigateur, "Failed to fetch")
            flux = google_drive_service.telecharger_sauvegarde_en_flux(h.google_drive_file_id)
            reponse = FileResponse(flux, content_type='application/octet-stream')
        else:
            chemin = Path(h.chemin_fichier_local)
            if not chemin.is_file():
                return JsonResponse({'error': 'Fichier local introuvable sur le serveur', 'error_code': 'FILE_NOT_FOUND'}, status=404)
            # même raison : FileResponse (streaming) plutôt que
            # HttpResponse(chemin.read_bytes()) — voir commentaire ci-dessus
            reponse = FileResponse(open(chemin, 'rb'), content_type='application/octet-stream')
    except google_drive_service.ErreurGoogleDrive as e:
        return JsonResponse({'error': str(e)}, status=502)

    reponse['Content-Disposition'] = f'attachment; filename="{nom_fichier}"'
    return reponse


# ── RESTAURATION — ANALYSE (aperçu, aucune écriture) ─────────────────────────
@csrf_exempt
def restaurer_analyser(request):
    """
    Déchiffre et lit le fichier .rhtbackup envoyé, retourne un résumé (date,
    type, nombre d'enregistrements par modèle, media inclus) SANS RIEN
    MODIFIER — réservé au super admin, même exigence que restaurer_confirmer.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'super_admin'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'super_admin'}}, status=403)

    fichier = request.FILES.get('fichier')
    if not fichier:
        return JsonResponse({'error': 'Le fichier de sauvegarde est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'fichier'}}, status=400)

    try:
        resume = analyser_restauration(fichier.read())
    except ErreurRestauration as e:
        return JsonResponse({'error': str(e), 'error_code': 'INVALID_BACKUP_FILE'}, status=400)

    return JsonResponse({'resume': resume}, status=200)


# ── RESTAURATION — CONFIRMATION (écriture réelle) ─────────────────────────────
@csrf_exempt
def restaurer_confirmer(request):
    """
    Restauration RÉELLE : prend d'abord une sauvegarde de sécurité de l'état
    courant, puis remplace les données applicatives par celles du fichier
    envoyé. Action la plus destructrice du système — réservée au super admin
    (pas seulement gestion_sauvegardes, voir DroitsAdmin.gestion_sauvegardes).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'super_admin'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'super_admin'}}, status=403)

    fichier = request.FILES.get('fichier')
    if not fichier:
        return JsonResponse({'error': 'Le fichier de sauvegarde est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'fichier'}}, status=400)

    try:
        resultat = confirmer_restauration(fichier.read(), restaure_par=utilisateur)
    except ErreurRestauration as e:
        return JsonResponse({'error': str(e), 'error_code': 'RESTORE_FAILED'}, status=400)

    enregistrer_audit(
        utilisateur, 'sauvegarde.restaurer',
        f"A restauré une sauvegarde ({resultat['nombre_enregistrements_restaures']} enregistrements) — "
        f"sauvegarde de sécurité prise avant restauration : id {resultat['sauvegarde_securite_id']}"
    )

    return JsonResponse({'message': 'Restauration effectuée avec succès', 'resultat': resultat}, status=200)


# ── GOOGLE DRIVE — CONNEXION (OAuth2) ─────────────────────────────────────────
@csrf_exempt
def google_autoriser(request):
    """
    Construit l'URL de consentement Google OAuth2, à ouvrir côté frontend dans
    un nouvel onglet — réservé à gestion_sauvegardes, comme les autres actions
    de ce module (configuration/déclenchement/téléchargement/déconnexion,
    voir configuration/declencher/historique_telecharger/google_deconnecter
    ci-dessus/ci-dessous) : un admin qui n'a QUE ce droit doit pouvoir
    configurer entièrement les sauvegardes, y compris connecter Google Drive,
    pas seulement les déconnecter. Le `state` généré est mémorisé côté serveur
    (cache, expire après 10 min) associé à l'admin à l'origine de la demande :
    pas de session Django dans ce projet (auth par token), donc rien d'autre
    ne permettrait de relier le callback Google (appelé directement par le
    navigateur, sans notre header Authorization) à un compte.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_sauvegardes'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_sauvegardes'}}, status=403)

    try:
        url, state, code_verifier = google_drive_service.construire_url_autorisation()
    except google_drive_service.ErreurGoogleDrive as e:
        return JsonResponse({'error': str(e), 'error_code': 'GOOGLE_DRIVE_NOT_CONFIGURED'}, status=400)

    # code_verifier doit voyager jusqu'au callback (voir construire_url_autorisation,
    # Sauvegarde/services/google_drive_service.py) — même cache que l'admin_id,
    # sous la même clé state
    cache.set(f"gdrive_oauth_state:{state}", {'admin_id': utilisateur.id, 'code_verifier': code_verifier}, timeout=600)

    return JsonResponse({'url_autorisation': url}, status=200)


@csrf_exempt
def google_callback(request):
    """
    Appelée directement par Google après consentement (pas par le frontend) —
    aucun header Authorization disponible ici, l'identité de l'admin est
    retrouvée via le `state` mémorisé par google_autoriser ci-dessus. Redirige
    toujours vers le tableau de bord admin, avec un paramètre `google` indiquant
    le résultat (le frontend n'a rien d'autre à faire que lire ce paramètre).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    cible = f"{settings.FRONTEND_URL}/admin/dashboard?tab=sauvegarde&google="

    if request.GET.get('error'):
        return redirect(cible + 'refuse')

    code = request.GET.get('code')
    state = request.GET.get('state')
    if not code or not state:
        return redirect(cible + 'erreur')

    cle_cache = f"gdrive_oauth_state:{state}"
    donnees_cache = cache.get(cle_cache)
    if not donnees_cache:
        return redirect(cible + 'erreur')
    cache.delete(cle_cache)
    admin_id = donnees_cache['admin_id']
    code_verifier = donnees_cache['code_verifier']

    try:
        refresh_token = google_drive_service.echanger_code_contre_refresh_token(code, code_verifier)
    except google_drive_service.ErreurGoogleDrive as e:
        # le frontend ne reçoit qu'un simple "erreur" dans l'URL de
        # redirection (voir docstring ci-dessus) — sans cette trace côté
        # serveur, un échec d'échange de code (scope, code déjà consommé,
        # identifiants invalides...) était impossible à diagnostiquer
        print(f"ERREUR connexion Google Drive (échange du code) :", e)
        return redirect(cible + 'erreur')

    config = obtenir_configuration()
    config.google_drive_connecte = True
    config.google_drive_refresh_token_chiffre = chiffrer_texte(refresh_token)
    config.save(update_fields=['google_drive_connecte', 'google_drive_refresh_token_chiffre'])

    try:
        admin = Utilisateur.objects.get(id=admin_id)
        enregistrer_audit(admin, 'sauvegarde.google_connecter', "A connecté un compte Google Drive pour les sauvegardes")
    except Utilisateur.DoesNotExist:
        pass

    return redirect(cible + 'connecte')


@csrf_exempt
def google_deconnecter(request):
    """Oublie la connexion Google Drive stockée — réservé à gestion_sauvegardes."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_sauvegardes'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_sauvegardes'}}, status=403)

    google_drive_service.deconnecter()
    enregistrer_audit(utilisateur, 'sauvegarde.google_deconnecter', "A déconnecté Google Drive des sauvegardes")

    return JsonResponse({'message': 'Google Drive déconnecté avec succès'}, status=200)
