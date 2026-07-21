# ── IMPORTS ───────────────────────────────────────────────────────────────────
import base64          # décodage des images/logos envoyés en base64 depuis le frontend
import json            # lecture/écriture du corps des requêtes HTTP au format JSON
import random          # génération du code PIN à 4 chiffres pour la réinitialisation
import re              # normalisation des identifiants de pièce (comparaison OCR / unicité)
import secrets         # génération des tokens d'authentification cryptographiquement sécurisés
import unicodedata     # retrait des accents pour comparer noms/identifiants de façon fiable
import requests        # appel HTTP à l'API Google OAuth2 pour valider le token Google

from datetime import timedelta                        # calcul de la date d'expiration (code PIN : +15 min)
from django.conf import settings                      # accès aux variables de configuration (settings.py)
from django.core.files.base import ContentFile        # crée un fichier Django en mémoire depuis des octets
from django.core.mail import send_mail                # envoi d'emails SMTP (code PIN de réinitialisation)
from django.http import JsonResponse, HttpResponse    # HttpResponse : réponse binaire (PDF de prévisualisation du contrat)
from django.utils import timezone                     # horodatage UTC cohérent avec USE_TZ = True
from django.views.decorators.csrf import csrf_exempt  # désactive la protection CSRF (API JSON, pas de cookies)
from django.db import IntegrityError, transaction     # IntegrityError pour les doublons, transaction pour l'atomicité

from .models import (
    Utilisateur,            # modèle parent : identité + mot de passe + statut
    Profil,                 # informations complémentaires (bio, photo, rôle, GPS)
    Entreprise,             # compte entreprise, hérite d'Utilisateur (email/mdp propres)
    DemandeVerification,    # dossier de vérification KYC (individuel ou entreprise)
    CodeReinitialisation,   # code PIN à 4 chiffres, durée de vie 15 minutes
    Token,                  # token de session persisté en base (single-session)
    haser_password,         # hash SHA-256 avec sel aléatoire
    verifier_password,      # vérifie un mot de passe en clair contre son hash
)
from .services.ocr_service import (
    extraire_infos_piece,       # OCR (PaddleOCR) sur un document, étape 02
    parser_date_naissance,      # convertit la date extraite (str) en objet date
    SEUIL_CONFIANCE_MINIMUM,    # score PaddleOCR minimum avant de juger le document illisible
)


# ── CONNEXION VIA GOOGLE ──────────────────────────────────────────────────────
@csrf_exempt  # pas de cookie de session → pas besoin de CSRF
def google_connection(request):
    """Connexion via Google — l'utilisateur doit déjà avoir un compte."""
    if request.method != "POST":
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data         = json.loads(request.body)   # désérialise le JSON
        google_token = data.get('token')           # token OAuth2 fourni par le frontend

        if not google_token:
            return JsonResponse({'error': 'Token Google manquant'}, status=400)

        # appel à l'API Google pour récupérer l'email associé au token
        google_response = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {google_token}'}
        )

        if google_response.status_code != 200:
            # token invalide ou expiré côté Google
            return JsonResponse({'error': 'Token Google invalide'}, status=401)

        google_data = google_response.json()
        email       = google_data.get('email')

        if not email:
            return JsonResponse({'error': 'Email Google non disponible'}, status=400)

        # vérifie que l'utilisateur possède déjà un compte
        try:
            utilisateur = Utilisateur.objects.get(email=email)
        except Utilisateur.DoesNotExist:
            # message bilingue : guide l'utilisateur vers l'inscription
            return JsonResponse({
                'error': 'Kont sa a pa egziste. Tanpri enskri dabò.'
            }, status=404)

        # marquer l'utilisateur comme en ligne (est_actif = indicateur de présence)
        if not utilisateur.est_actif:
            utilisateur.modifier_est_actif()

        # single-session : supprimer tous les tokens existants avant d'en créer un nouveau
        # → force la déconnexion de tout autre navigateur/onglet déjà connecté
        Token.objects.filter(utilisateur=utilisateur).delete()
        token = secrets.token_hex(32)  # 64 caractères hexadécimaux (256 bits d'entropie)
        Token.objects.create(utilisateur=utilisateur, cle=token)

        return JsonResponse({
            'message':     'Koneksyon reyisi via Google',
            'token':       token,
            'utilisateur': _serialiseUtilisateur(utilisateur),
        })

    except Exception as e:
        print("ERREUR google_connexion :", str(e))
        return JsonResponse({'error': str(e)}, status=500)


# ── INSCRIPTION VIA GOOGLE ────────────────────────────────────────────────────
@csrf_exempt
def google_inscription(request):
    """Inscription via Google — crée un nouveau compte depuis les infos Google."""
    if request.method != "POST":
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data         = json.loads(request.body)
        google_token = data.get('token')

        if not google_token:
            return JsonResponse({'error': 'Token Google manquant'}, status=400)

        # valider le token auprès de Google et récupérer les informations du compte
        google_response = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {google_token}'}
        )
        if google_response.status_code != 200:
            return JsonResponse({'error': 'Token Google invalide'}, status=401)

        google_data = google_response.json()
        email       = google_data.get('email')
        nom         = google_data.get('family_name',  'Inconnu')   # nom de famille Google
        prenom      = google_data.get('given_name',   'Inconnu')   # prénom Google

        if not email:
            return JsonResponse({'error': 'Email Google non disponible'}, status=400)

        # bloquer si le compte existe déjà → rediriger vers la connexion
        if Utilisateur.objects.filter(email=email).exists():
            return JsonResponse({
                'error': 'Kont sa a deja egziste. Tanpri konekte.'
            }, status=400)

        # créer l'utilisateur avec un mot de passe aléatoire (connexion uniquement via Google)
        utilisateur = Utilisateur.objects.create(
            nom          = nom,
            prenom       = prenom,
            email        = email,
            mot_de_passe = haser_password(secrets.token_hex(16)),  # mdp inaccessible à l'utilisateur
            telephone    = '',
            est_actif    = False,   # sera activé juste après
        )

        # compléter le profil créé automatiquement par le signal post_save
        profil           = utilisateur.profil
        profil.pays      = 'Haiti'
        profil.role      = data.get('role',      'acheteur')   # rôle choisi pendant l'inscription
        profil.latitude  = _coord_ou_none(data.get('latitude'))
        profil.longitude = _coord_ou_none(data.get('longitude'))
        profil.save()

        # activer l'utilisateur (est_actif = présence en ligne)
        if not utilisateur.est_actif:
            utilisateur.modifier_est_actif()

        # créer le token de session
        token = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=token)

        return JsonResponse({
            'message':     'Enskripsyon reyisi via Google',
            'token':       token,
            'utilisateur': _serialiseUtilisateur(utilisateur),
        }, status=201)

    except Exception as e:
        print("ERREUR google_inscription :", str(e))
        return JsonResponse({'error': str(e)}, status=500)


# ── INSCRIPTION CLASSIQUE ─────────────────────────────────────────────────────
@csrf_exempt
def sinscrire(request):
    """Inscription avec email + mot de passe."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    # parser le JSON du corps de la requête
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # vérifier la présence de tous les champs obligatoires
    for field in ['nom', 'prenom', 'email', 'mot_de_passe', 'telephone']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier l'unicité de l'email avant la création
    if Utilisateur.objects.filter(email=data['email']).exists():
        return JsonResponse({'error': "L'email existe déjà"}, status=400)

    try:
        # transaction atomique : si la sauvegarde du profil échoue, l'utilisateur est annulé
        with transaction.atomic():
            utilisateur = Utilisateur.objects.create(
                nom          = data['nom'],
                prenom       = data['prenom'],
                email        = data['email'],
                mot_de_passe = haser_password(data['mot_de_passe']),  # hashage avant stockage
                telephone    = data['telephone'],
                est_actif    = False,   # sera activé à la première connexion
            )

            # le signal post_save crée automatiquement le Profil lié
            profil           = utilisateur.profil
            profil.bio       = data.get('bio',       '')
            _enregistrer_photo_profil(profil, data.get('photo_profil'))  # base64 → fichier
            profil.adresse   = data.get('adresse',   '')
            profil.commune   = data.get('commune',   '')
            profil.ville     = data.get('ville',     '')
            profil.pays      = data.get('pays',      'Haiti')
            profil.role      = data.get('role',      'acheteur')
            profil.latitude  = _coord_ou_none(data.get('latitude'))
            profil.longitude = _coord_ou_none(data.get('longitude'))
            profil.save()

    except ValueError as e:
        # _enregistrer_photo_profil lève ValueError si les données base64 sont corrompues
        return JsonResponse({'error': str(e)}, status=400)

    # créer le token après la transaction pour éviter de stocker un token orphelin
    token = secrets.token_hex(32)
    Token.objects.create(utilisateur=utilisateur, cle=token)

    return JsonResponse({
        'message':     'Utilisateur inscrit avec succès',
        'token':       token,
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=201)


# ── CONNEXION CLASSIQUE ───────────────────────────────────────────────────────
@csrf_exempt
def seConnecter(request):
    """Connexion avec email + mot de passe."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # vérifier la présence des champs d'identification
    for field in ['email', 'mot_de_passe']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # récupérer l'utilisateur par son email
    try:
        utilisateur = Utilisateur.objects.get(email=data['email'])
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': "Email n'existe pas ou est incorrect"}, status=401)

    # comparer le mot de passe saisi avec le hash stocké
    if not verifier_password(data['mot_de_passe'], utilisateur.mot_de_passe):
        return JsonResponse({'error': "Le mot de passe n'existe pas ou incorrect"}, status=401)

    # marquer l'utilisateur comme en ligne
    if not utilisateur.est_actif:
        utilisateur.modifier_est_actif()

    # single-session : invalider tous les tokens précédents (connexion sur un autre navigateur)
    # → le 1er navigateur recevra 401 à sa prochaine requête et sera déconnecté automatiquement
    Token.objects.filter(utilisateur=utilisateur).delete()
    token = secrets.token_hex(32)
    Token.objects.create(utilisateur=utilisateur, cle=token)

    return JsonResponse({
        'message':     'Utilisateur connecté avec succès',
        'token':       token,
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=200)


# ── DÉCONNEXION ───────────────────────────────────────────────────────────────
@csrf_exempt
def seDeconnecter(request):
    """Déconnecte l'utilisateur : marque hors ligne + supprime le token de session."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification invalide"}, status=401)

    # basculer est_actif → False (indicateur de présence en ligne, pas de désactivation du compte)
    if utilisateur.est_actif:
        utilisateur.modifier_est_actif()

    # supprimer uniquement le token de cette session (pas tous les tokens)
    token_key = request.headers.get('Authorization', '').replace('Token ', '')
    Token.objects.filter(cle=token_key).delete()

    return JsonResponse({'message': 'Utilisateur déconnecté avec succès'}, status=200)


# ── AFFICHER LE PROFIL ────────────────────────────────────────────────────────
@csrf_exempt
def profilAfficher(request):
    """Retourne les informations de l'utilisateur connecté et son profil."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil  # accès via la relation OneToOne définie dans models.py

    return JsonResponse({
        'utilisateur': _serialiseUtilisateur(utilisateur),
        'profil':      _serialiseProfil(profil, request),  # request pour construire l'URL absolue de la photo
    }, status=200)


# ── MODIFIER LES INFORMATIONS UTILISATEUR ────────────────────────────────────
@csrf_exempt
def modifierUtilisateur(request):
    """Met à jour nom, prénom, email et/ou téléphone de l'utilisateur connecté."""
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # mise à jour partielle : seuls les champs présents dans le JSON sont modifiés
    for champ in ['nom', 'prenom', 'email', 'telephone']:
        if champ in data:
            setattr(utilisateur, champ, data[champ])

    try:
        utilisateur.save()
    except IntegrityError:
        # l'email choisi est déjà utilisé par un autre compte
        return JsonResponse({'error': "L'email existe déjà"}, status=400)

    return JsonResponse({
        'message':     'Utilisateur mis à jour avec succès',
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=200)


# ── MODIFIER LE PROFIL ────────────────────────────────────────────────────────
@csrf_exempt
def modifierProfil(request):
    """Met à jour les informations du profil (bio, adresse, photo, rôle, GPS…)."""
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # mise à jour partielle des champs texte/numériques du profil
    for champ in ['bio', 'adresse', 'commune', 'ville', 'pays', 'role', 'latitude', 'longitude']:
        if champ in data:
            valeur = data[champ]
            if champ in ('latitude', 'longitude'):
                valeur = _coord_ou_none(valeur)
            setattr(profil, champ, valeur)

    # traiter la photo uniquement si un nouveau fichier est fourni
    try:
        _enregistrer_photo_profil(profil, data.get('photo_profil'))
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    profil.save()

    return JsonResponse({
        'message': 'Profil mis à jour avec succès',
        'profil':  _serialiseProfil(profil, request),
    }, status=200)


# ── MODIFIER LE MOT DE PASSE ──────────────────────────────────────────────────
@csrf_exempt
def modifierMotDePasse(request):
    """Change le mot de passe après vérification de l'ancien."""
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    data = json.loads(request.body)

    # les deux champs sont obligatoires pour ce endpoint
    for field in ['ancien_mot_de_passe', 'nouveau_mot_de_passe']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier que l'ancien mot de passe est correct avant d'accepter le changement
    if not verifier_password(data['ancien_mot_de_passe'], utilisateur.mot_de_passe):
        return JsonResponse({'error': 'Ancien mot de passe incorrect'}, status=401)

    # hash + sauvegarde via la méthode du modèle
    utilisateur.modifier_mot_de_passe(data['nouveau_mot_de_passe'])

    return JsonResponse({'message': 'Mot de passe modifié avec succès'}, status=200)


# ── VÉRIFIER SI UNE ENTREPRISE EXISTE (sans authentification) ────────────────
@csrf_exempt
def verifierEntreprise(request):
    """Vérifie l'unicité du nom et du numéro d'enregistrement avant la création."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    # paramètres passés dans la query string (?nom_Entreprise=...&num_Enregistrement=...)
    nom_Entreprise     = request.GET.get('nom_Entreprise', '').strip()
    num_Enregistrement = request.GET.get('num_Enregistrement', '').strip()

    if not nom_Entreprise or not num_Enregistrement:
        return JsonResponse({'error': "Le nom et le numéro d'enregistrement sont requis"}, status=400)

    # trois vérifications distinctes pour retourner un message précis
    existe_combo = Entreprise.objects.filter(
        nom_Entreprise=nom_Entreprise,
        num_Enregistrement=num_Enregistrement,
    ).exists()
    existe_nom = Entreprise.objects.filter(nom_Entreprise=nom_Entreprise).exists()
    existe_num = Entreprise.objects.filter(num_Enregistrement=num_Enregistrement).exists()

    # construire un message d'erreur explicite selon le cas de conflit
    if existe_combo:
        message = "Cette entreprise existe déjà"
    elif existe_nom:
        message = "Le nom de l'entreprise existe déjà"
    elif existe_num:
        message = "Le numéro d'enregistrement existe déjà"
    else:
        message = None  # aucun conflit → l'entreprise peut être créée

    return JsonResponse({
        'existe':  bool(existe_combo or existe_nom or existe_num),
        'message': message,
    }, status=200)


# ── CRÉER UNE ENTREPRISE ──────────────────────────────────────────────────────
@csrf_exempt
def creerEntreprise(request):
    """
    Inscrit un nouveau compte entreprise, de façon autonome (pas de compte
    personnel requis au préalable) : l'entreprise possède directement son
    propre email/mot de passe et se connecte ensuite via /connexion/ comme
    n'importe quel compte. Elle est propriétaire d'elle-même.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # champs obligatoires : identité de l'entreprise + ses propres identifiants de connexion
    # (Entreprise hérite d'Utilisateur : c'est un compte à part entière sur la plateforme)
    for field in ['nom_Entreprise', 'num_Enregistrement', 'email', 'mot_de_passe', 'telephone']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifications d'unicité avant d'insérer (évite des erreurs DB moins lisibles)
    if Entreprise.objects.filter(
        nom_Entreprise=data['nom_Entreprise'],
        num_Enregistrement=data['num_Enregistrement'],
    ).exists():
        return JsonResponse({'error': "Cette entreprise existe déjà"}, status=400)

    if Entreprise.objects.filter(nom_Entreprise=data['nom_Entreprise']).exists():
        return JsonResponse({'error': "Le nom de l'entreprise existe déjà"}, status=400)

    if Entreprise.objects.filter(num_Enregistrement=data['num_Enregistrement']).exists():
        return JsonResponse({'error': "Le numéro d'enregistrement existe déjà"}, status=400)

    # l'email de connexion de l'entreprise est partagé avec la table Utilisateur (unique)
    if Utilisateur.objects.filter(email=data['email']).exists():
        return JsonResponse({'error': "L'email existe déjà"}, status=400)

    try:
        # transaction atomique : si le logo est invalide, l'entreprise n'est pas créée
        with transaction.atomic():
            entreprise = Entreprise.objects.create(
                nom                = data['nom_Entreprise'],       # pas d'info personnelle : identité = celle de l'entreprise
                prenom             = '',
                email              = data['email'],
                mot_de_passe       = haser_password(data['mot_de_passe']),  # hashage avant stockage
                telephone          = data['telephone'],            # téléphone de contact de l'entreprise
                nom_Entreprise     = data['nom_Entreprise'],
                num_Enregistrement = data['num_Enregistrement'],
                secteur            = data.get('secteur',     'agriculture'),
                description        = data.get('description', ''),
                adresse            = data.get('adresse',     ''),
                commune            = data.get('commune',     ''),
                pays               = data.get('pays',        'Haiti'),
                longitude          = _coord_ou_none(data.get('longitude')),
                latitude           = _coord_ou_none(data.get('latitude')),
            )
            entreprise.proprietaire = entreprise   # l'entreprise gère son propre compte
            _enregistrer_logo_entreprise(entreprise, data.get('logo'))  # base64 → fichier
            entreprise.save()
            # le signal post_save crée automatiquement le Profil (rôle 'acheteur' par défaut)

    except ValueError as e:
        # logo base64 corrompu ou malformé
        return JsonResponse({'error': str(e)}, status=400)

    # émet un token de session directement, comme /inscription/, pour connecter
    # l'entreprise immédiatement après son inscription
    token = secrets.token_hex(32)
    Token.objects.create(utilisateur=entreprise, cle=token)

    return JsonResponse({
        'message':     'Entreprise inscrite avec succès',
        'token':       token,
        # Entreprise hérite d'Utilisateur : ces deux vues du même compte
        # permettent au frontend de le traiter comme un compte connecté normal.
        'utilisateur': _serialiseUtilisateur(entreprise),
        'entreprise':  _serialiseEntreprise(entreprise, request),
    }, status=201)


# ── LISTER LES ENTREPRISES ────────────────────────────────────────────────────
@csrf_exempt
def listerEntreprises(request):
    """Liste les entreprises de l'utilisateur (ou toutes si rôle admin)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    # un admin voit toutes les entreprises de la plateforme, les autres voient uniquement les leurs
    if utilisateur.profil.role == 'admin':
        entreprises = Entreprise.objects.all()
    else:
        entreprises = utilisateur.entreprises.all()  # relation inverse via ForeignKey

    return JsonResponse({
        'entreprises': [_serialiseEntreprise(e, request) for e in entreprises],
    }, status=200)


# ── MODIFIER UNE ENTREPRISE ───────────────────────────────────────────────────
@csrf_exempt
def modifierEntreprise(request):
    """Met à jour une entreprise appartenant à l'utilisateur connecté."""
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': "Le champ id est requis"}, status=400)

    # scoper la recherche à l'utilisateur connecté pour empêcher la modification d'entreprises tierces
    try:
        entreprise = utilisateur.entreprises.get(id=data['id'])
    except Entreprise.DoesNotExist:
        return JsonResponse({'error': "Entreprise introuvable"}, status=404)

    # mise à jour partielle : seuls les champs présents dans le JSON sont modifiés
    for champ in ['nom_Entreprise', 'num_Enregistrement', 'secteur', 'description',
                  'email', 'telephone', 'adresse', 'commune', 'pays',
                  'longitude', 'latitude']:
        if champ in data:
            valeur = data[champ]
            if champ in ('latitude', 'longitude'):
                valeur = _coord_ou_none(valeur)
            setattr(entreprise, champ, valeur)

    # traiter le logo uniquement si un nouveau fichier est fourni
    try:
        _enregistrer_logo_entreprise(entreprise, data.get('logo'))
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    try:
        entreprise.save()
    except IntegrityError:
        # nom, numéro d'enregistrement ou email déjà utilisé par un autre compte
        return JsonResponse({
            'error': "Le nom de l'entreprise, le numéro d'enregistrement ou l'email existe déjà"
        }, status=400)

    return JsonResponse({
        'message':    'Entreprise mise à jour avec succès',
        'entreprise': _serialiseEntreprise(entreprise, request),
    }, status=200)


# ── SUPPRIMER UNE ENTREPRISE ──────────────────────────────────────────────────
@csrf_exempt
def supprimerEntreprise(request):
    """Supprime définitivement une entreprise de l'utilisateur connecté."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': "Le champ id est requis"}, status=400)

    # vérifier que l'entreprise appartient bien à l'utilisateur connecté
    try:
        entreprise = utilisateur.entreprises.get(id=data['id'])
    except Entreprise.DoesNotExist:
        return JsonResponse({'error': "Entreprise introuvable"}, status=404)

    entreprise.delete()  # CASCADE : supprime aussi les fichiers liés (logo)

    return JsonResponse({'message': 'Entreprise supprimée avec succès'}, status=200)


# ── SUPPRIMER LE LOGO D'UNE ENTREPRISE ───────────────────────────────────────
@csrf_exempt
def supprimerLogoEntreprise(request):
    """Supprime le logo d'une entreprise (fichier physique + référence en base)."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': "Le champ id est requis"}, status=400)

    try:
        entreprise = utilisateur.entreprises.get(id=data['id'])
    except Entreprise.DoesNotExist:
        return JsonResponse({'error': "Entreprise introuvable"}, status=404)

    entreprise.supprimer_logo()  # méthode du modèle : supprime fichier + met logo=None

    return JsonResponse({
        'message':    'Logo supprimé avec succès',
        'entreprise': _serialiseEntreprise(entreprise, request),
    }, status=200)


# ── SUPPRIMER LA PHOTO DE PROFIL ──────────────────────────────────────────────
@csrf_exempt
def supprimerPhotoProfil(request):
    """Supprime la photo de profil (fichier physique + référence en base)."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil
    profil.supprimer_photo_profil()  # méthode du modèle : supprime fichier + met photo_profil=None

    return JsonResponse({
        'message': 'Photo de profil supprimée avec succès',
        'profil':  _serialiseProfil(profil, request),
    }, status=200)


# ── VÉRIFICATION VENDEUR (KYC) ────────────────────────────────────────────────
@csrf_exempt
def soumettre_verification(request):
    """
    Crée ou met à jour la demande de vérification KYC de l'utilisateur connecté.
    Contrairement aux photos de profil/logos (base64 dans le JSON), les pièces
    d'identité arrivent en upload multipart classique (request.FILES) — plus
    adapté à des documents officiels (PDF possible pour le certificat de patente).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    # le type de demandeur est déduit du compte, jamais déclaré par le client :
    # même logique que Profil.obtenir_utilisateur_type()/est_entreprise —
    # Entreprise est structurelle, indépendante de ce que la requête prétend.
    est_entreprise = Entreprise.objects.filter(pk=utilisateur.id).exists()
    type_demandeur = 'entreprise' if est_entreprise else 'individuel'

    # requête multipart/form-data : les champs texte sont dans request.POST,
    # les fichiers dans request.FILES (pas de json.loads(request.body) ici)
    numero_piece_saisi = request.POST.get('numero_piece_saisi', '').strip()
    if not numero_piece_saisi:
        return JsonResponse({'error': "Le numéro de la pièce fournie est requis"}, status=400)

    if type_demandeur == 'individuel':
        type_document = request.POST.get('type_document')
        if type_document not in dict(DemandeVerification.TYPE_DOCUMENT):
            return JsonResponse({'error': 'Le champ type_document est requis et doit être valide'}, status=400)
        if 'document_recto' not in request.FILES:
            return JsonResponse({'error': 'Le document (recto) est requis'}, status=400)
        if type_document == 'cin' and 'document_verso' not in request.FILES:
            return JsonResponse({'error': "Le verso de la carte d'identité est requis"}, status=400)
        if 'selfie' not in request.FILES:
            return JsonResponse({'error': 'Le selfie est requis'}, status=400)
    else:
        if 'certificat_patente' not in request.FILES:
            return JsonResponse({'error': 'Le certificat de patente est requis'}, status=400)

    # une pièce ne peut créer qu'un seul compte : on rejette avant même de
    # lancer l'OCR si ce numéro est déjà associé à une AUTRE demande active
    # (peu importe son statut, sauf 'echoue' — un échec ne réserve pas le
    # numéro indéfiniment) — comparaison normalisée, voir _normaliser_identifiant
    numero_normalise = _normaliser_identifiant(numero_piece_saisi)
    deja_utilise = any(
        _normaliser_identifiant(autre.numero_piece_saisi) == numero_normalise
        for autre in DemandeVerification.objects
            .exclude(utilisateur=utilisateur)
            .exclude(statut='echoue')
            .exclude(numero_piece_saisi__isnull=True)
            .exclude(numero_piece_saisi='')
    )
    if deja_utilise:
        return JsonResponse({
            'error': "Ce document est déjà associé à un autre compte sur la plateforme. "
                     "Une même pièce d'identité ou un même certificat de patente ne peut servir "
                     "qu'à un seul compte vendeur."
        }, status=409)

    # DemandeVerification n'a pas ses propres champs GPS : latitude/longitude
    # confirmées pendant la vérification appartiennent au compte et mettent à
    # jour Profil (individuel) ou Entreprise (entreprise), pas la demande elle-même
    latitude  = _coord_ou_none(request.POST.get('latitude'))
    longitude = _coord_ou_none(request.POST.get('longitude'))

    with transaction.atomic():
        demande, _ = DemandeVerification.objects.get_or_create(
            utilisateur=utilisateur,
            defaults={'type_demandeur': type_demandeur},
        )
        demande.type_demandeur      = type_demandeur
        demande.numero_piece_saisi  = numero_piece_saisi
        demande.statut              = 'en_attente'   # toute nouvelle soumission relance le traitement
        demande.motif_echec         = None

        if type_demandeur == 'individuel':
            demande.type_document  = type_document
            demande.document_recto = request.FILES['document_recto']
            if type_document == 'cin':
                demande.document_verso = request.FILES['document_verso']
            demande.selfie = request.FILES['selfie']

            profil = utilisateur.profil
            profil.latitude  = latitude
            profil.longitude = longitude
            profil.save()
        else:
            demande.certificat_patente = request.FILES['certificat_patente']

            entreprise = Entreprise.objects.get(pk=utilisateur.id)
            entreprise.latitude  = latitude
            entreprise.longitude = longitude
            entreprise.save()

        demande.save()

    # étape 03 (OCR) puis, selon le type de demandeur, étape 04 (vérification
    # faciale, individuel) ou étape 05 (registre MCI, entreprise) — déclenchées
    # ici de façon synchrone (traitement attendu sous les 5 min) ; une erreur du
    # pipeline ne doit pas faire échouer la soumission elle-même.
    # Si _lancer_pipeline_ocr a déjà marqué la demande "echoue" (document
    # illisible), on n'enchaîne pas sur l'étape suivante : sinon la vérification
    # faciale/MCI pourrait écraser un échec légitime par un succès accidentel.
    try:
        _lancer_pipeline_ocr(demande)
        if demande.statut != 'echoue':
            if demande.type_demandeur == 'individuel':
                _lancer_verification_faciale(demande)
            else:
                _verifier_patente_mci(demande)
    except Exception as e:
        print("ERREUR pipeline de vérification :", str(e))
        demande.marquer_echoue(f"Erreur de traitement automatique : {e}")

    return JsonResponse({
        'message':      'Demande de vérification soumise avec succès',
        'verification': _serialiseDemandeVerification(demande, request),
    }, status=201)


# ── STATUT DE LA DEMANDE DE VÉRIFICATION ─────────────────────────────────────
@csrf_exempt
def statut_verification(request):
    """
    Retourne le statut courant (+ motif d'échec éventuel) de la demande de
    vérification de l'utilisateur connecté, pour que le frontend puisse
    l'afficher sans dépendre uniquement du WebSocket.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        demande = utilisateur.demande_verification  # relation OneToOne définie dans models.py
    except DemandeVerification.DoesNotExist:
        return JsonResponse({'error': "Aucune demande de vérification trouvée"}, status=404)

    # réutilise le même sérialiseur que soumettre_verification : le frontend a
    # besoin de contrat_pdf (étape 06) quand statut == 'verifie', pas seulement
    # de statut/motif_echec
    return JsonResponse(_serialiseDemandeVerification(demande, request), status=200)


# ── PRÉVISUALISATION DU CONTRAT (avant soumission finale) ────────────────────
@csrf_exempt
def previsualiser_contrat(request):
    """
    Génère un contrat de prévisualisation (PDF, retourné directement en
    binaire) à partir des données du formulaire — SANS jamais créer ni
    modifier de DemandeVerification — pour que l'utilisateur puisse voir à
    quoi ressemblera son contrat avant d'envoyer sa demande définitivement
    (étape 05 du wizard, voir DevenirVendeur.jsx). Accepte le même multipart
    que soumettre_verification, mais ne persiste rien.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    from .services.contrat_service import generer_apercu_contrat

    est_entreprise = Entreprise.objects.filter(pk=utilisateur.id).exists()
    type_demandeur = 'entreprise' if est_entreprise else 'individuel'
    numero_piece_saisi = request.POST.get('numero_piece_saisi', '').strip()

    if type_demandeur == 'entreprise':
        entreprise       = Entreprise.objects.get(pk=utilisateur.id)
        fichier_document = request.FILES.get('certificat_patente')
        pdf = generer_apercu_contrat(
            type_demandeur     = type_demandeur,
            nom_affiche        = entreprise.nom_Entreprise,
            type_document       = None,
            numero_piece_saisi  = numero_piece_saisi,
            fichier_identite    = entreprise.logo,
            fichier_document    = fichier_document,
            document_est_pdf    = bool(fichier_document and fichier_document.name.lower().endswith('.pdf')),
        )
    else:
        pdf = generer_apercu_contrat(
            type_demandeur     = type_demandeur,
            nom_affiche        = f"{utilisateur.prenom} {utilisateur.nom}".strip(),
            type_document       = request.POST.get('type_document'),
            numero_piece_saisi  = numero_piece_saisi,
            fichier_identite    = request.FILES.get('selfie'),
            fichier_document    = request.FILES.get('document_recto'),
        )

    return HttpResponse(pdf.read(), content_type='application/pdf')


# ── ADMIN — LISTER TOUS LES UTILISATEURS ─────────────────────────────────────
@csrf_exempt
def listerUtilisateursAdmin(request):
    """Liste tous les utilisateurs de la plateforme (accès réservé au rôle admin)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    # vérification du rôle avant d'exposer des données sensibles
    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    utilisateurs = Utilisateur.objects.all()

    return JsonResponse({
        'utilisateurs': [
            # fusionner les infos utilisateur avec le rôle du profil associé
            {**_serialiseUtilisateur(u), 'role': u.profil.role}
            for u in utilisateurs
        ],
    }, status=200)


# ── ADMIN — LISTER LES DEMANDES DE VÉRIFICATION ENTREPRISE ───────────────────
@csrf_exempt
def lister_demandes_admin(request):
    """
    Liste les demandes de vérification entreprise en attente (accès réservé au
    rôle admin), avec le lien vers le certificat de patente et le numéro extrait
    par OCR — pour que l'admin ouvre guichet.mci.ht/recherche manuellement,
    saisisse ce numéro, et compare avec le certificat avant de valider/rejeter
    (voir DemandeVerificationAdmin.valider_selectionnees/rejeter_selectionnees
    dans admin.py, qui appellent marquer_verifie()/marquer_echoue()).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    # vérification du rôle avant d'exposer des données sensibles
    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    # 'en_attente_manuelle' = la vérification MCI automatique (_verifier_patente_mci)
    # n'a pas pu conclure seule (site indisponible, ou nom trouvé mais numéro non
    # confirmé) — voir Registration/services/patente_service.py. 'en_attente' ne
    # devrait normalement jamais être observé ici (pipeline synchrone, résolu
    # avant la fin de soumettre_verification) mais reste inclus par sécurité.
    demandes = DemandeVerification.objects.filter(
        type_demandeur='entreprise',
        statut__in=['en_attente', 'en_attente_manuelle'],
    ).select_related('utilisateur')

    return JsonResponse({
        'demandes': [
            {
                'id':                     d.id,
                'utilisateur_id':         d.utilisateur_id,
                'email':                  d.utilisateur.email,
                'statut':                 d.statut,
                'numero_patente_extrait': d.numero_patente_extrait,
                'certificat_patente':     request.build_absolute_uri(d.certificat_patente.url) if d.certificat_patente else None,
                'verification_mci':       (d.donnees_ocr_brutes or {}).get('verification_mci'),
                'date_soumission':        d.date_soumission.isoformat(),
            }
            for d in demandes
        ],
    }, status=200)


# ── DEMANDER UN CODE PIN DE RÉINITIALISATION ──────────────────────────────────
@csrf_exempt
def demanderReinitialisation(request):
    """Génère un code PIN à 4 chiffres et l'envoie par email (valable 15 min)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    email = data.get('email', '').strip()
    if not email:
        return JsonResponse({'error': 'Le champ email est requis'}, status=400)

    try:
        utilisateur = Utilisateur.objects.get(email=email)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Aucun compte associé à cet email'}, status=404)

    try:
        # supprimer les anciens codes non utilisés pour éviter l'accumulation en base
        CodeReinitialisation.objects.filter(utilisateur=utilisateur, utilise=False).delete()

        # générer un code PIN à 4 chiffres avec zéro de remplissage (ex: "0042")
        code            = f"{random.randint(0, 9999):04d}"
        date_expiration = timezone.now() + timedelta(minutes=15)  # expire dans 15 minutes

        CodeReinitialisation.objects.create(
            utilisateur     = utilisateur,
            code            = code,
            date_expiration = date_expiration,
        )

        # envoyer le code par email via le backend SMTP configuré dans settings.py
        send_mail(
            subject        = 'Réinitialisation de mot de passe — RekoltHt',
            message        = (
                f"Bonjour {utilisateur.prenom},\n\n"
                f"Votre code de réinitialisation est : {code}\n\n"
                f"Ce code est valable pendant 15 minutes.\n\n"
                f"Si vous n'avez pas demandé cette réinitialisation, ignorez cet email.\n\n"
                f"L'équipe RekoltHt"
            ),
            from_email     = settings.DEFAULT_FROM_EMAIL,
            recipient_list = [email],
            fail_silently  = False,  # lève une exception si le serveur SMTP est injoignable
        )

    except Exception as e:
        print("ERREUR demanderReinitialisation :", str(e))
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'message': 'Code de réinitialisation envoyé par email'}, status=200)


# ── VÉRIFIER LE CODE PIN (sans changer le mot de passe) ──────────────────────
@csrf_exempt
def verifierCodeReinitialisation(request):
    """Valide le code PIN reçu par email sans encore réinitialiser le mot de passe."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    for field in ['email', 'code']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    email = data['email'].strip()
    code  = data['code'].strip()

    try:
        utilisateur = Utilisateur.objects.get(email=email)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Aucun compte associé à cet email'}, status=404)

    # récupérer le code le plus récent non utilisé pour cet email
    try:
        code_obj = CodeReinitialisation.objects.filter(
            utilisateur = utilisateur,
            code        = code,
            utilise     = False,
        ).latest('date_creation')
    except CodeReinitialisation.DoesNotExist:
        return JsonResponse({'error': 'Code invalide'}, status=400)

    # vérifier que le code n'a pas expiré (date_expiration > maintenant)
    if not code_obj.est_valide():
        return JsonResponse({'error': 'Code expiré ou déjà utilisé'}, status=400)

    return JsonResponse({'message': 'Code valide'}, status=200)


# ── RÉINITIALISER LE MOT DE PASSE VIA CODE PIN ───────────────────────────────
@csrf_exempt
def reinitialiserMotDePasse(request):
    """Réinitialise le mot de passe après validation du code PIN (protégé contre les races conditions)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    for field in ['email', 'code', 'nouveau_mot_de_passe']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    email                = data['email'].strip()
    code                 = data['code'].strip()
    nouveau_mot_de_passe = data['nouveau_mot_de_passe']

    try:
        utilisateur = Utilisateur.objects.get(email=email)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Aucun compte associé à cet email'}, status=404)

    try:
        with transaction.atomic():
            # select_for_update() pose un verrou sur la ligne pour empêcher deux requêtes
            # simultanées d'utiliser le même code (race condition sur double-soumission du formulaire)
            code_obj = CodeReinitialisation.objects.select_for_update().filter(
                utilisateur = utilisateur,
                code        = code,
                utilise     = False,
            ).latest('date_creation')

            if not code_obj.est_valide():
                return JsonResponse({'error': 'Code expiré ou déjà utilisé'}, status=400)

            # marquer le code comme utilisé avant de changer le mot de passe
            code_obj.utilise = True
            code_obj.save()

            # hash + sauvegarde via la méthode du modèle
            utilisateur.modifier_mot_de_passe(nouveau_mot_de_passe)

    except CodeReinitialisation.DoesNotExist:
        return JsonResponse({'error': 'Code invalide'}, status=400)
    except Exception as e:
        print("ERREUR reinitialiserMotDePasse :", str(e))
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'message': 'Mot de passe réinitialisé avec succès'}, status=200)


# ── FONCTIONS UTILITAIRES PRIVÉES ─────────────────────────────────────────────

def _normaliser_identifiant(valeur):
    """
    Normalise un identifiant de pièce (numéro) ou un nom pour comparaison :
    accents, espaces, tirets et casse ignorés — l'OCR ne restitue jamais un
    champ à l'identique de la saisie utilisateur (ex: "JEANWOOBENS" vs
    "Jean Woobens", "0083904936" vs "008-390-493-6", constaté en conditions
    réelles), donc une comparaison stricte produirait des faux rejets massifs.
    """
    if not valeur:
        return ''
    sans_accents = ''.join(c for c in unicodedata.normalize('NFD', valeur) if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Z0-9]', '', sans_accents.upper())


def _coord_ou_none(valeur):
    """
    Convertit une coordonnée GPS reçue du frontend en float, ou None si absente.
    Le frontend envoie parfois une chaîne vide ('') quand la géolocalisation a
    échoué/été refusée — un FloatField Django rejette '' (attend un nombre ou None).
    """
    if valeur in (None, ''):
        return None
    return valeur


def _get_user_from_token(request):
    """
    Résout le token du header Authorization: Token <cle> en un objet Utilisateur.
    Retourne None si le header est absent ou si le token n'existe pas en base.
    """
    auth = request.headers.get('Authorization', '')
    if not auth:
        return None

    token_key = auth.replace('Token ', '')  # extraire la clé après le préfixe "Token "

    try:
        # select_related évite une requête SQL supplémentaire pour charger l'utilisateur
        token = Token.objects.select_related('utilisateur').get(cle=token_key)
        return token.utilisateur
    except Token.DoesNotExist:
        # token inconnu ou révoqué (single-session → ancien token supprimé)
        return None


def _enregistrer_photo_profil(profil, photo_data):
    """
    Décode une image encodée en base64 et la sauvegarde comme photo de profil.
    photo_data doit être un dict avec les clés 'content' (base64) et 'filename'.
    Lève ValueError si les données sont corrompues ou mal formées.
    """
    if not photo_data:
        return  # aucune photo fournie → on ne touche pas la photo existante

    try:
        contenu = photo_data['content']

        # retirer le préfixe data URL si présent (ex: "data:image/png;base64,iVBOR...")
        if contenu.startswith('data:'):
            contenu = contenu.split(',', 1)[1]

        fichier_binaire = base64.b64decode(contenu)  # décoder le base64 en octets
        # save=False : ne sauvegarde pas encore l'objet, permet de grouper les sauvegardes
        profil.photo_profil.save(photo_data['filename'], ContentFile(fichier_binaire), save=False)

    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Photo de profil invalide : {e}")


def _enregistrer_logo_entreprise(entreprise, logo_data):
    """
    Décode un logo encodé en base64 et le sauvegarde sur l'entreprise.
    logo_data doit être un dict avec les clés 'content' (base64) et 'filename'.
    Lève ValueError si les données sont corrompues ou mal formées.
    """
    if not logo_data:
        return  # aucun logo fourni → on ne touche pas le logo existant

    try:
        contenu = logo_data['content']

        # retirer le préfixe data URL si présent
        if contenu.startswith('data:'):
            contenu = contenu.split(',', 1)[1]

        fichier_binaire = base64.b64decode(contenu)
        entreprise.logo.save(logo_data['filename'], ContentFile(fichier_binaire), save=False)

    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Logo invalide : {e}")


def _lancer_pipeline_ocr(demande):
    """
    Étape 02 : extraction OCR des informations depuis les documents fournis,
    juste après leur sauvegarde sur disque (PaddleOCR a besoin d'un chemin
    réel, pas du fichier en mémoire) — document_recto pour un individuel,
    certificat_patente pour une entreprise (mode générique).
    """
    if demande.type_demandeur == 'individuel':
        infos = extraire_infos_piece(demande.document_recto.path, demande.type_document)
        print("── OCR pièce d'identité (individuel) ──")
        for cle, valeur in infos.items():
            print(f"  {cle} : {valeur}")

        # permis de conduire : seul le NIF est vérifié (décision produit) —
        # nom/prénom/date de naissance ne sont ni exigés ni cross-vérifiés,
        # contrairement au passeport et à la CIN
        if demande.type_document == 'permis':
            champs_obligatoires = (infos['numero_piece'],)
        else:
            champs_obligatoires = (infos['nom'], infos['prenom'], infos['numero_piece'], infos['date_naissance'])
        if infos['confiance'] < SEUIL_CONFIANCE_MINIMUM or not all(champs_obligatoires):
            # document illisible (ou champ clé manquant) : on ne laisse pas de
            # champs vides silencieusement, voir consigne de marquer_echoue
            demande.marquer_echoue("Document illisible, merci de reprendre une photo plus nette")
            return

        demande.nom_extrait             = infos['nom']
        demande.prenom_extrait          = infos['prenom']
        demande.numero_piece_extrait    = infos['numero_piece']
        demande.date_naissance_extraite = parser_date_naissance(infos['date_naissance'])
        demande.donnees_ocr_brutes      = infos
        demande.save()

        # le nom/prénom du compte doit correspondre à la pièce fournie — sinon
        # n'importe qui pourrait soumettre le document d'identité d'un tiers.
        # Exception permis : seul le NIF est vérifié (voir plus haut), la
        # correspondance d'identité reste couverte par la vérification faciale
        utilisateur = demande.utilisateur
        if demande.type_document != 'permis' and (
                _normaliser_identifiant(infos['nom']) != _normaliser_identifiant(utilisateur.nom)
                or _normaliser_identifiant(infos['prenom']) != _normaliser_identifiant(utilisateur.prenom)):
            demande.marquer_echoue(
                "Le nom et prénom de votre compte ne correspondent pas à ceux figurant sur le "
                "document fourni. Vérifiez vos informations de compte ou le document soumis."
            )
            return

        # le numéro saisi par l'utilisateur doit être celui réellement lu sur le document
        if _normaliser_identifiant(demande.numero_piece_saisi) != _normaliser_identifiant(infos['numero_piece']):
            demande.marquer_echoue(
                "Le numéro de pièce saisi ne correspond pas à celui figurant sur le document "
                "fourni. Vérifiez le numéro saisi et réessayez."
            )
            return

    else:  # entreprise
        infos = extraire_infos_piece(demande.certificat_patente.path, type_document=None)
        print("── OCR certificat de patente (entreprise) ──")
        for cle, valeur in infos.items():
            print(f"  {cle} : {valeur}")

        if infos['confiance'] < SEUIL_CONFIANCE_MINIMUM:
            demande.marquer_echoue("Document illisible, merci de reprendre une photo plus nette")
            return

        demande.numero_patente_extrait = infos['numero_piece']
        demande.donnees_ocr_brutes     = infos
        demande.save()

        # le nom de l'entreprise enregistrée doit correspondre au certificat —
        # uniquement si "Délivré à" a bien été lu (label moins garanti que le
        # numéro de patente lui-même, voir extraire_infos_piece)
        entreprise = Entreprise.objects.get(pk=demande.utilisateur_id)
        if infos['nom_entreprise'] and _normaliser_identifiant(infos['nom_entreprise']) != _normaliser_identifiant(entreprise.nom_Entreprise):
            demande.marquer_echoue(
                "Le nom de l'entreprise enregistrée sur la plateforme ne correspond pas à celui "
                "figurant sur le certificat de patente fourni."
            )
            return

        # contrairement au nom, le numéro de patente saisi doit toujours être
        # confirmable sur le document — s'il n'a pas pu être lu du tout, c'est
        # un problème de lisibilité, pas une non-concordance
        numero_extrait_normalise = _normaliser_identifiant(infos['numero_piece'])
        if not numero_extrait_normalise:
            demande.marquer_echoue(
                "Le numéro de patente n'a pas pu être lu sur le certificat fourni. "
                "Merci de reprendre une photo plus nette."
            )
            return
        if _normaliser_identifiant(demande.numero_piece_saisi) != numero_extrait_normalise:
            demande.marquer_echoue(
                "Le numéro de patente saisi ne correspond pas à celui figurant sur le "
                "certificat fourni. Vérifiez le numéro saisi et réessayez."
            )
            return


def _lancer_verification_faciale(demande):
    """
    Étape 04 (individuel uniquement) : compare selfie et document_recto via
    DeepFace, exécuté dans un environnement Python dédié (voir
    Registration/services/face_service.py — incompatible en environnement
    partagé avec paddleocr/paddlepaddle, déjà utilisés par le pipeline OCR).
    Un match valide déclenche marquer_verifie() — c'est la dernière étape
    automatique du flux individuel, contrairement au flux entreprise qui
    passe par _verifier_patente_mci puis, en repli, une revue manuelle admin
    (lister_demandes_admin).
    """
    from .services.face_service import comparer_visages, VerificationFacialeIndisponible

    try:
        resultat = comparer_visages(demande.selfie.path, demande.document_recto.path)
    except VerificationFacialeIndisponible as e:
        # aucune revue manuelle : le pipeline doit toujours conclure vérifié/échoué
        print("Vérification faciale indisponible :", str(e))
        demande.marquer_echoue(f"La vérification faciale automatique n'a pas pu être effectuée ({e}). Merci de réessayer plus tard.")
        return

    demande.score_correspondance_visage = resultat['score_confiance']
    demande.save()

    # decision basee sur le "verified" natif de DeepFace (seuil calibre par
    # modele, voir face_service.py) — score_confiance est stocke pour
    # audit/debogage mais ne conditionne plus le resultat (voir face_service.py)
    if not resultat['correspond']:
        # resultat['erreur'] est renseigné quand DeepFace n'a détecté aucun
        # visage dans une des deux images (face_worker.py) — un motif bien
        # plus actionnable pour l'utilisateur que le générique "ne correspond
        # pas", et qui évite de faire croire à tort à une usurpation d'identité
        if resultat.get('erreur'):
            demande.marquer_echoue(
                "Aucun visage détecté sur l'une des deux photos (selfie ou pièce d'identité) — "
                "merci de reprendre une photo plus nette et bien éclairée."
            )
        else:
            demande.marquer_echoue("Le selfie ne correspond pas à la photo de la pièce d'identité, merci de réessayer")
        return

    demande.marquer_verifie()


def _verifier_patente_mci(demande):
    """
    Étape 05 (entreprise uniquement) : croise l'entreprise avec le registre
    public du Ministère du Commerce et de l'Industrie (Registration/services/
    patente_service.py — guichet.mci.ht/recherche). Vérifié empiriquement
    avant d'écrire ce module : le site n'offre qu'une recherche PAR NOM
    ("recherche d'antériorité"), pas par numéro — on cherche donc par
    nom_Entreprise, et on croise le numéro trouvé avec numero_patente_extrait
    (OCR, étape 03) ou, à défaut, num_Enregistrement (saisi à la création du
    compte) quand il est disponible.

    Trois issues possibles :
      - nom introuvable sur le registre               → échoué
      - nom ET numéro correspondent                   → vérifié
      - nom trouvé mais numéro absent/non concordant   → revue manuelle
        (le format de numero_patente_extrait n'est pas garanti, voir
        _lancer_pipeline_ocr — un nom seul ne suffit pas à auto-valider,
        vu que la recherche est floue, voir patente_service.py)

    Si le site est indisponible, la demande reste 'en_attente_manuelle' pour
    une revue manuelle par un admin plutôt que d'échouer à tort une
    entreprise légitime à cause d'un aléa tiers.
    """
    entreprise = Entreprise.objects.get(pk=demande.utilisateur_id)
    numero_attendu = demande.numero_patente_extrait or entreprise.num_Enregistrement

    from .services.patente_service import verifier_patente, PatenteIndisponible

    try:
        resultat = verifier_patente(entreprise.nom_Entreprise, numero_attendu=numero_attendu)
    except PatenteIndisponible as e:
        # aucune revue manuelle : le pipeline doit toujours conclure vérifié/échoué
        print("guichet.mci.ht indisponible :", str(e))
        demande.marquer_echoue(f"La vérification du registre du Ministère du Commerce et de l'Industrie n'a pas pu être effectuée ({e}). Merci de réessayer plus tard.")
        return

    demande.donnees_ocr_brutes = {**(demande.donnees_ocr_brutes or {}), 'verification_mci': resultat}

    if not resultat['trouve']:
        demande.marquer_echoue("Aucune entreprise correspondante trouvée sur le registre du Ministère du Commerce et de l'Industrie")
    elif resultat['numero_concorde'] is True:
        demande.marquer_verifie()
    else:
        # nom trouvé mais numéro absent/non concordant : le format de
        # numero_patente_extrait n'est pas garanti (voir _lancer_pipeline_ocr),
        # mais le pipeline doit tout de même conclure — pas de revue manuelle
        demande.marquer_echoue(
            "Le nom de l'entreprise a été trouvé sur le registre du Ministère du Commerce "
            "et de l'Industrie, mais le numéro de patente n'a pas pu être confirmé. "
            "Vérifiez le numéro sur votre certificat et soumettez à nouveau."
        )


# ── SÉRIALISEURS ──────────────────────────────────────────────────────────────

def _serialiseUtilisateur(utilisateur):
    """Convertit un objet Utilisateur en dict sérialisable en JSON."""
    return {
        'id':               utilisateur.id,
        'nom':              utilisateur.nom,
        'prenom':           utilisateur.prenom,
        'email':            utilisateur.email,
        'telephone':        utilisateur.telephone,
        'est_actif':        utilisateur.est_actif,        # True = en ligne, False = hors ligne
        'date_inscription': utilisateur.date_inscription.isoformat(),  # format ISO 8601
    }


def _serialiseProfil(profil, request=None):
    """
    Convertit un objet Profil en dict sérialisable en JSON.
    Si request est fourni, l'URL de la photo est construite en URL absolue
    (ex: http://localhost:8000/media/photos_profil/image.jpg).
    """
    photo_url = None
    if profil.photo_profil:
        photo_url = profil.photo_profil.url
        if request is not None:
            # build_absolute_uri ajoute le schéma + l'hôte à l'URL relative
            photo_url = request.build_absolute_uri(photo_url)

    return {
        'id':             profil.id,
        'bio':            profil.bio,
        'photo_profil':   photo_url,
        'adresse':        profil.adresse,
        'commune':        profil.commune,
        'ville':          profil.ville,
        'pays':           profil.pays,
        'longitude':      profil.longitude,
        'latitude':       profil.latitude,
        'date_maj':       profil.date_maj.isoformat(),
        'role':           profil.role,  # 'acheteur' | 'vendeur' | 'admin' (n'est JAMAIS 'entreprise', voir est_entreprise)
        # Entreprise est structurelle et indépendante du rôle (voir Profil.obtenir_utilisateur_type
        # dans models.py) : c'est le champ à utiliser côté frontend pour détecter un compte entreprise,
        # plutôt que de comparer role à une valeur qui n'existe pas dans Profil.ROLES.
        'est_entreprise': isinstance(profil.obtenir_utilisateur_type(), Entreprise),
    }


def _serialiseEntreprise(entreprise, request=None):
    """
    Convertit un objet Entreprise en dict sérialisable en JSON.
    Si request est fourni, l'URL du logo est construite en URL absolue.
    """
    logo_url = None
    if entreprise.logo:
        logo_url = entreprise.logo.url
        if request is not None:
            logo_url = request.build_absolute_uri(logo_url)

    return {
        'id':                  entreprise.id,
        'proprietaire_id':     entreprise.proprietaire_id,   # clé étrangère → id de l'Utilisateur
        'nom_Entreprise':      entreprise.nom_Entreprise,
        'num_Enregistrement':  entreprise.num_Enregistrement,
        'secteur':             entreprise.secteur,
        'description':         entreprise.description,
        'email':               entreprise.email,
        'telephone':           entreprise.telephone,
        'adresse':             entreprise.adresse,
        'commune':             entreprise.commune,
        'pays':                entreprise.pays,
        'logo':                logo_url,
        'longitude':           entreprise.longitude,
        'latitude':            entreprise.latitude,
        'est_verifiee':        entreprise.est_verifiee,           # validation manuelle par un admin
        'statut_verification': entreprise.statut_verification,   # 'en attente' | 'valide' | 'rejete'
        'date_creation':       entreprise.date_creation.isoformat(),
        'date_maj':            entreprise.date_maj.isoformat(),
    }


def _serialiseDemandeVerification(demande, request=None):
    """
    Convertit une DemandeVerification en dict sérialisable en JSON.
    Si request est fourni, les URLs des fichiers sont construites en URL absolue.
    """
    def _url(fichier):
        if not fichier:
            return None
        return request.build_absolute_uri(fichier.url) if request is not None else fichier.url

    return {
        'id':                 demande.id,
        'type_demandeur':     demande.type_demandeur,
        'type_document':      demande.type_document,
        'document_recto':     _url(demande.document_recto),
        'document_verso':     _url(demande.document_verso),
        'selfie':             _url(demande.selfie),
        'certificat_patente': _url(demande.certificat_patente),
        'contrat_pdf':        _url(demande.contrat_pdf),
        'statut':             demande.statut,
        'motif_echec':        demande.motif_echec,
        'date_soumission':    demande.date_soumission.isoformat(),
        'date_traitement':    demande.date_traitement.isoformat() if demande.date_traitement else None,
    }
