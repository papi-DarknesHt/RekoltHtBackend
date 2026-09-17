import json

from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from Registration.models import verifier_droit_admin, enregistrer_audit
from ..models import Produits, SignalementProduit, SignalementVendeur, AvisProduit, SignalementAvis
from ._auth import _get_user_from_token
from .produitsViews import _nomVendeur

# au-delà de ce nombre de signalements pour le même motif (type_probleme), le
# compte du vendeur est automatiquement suspendu — voir signalerVendeur
SEUIL_SIGNALEMENTS_VENDEUR = 5


def _serialiseSignalement(signalement):
    return {
        'id':                signalement.id,
        'produit_id':        signalement.produit_id,
        'produit_nom':       signalement.produit.nom,
        'vendeur_id':        signalement.produit.vendeur_id,
        'vendeur_nom':       _nomVendeur(signalement.produit.vendeur),
        'signaleur_id':      signalement.signaleur_id,
        'signaleur_nom':     _nomVendeur(signalement.signaleur) if signalement.signaleur_id else None,
        'type_probleme':     signalement.type_probleme,
        'motif':             signalement.motif,
        'date_signalement':  signalement.date_signalement.isoformat(),
        'admin_traitant_id':  signalement.admin_traitant_id,
        'admin_traitant_nom': _nomVendeur(signalement.admin_traitant) if signalement.admin_traitant_id else None,
        # signalements = endpoints 100% réservés aux admins (aucun vendeur ne
        # les consulte jamais, contrairement à MessageSupport côté vendeur) —
        # pas de masquage nécessaire, l'email est toujours utile "entre admins"
        'admin_traitant_email': signalement.admin_traitant.email if signalement.admin_traitant_id else None,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
        'explication_decision': signalement.explication_decision,
        # True si CE signalement est celui qui vient de déclencher la
        # désactivation automatique au seuil (voir signalerProduit) — permet
        # au frontend d'afficher un bandeau explicatif distinct plutôt qu'une
        # carte de signalement normale
        'a_declenche_desactivation_auto': signalement.produit.desactive_par_signalements,
    }


# ── SIGNALER UN PRODUIT (acheteur ou vendeur connecté) ────────────────────────
@csrf_exempt
def signalerProduit(request):
    """
    Signale un produit incorrect/obsolète directement aux administrateurs —
    jamais au vendeur concerné. Réservé aux comptes connectés (acheteur,
    vendeur ou admin) : contrairement à contacterProduit, un signalement
    engage la responsabilité du signaleur, pas d'anonymat ici.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    produit_id = data.get('produit_id')
    if not produit_id:
        return JsonResponse({'error': 'Le champ produit_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'produit_id'}}, status=400)

    type_probleme = data.get('type_probleme')
    if type_probleme not in dict(SignalementProduit.TYPE_PROBLEME):
        return JsonResponse({'error': 'Le champ type_probleme est requis et doit être valide'}, status=400)

    motif = (data.get('motif') or '').strip()
    if not motif:
        return JsonResponse({'error': 'Le motif est requis'}, status=400)

    try:
        produit = Produits.objects.get(id=produit_id)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    if produit.vendeur_id == utilisateur.id:
        return JsonResponse({'error': 'Vous ne pouvez pas signaler votre propre produit'}, status=400)

    signalement = SignalementProduit.objects.create(
        produit=produit, signaleur=utilisateur, type_probleme=type_probleme, motif=motif,
    )

    # désactivation automatique du produit au 5e signalement (tous statuts
    # confondus, traités ou non) — seul un admin peut ensuite le réactiver
    # (voir reactiverProduitAdmin, Produits/views/produitsViews.py)
    if not produit.desactive_par_signalements and SignalementProduit.objects.filter(produit=produit).count() >= 5:
        produit.est_disponible = False
        produit.desactive_par_signalements = True
        produit.save(update_fields=['est_disponible', 'desactive_par_signalements'])

        # marque tous les signalements PRÉCÉDENTS de ce produit comme résolus
        # automatiquement — exclus de la file d'attente admin (voir
        # listerSignalementsAdmin plus bas), seul celui qui vient de
        # déclencher le seuil (créé juste au-dessus) reste visible, comme
        # entrée explicative unique plutôt que 5 doublons
        SignalementProduit.objects.filter(produit=produit).exclude(id=signalement.id).update(
            resolu_automatiquement=True
        )

    from Api.broadcast import broadcast_to_admins
    broadcast_to_admins('signalement.created', _serialiseSignalement(signalement))

    return JsonResponse({
        'message': 'Signalement envoyé aux administrateurs',
        'signalement': _serialiseSignalement(signalement),
    }, status=201)


# ── SIGNALEMENTS EN ATTENTE (admin) ───────────────────────────────────────────
@csrf_exempt
def listerSignalementsAdmin(request):
    """Signalements pas encore traités, tous confondus — file partagée entre
    admins, même principe que listerMessagesAdminEnAttente (Messagerie/views.py)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementProduit.objects.filter(
        admin_traitant__isnull=True, resolu_automatiquement=False
    ).select_related('produit', 'produit__vendeur', 'signaleur')

    return JsonResponse({
        'signalements': [_serialiseSignalement(s) for s in signalements],
    }, status=200)


# ── SIGNALEMENTS DE PRODUITS DÉJÀ TRAITÉS (admin) ─────────────────────────────
@csrf_exempt
def listerSignalementsTraites(request):
    """Historique des signalements de produits déjà traités — MOI SEUL par
    défaut (un admin gestion_signalements à droits limités ne voit que ses
    propres décisions), TOUT LE MONDE pour un compte "Tous les droits" ou le
    propriétaire (voir verifier_droit_admin(utilisateur, 'super_admin')).
    Exclut aussi ce que MOI j'ai retiré de MON historique
    (historique_masque_pour, SignalementProduit) — chaque admin a son propre
    historique."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementProduit.objects.filter(admin_traitant__isnull=False).exclude(historique_masque_pour=utilisateur)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)
    signalements = signalements.select_related('produit', 'produit__vendeur', 'signaleur', 'admin_traitant').order_by('-date_traitement')

    return JsonResponse({
        'signalements': [_serialiseSignalement(s) for s in signalements],
    }, status=200)


# ── RETIRER DE MON HISTORIQUE DES SIGNALEMENTS DE PRODUITS (admin connecté) ──
@csrf_exempt
def supprimerHistoriqueSignalements(request):
    """
    Retire une ou plusieurs entrées de l'historique des signalements de
    produits — MAIS SEULEMENT de la vue de l'admin qui clique
    (historique_masque_pour) : jamais un vrai delete(), chaque admin a son
    propre historique (demande explicite) — voir Messagerie/views.py::
    supprimerHistoriqueMessagesSupport, même principe. Portée de sélection =
    ce que l'admin peut déjà voir dans son historique (moi seul, sauf "Tous
    les droits"/propriétaire).
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    signalements = SignalementProduit.objects.filter(id__in=ids, admin_traitant__isnull=False)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)

    nombre_masque = 0
    for signalement in signalements:
        signalement.historique_masque_pour.add(utilisateur)
        nombre_masque += 1

    enregistrer_audit(utilisateur, 'signalement_produit.masquer_historique', f"A retiré {nombre_masque} entrée(s) de son historique des signalements de produits")

    return JsonResponse({'nombre_supprime': nombre_masque}, status=200)


# ── TRAITER UN OU PLUSIEURS SIGNALEMENTS (admin) ──────────────────────────────
@csrf_exempt
def traiterSignalement(request):
    """
    Marque un ou plusieurs signalements du MÊME produit comme traités en un
    seul geste (regroupement par cible côté frontend, voir
    AdminDashboard.jsx::regrouperParCible) — ne bloque pas le compte du
    vendeur automatiquement : c'est à l'admin de décider séparément via
    toggleBloquerUtilisateur (Registration/views.py) s'il y a lieu. Une
    explication (`explication`) est désormais obligatoire, demandée côté
    frontend avant toute décision (voir RaisonModal.jsx) et conservée dans
    explication_decision, l'historique et le rapport PDF d'audit
    (genererRapportSignalements). Même prise en charge atomique "premier
    arrivé premier servi" que repondreMessageAdmin (Messagerie/views.py),
    signalement par signalement (un id déjà pris par un autre admin est
    simplement ignoré, pas bloquant pour le reste du groupe).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    explication = (data.get('explication') or '').strip()
    if not explication:
        return JsonResponse({'error': "Le champ explication est requis", 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'explication'}}, status=400)

    signalements_cibles = SignalementProduit.objects.filter(id__in=ids)
    if not signalements_cibles.exists():
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    ids_deja_traites = list(signalements_cibles.filter(admin_traitant__isnull=False).values_list('id', flat=True))

    nombre_maj = SignalementProduit.objects.filter(id__in=ids, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now(), explication_decision=explication
    )
    if nombre_maj == 0:
        return JsonResponse({'error': 'Ce ou ces signalements ont déjà été traités'}, status=409)

    signalements_traites = list(
        SignalementProduit.objects.filter(id__in=ids, admin_traitant=utilisateur).select_related('produit', 'produit__vendeur', 'signaleur')
    )

    from Api.broadcast import broadcast_to_admins
    for signalement in signalements_traites:
        # retire chaque signalement de la file des AUTRES admins
        broadcast_to_admins('signalement.traite', {'id': signalement.id})

    noms_produits = ", ".join(sorted({s.produit.nom for s in signalements_traites}))
    enregistrer_audit(
        utilisateur, 'signalement_produit.traiter',
        f"A traité {nombre_maj} signalement(s) du produit « {noms_produits} » (ids {ids}) — Raison : {explication}"
    )

    return JsonResponse({
        'signalements': [_serialiseSignalement(s) for s in signalements_traites],
        'ids_deja_traites': ids_deja_traites,
    }, status=200)


def _serialiseSignalementVendeur(signalement):
    return {
        'id':                signalement.id,
        'vendeur_id':        signalement.vendeur_id,
        'vendeur_nom':       _nomVendeur(signalement.vendeur),
        'signaleur_id':      signalement.signaleur_id,
        'signaleur_nom':     _nomVendeur(signalement.signaleur) if signalement.signaleur_id else None,
        'type_probleme':     signalement.type_probleme,
        'motif':             signalement.motif,
        'date_signalement':  signalement.date_signalement.isoformat(),
        'admin_traitant_id':  signalement.admin_traitant_id,
        'admin_traitant_nom': _nomVendeur(signalement.admin_traitant) if signalement.admin_traitant_id else None,
        'admin_traitant_email': signalement.admin_traitant.email if signalement.admin_traitant_id else None,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
        'explication_decision': signalement.explication_decision,
        # True si CE signalement est celui qui vient de déclencher la
        # suspension automatique au seuil (voir signalerVendeur) — permet au
        # frontend d'afficher un bandeau explicatif distinct
        'a_declenche_suspension_auto': signalement.vendeur.desactive_par_signalements,
    }


# ── SIGNALER UN VENDEUR (acheteur ou autre vendeur connecté) ──────────────────
@csrf_exempt
def signalerVendeur(request):
    """
    Signale un vendeur (comportement, fiabilité...) directement aux
    administrateurs — jamais au vendeur concerné. Réservé aux comptes
    connectés, même logique que signalerProduit ci-dessus. Au-delà de
    SEUIL_SIGNALEMENTS_VENDEUR signalements pour le même motif, le compte est
    automatiquement suspendu : plus de nouveau produit possible, et tous ses
    produits existants (non déjà bannis individuellement) passent indisponibles
    — voir Utilisateur.desactive_par_signalements (Registration/models.py).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    vendeur_id = data.get('vendeur_id')
    if not vendeur_id:
        return JsonResponse({'error': 'Le champ vendeur_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'vendeur_id'}}, status=400)

    type_probleme = data.get('type_probleme')
    if type_probleme not in dict(SignalementVendeur.TYPE_PROBLEME):
        return JsonResponse({'error': 'Le champ type_probleme est requis et doit être valide'}, status=400)

    motif = (data.get('motif') or '').strip()
    if not motif:
        return JsonResponse({'error': 'Le motif est requis'}, status=400)

    from Registration.models import Utilisateur

    try:
        vendeur = Utilisateur.objects.get(id=vendeur_id)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Vendeur introuvable', 'error_code': 'SELLER_NOT_FOUND'}, status=404)

    if vendeur.id == utilisateur.id:
        return JsonResponse({'error': 'Vous ne pouvez pas signaler votre propre compte'}, status=400)

    signalement = SignalementVendeur.objects.create(
        vendeur=vendeur, signaleur=utilisateur, type_probleme=type_probleme, motif=motif,
    )

    # suspension automatique du compte au-delà de SEUIL_SIGNALEMENTS_VENDEUR
    # signalements pour le MÊME motif (contrairement à signalerProduit, qui
    # compte tous motifs confondus) — seul un admin peut ensuite le réactiver
    # (voir reactiverVendeurAdmin, Registration/views.py)
    if not vendeur.desactive_par_signalements:
        nombre_meme_motif = SignalementVendeur.objects.filter(vendeur=vendeur, type_probleme=type_probleme).count()
        if nombre_meme_motif > SEUIL_SIGNALEMENTS_VENDEUR:
            vendeur.desactive_par_signalements = True
            vendeur.save(update_fields=['desactive_par_signalements'])

            # produits sauvegardés un par un (pas de bulk .update()) pour que
            # chacun déclenche normalement broadcast_produit (Produits/signals.py)
            # et reste ainsi cohérent en temps réel sur les catalogues déjà
            # affichés — les produits déjà bannis individuellement (5+
            # signalements sur le produit lui-même) restent inchangés, c'est un
            # blocage distinct que seule reactiverProduitAdmin peut lever
            for produit in Produits.objects.filter(vendeur=vendeur, desactive_par_signalements=False, est_disponible=True):
                produit.est_disponible = False
                produit.save(update_fields=['est_disponible'])

            # marque tous les signalements PRÉCÉDENTS (même vendeur + même
            # motif) comme résolus automatiquement — exclus de la file
            # d'attente admin (voir listerSignalementsVendeursAdmin plus bas),
            # seul celui qui vient de déclencher le seuil reste visible
            SignalementVendeur.objects.filter(vendeur=vendeur, type_probleme=type_probleme).exclude(
                id=signalement.id
            ).update(resolu_automatiquement=True)

    from Api.broadcast import broadcast_to_admins
    broadcast_to_admins('signalement_vendeur.created', _serialiseSignalementVendeur(signalement))

    return JsonResponse({
        'message': 'Signalement envoyé aux administrateurs',
        'signalement': _serialiseSignalementVendeur(signalement),
    }, status=201)


# ── SIGNALEMENTS VENDEUR EN ATTENTE (admin) ───────────────────────────────────
@csrf_exempt
def listerSignalementsVendeursAdmin(request):
    """Signalements de vendeurs pas encore traités, tous confondus — même
    principe de file partagée que listerSignalementsAdmin."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementVendeur.objects.filter(
        admin_traitant__isnull=True, resolu_automatiquement=False
    ).select_related('vendeur', 'signaleur')

    return JsonResponse({
        'signalements': [_serialiseSignalementVendeur(s) for s in signalements],
    }, status=200)


# ── SIGNALEMENTS VENDEUR DÉJÀ TRAITÉS (admin) ─────────────────────────────────
@csrf_exempt
def listerSignalementsVendeursTraites(request):
    """Historique des signalements de vendeurs déjà traités — MOI SEUL par
    défaut, TOUT LE MONDE pour un compte "Tous les droits" ou le propriétaire
    (voir listerSignalementsTraites ci-dessus, même principe). Exclut aussi
    ce que MOI j'ai retiré de MON historique (historique_masque_pour)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementVendeur.objects.filter(admin_traitant__isnull=False).exclude(historique_masque_pour=utilisateur)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)
    signalements = signalements.select_related('vendeur', 'signaleur', 'admin_traitant').order_by('-date_traitement')

    return JsonResponse({
        'signalements': [_serialiseSignalementVendeur(s) for s in signalements],
    }, status=200)


# ── RETIRER DE MON HISTORIQUE DES SIGNALEMENTS DE VENDEURS (admin connecté) ──
@csrf_exempt
def supprimerHistoriqueSignalementsVendeurs(request):
    """Même principe que supprimerHistoriqueSignalements ci-dessus (masque
    pour l'admin qui clique, jamais un vrai delete()), pour les signalements
    de vendeurs."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    signalements = SignalementVendeur.objects.filter(id__in=ids, admin_traitant__isnull=False)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)

    nombre_masque = 0
    for signalement in signalements:
        signalement.historique_masque_pour.add(utilisateur)
        nombre_masque += 1

    enregistrer_audit(utilisateur, 'signalement_vendeur.masquer_historique', f"A retiré {nombre_masque} entrée(s) de son historique des signalements de vendeurs")

    return JsonResponse({'nombre_supprime': nombre_masque}, status=200)


# ── TRAITER UN OU PLUSIEURS SIGNALEMENTS VENDEUR (admin) ──────────────────────
@csrf_exempt
def traiterSignalementVendeur(request):
    """
    Marque un ou plusieurs signalements du MÊME vendeur comme traités en un
    seul geste (voir traiterSignalement ci-dessus, même principe de
    regroupement par cible et d'explication obligatoire) — ne lève pas la
    suspension automatique du compte (le cas échéant) : c'est à l'admin de le
    faire séparément via reactiverVendeurAdmin (Registration/views.py) s'il
    juge la situation résolue.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    explication = (data.get('explication') or '').strip()
    if not explication:
        return JsonResponse({'error': "Le champ explication est requis", 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'explication'}}, status=400)

    signalements_cibles = SignalementVendeur.objects.filter(id__in=ids)
    if not signalements_cibles.exists():
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    ids_deja_traites = list(signalements_cibles.filter(admin_traitant__isnull=False).values_list('id', flat=True))

    nombre_maj = SignalementVendeur.objects.filter(id__in=ids, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now(), explication_decision=explication
    )
    if nombre_maj == 0:
        return JsonResponse({'error': 'Ce ou ces signalements ont déjà été traités'}, status=409)

    signalements_traites = list(
        SignalementVendeur.objects.filter(id__in=ids, admin_traitant=utilisateur).select_related('vendeur', 'signaleur')
    )

    from Api.broadcast import broadcast_to_admins
    for signalement in signalements_traites:
        broadcast_to_admins('signalement_vendeur.traite', {'id': signalement.id})

    noms_vendeurs = ", ".join(sorted({_nomVendeur(s.vendeur) for s in signalements_traites}))
    enregistrer_audit(
        utilisateur, 'signalement_vendeur.traiter',
        f"A traité {nombre_maj} signalement(s) du vendeur {noms_vendeurs} (ids {ids}) — Raison : {explication}"
    )

    return JsonResponse({
        'signalements': [_serialiseSignalementVendeur(s) for s in signalements_traites],
        'ids_deja_traites': ids_deja_traites,
    }, status=200)


def _serialiseSignalementAvis(signalement):
    # avis_id peut être None si l'avis a depuis été supprimé par un admin
    # (voir supprimerAvis, Produits/views/avisViews.py — avis en SET_NULL,
    # pas CASCADE, voir SignalementAvis.avis) : on retombe alors sur la copie
    # figée prise au moment du signalement (*_snapshot) pour que l'historique
    # et le rapport PDF restent lisibles malgré la suppression.
    avis_existe = signalement.avis_id is not None
    return {
        'id':                signalement.id,
        'avis_id':           signalement.avis_id,
        'avis_supprime':     not avis_existe,
        'avis_commentaire':  signalement.avis.commentaire if avis_existe else signalement.avis_commentaire_snapshot,
        'avis_note':         signalement.avis.note if avis_existe else signalement.avis_note_snapshot,
        'produit_id':        signalement.avis.produit_id if avis_existe else None,
        'produit_nom':       signalement.avis.produit.nom if avis_existe else signalement.produit_nom_snapshot,
        'auteur_avis_id':    signalement.avis.auteur_id if avis_existe else None,
        'auteur_avis_nom':   (_nomVendeur(signalement.avis.auteur) if signalement.avis.auteur_id else None) if avis_existe else signalement.auteur_avis_nom_snapshot,
        # contexte pour l'admin avant de décider de supprimer l'avis (voir
        # Utilisateur.ajouter_avertissement, Registration/models.py) — chaque
        # avis supprimé par un admin ajoute un avertissement à son auteur
        'auteur_avis_avertissements': (signalement.avis.auteur.nombre_avertissements if signalement.avis.auteur_id else None) if avis_existe else None,
        'signaleur_id':      signalement.signaleur_id,
        'signaleur_nom':     _nomVendeur(signalement.signaleur) if signalement.signaleur_id else None,
        'type_probleme':     signalement.type_probleme,
        'motif':             signalement.motif,
        'date_signalement':  signalement.date_signalement.isoformat(),
        'admin_traitant_id':  signalement.admin_traitant_id,
        'admin_traitant_nom': _nomVendeur(signalement.admin_traitant) if signalement.admin_traitant_id else None,
        'admin_traitant_email': signalement.admin_traitant.email if signalement.admin_traitant_id else None,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
        'explication_decision': signalement.explication_decision,
    }


# ── SIGNALER UN AVIS (acheteur ou vendeur connecté) ───────────────────────────
@csrf_exempt
def signalerAvis(request):
    """
    Signale un avis (contenu inapproprié, faux avis...) directement aux
    administrateurs — jamais à l'auteur de l'avis. Réservé aux comptes
    connectés, même logique que signalerProduit/signalerVendeur ci-dessus.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    avis_id = data.get('avis_id')
    if not avis_id:
        return JsonResponse({'error': 'Le champ avis_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'avis_id'}}, status=400)

    type_probleme = data.get('type_probleme')
    if type_probleme not in dict(SignalementAvis.TYPE_PROBLEME):
        return JsonResponse({'error': 'Le champ type_probleme est requis et doit être valide'}, status=400)

    motif = (data.get('motif') or '').strip()
    if not motif:
        return JsonResponse({'error': 'Le motif est requis'}, status=400)

    try:
        avis = AvisProduit.objects.get(id=avis_id)
    except AvisProduit.DoesNotExist:
        return JsonResponse({'error': 'Avis introuvable', 'error_code': 'REVIEW_NOT_FOUND'}, status=404)

    if avis.auteur_id == utilisateur.id:
        return JsonResponse({'error': 'Vous ne pouvez pas signaler votre propre avis'}, status=400)

    signalement = SignalementAvis.objects.create(
        avis=avis, signaleur=utilisateur, type_probleme=type_probleme, motif=motif,
        # copie figée immédiate — voir SignalementAvis.avis_commentaire_snapshot
        # (Produits/models/signalementAvisModel.py) : reste consultable même si
        # l'avis est supprimé plus tard par un admin
        avis_commentaire_snapshot=avis.commentaire,
        avis_note_snapshot=avis.note,
        produit_nom_snapshot=avis.produit.nom,
        auteur_avis_nom_snapshot=_nomVendeur(avis.auteur) if avis.auteur_id else '',
    )

    from Api.broadcast import broadcast_to_admins
    broadcast_to_admins('signalement_avis.created', _serialiseSignalementAvis(signalement))

    return JsonResponse({
        'message': 'Signalement envoyé aux administrateurs',
        'signalement': _serialiseSignalementAvis(signalement),
    }, status=201)


# ── SIGNALEMENTS AVIS EN ATTENTE (admin) ──────────────────────────────────────
@csrf_exempt
def listerSignalementsAvisAdmin(request):
    """Signalements d'avis pas encore traités, tous confondus — même principe
    de file partagée que listerSignalementsAdmin."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementAvis.objects.filter(admin_traitant__isnull=True).select_related(
        'avis', 'avis__produit', 'avis__auteur', 'signaleur'
    )

    return JsonResponse({
        'signalements': [_serialiseSignalementAvis(s) for s in signalements],
    }, status=200)


# ── SIGNALEMENTS AVIS DÉJÀ TRAITÉS (admin) ────────────────────────────────────
@csrf_exempt
def listerSignalementsAvisTraites(request):
    """Historique des signalements d'avis déjà traités — MOI SEUL par défaut,
    TOUT LE MONDE pour un compte "Tous les droits" ou le propriétaire (voir
    listerSignalementsTraites plus haut, même principe). Exclut aussi ce que
    MOI j'ai retiré de MON historique (historique_masque_pour)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementAvis.objects.filter(admin_traitant__isnull=False).exclude(historique_masque_pour=utilisateur)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)
    signalements = signalements.select_related('avis', 'avis__produit', 'avis__auteur', 'signaleur', 'admin_traitant').order_by('-date_traitement')

    return JsonResponse({
        'signalements': [_serialiseSignalementAvis(s) for s in signalements],
    }, status=200)


# ── RETIRER DE MON HISTORIQUE DES SIGNALEMENTS D'AVIS (admin connecté) ───────
@csrf_exempt
def supprimerHistoriqueSignalementsAvis(request):
    """Même principe que supprimerHistoriqueSignalements ci-dessus (masque
    pour l'admin qui clique, jamais un vrai delete()), pour les signalements
    d'avis."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    signalements = SignalementAvis.objects.filter(id__in=ids, admin_traitant__isnull=False)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)

    nombre_masque = 0
    for signalement in signalements:
        signalement.historique_masque_pour.add(utilisateur)
        nombre_masque += 1

    enregistrer_audit(utilisateur, 'signalement_avis.masquer_historique', f"A retiré {nombre_masque} entrée(s) de son historique des signalements d'avis")

    return JsonResponse({'nombre_supprime': nombre_masque}, status=200)


# ── TRAITER UN OU PLUSIEURS SIGNALEMENTS AVIS (admin) ─────────────────────────
@csrf_exempt
def traiterSignalementAvis(request):
    """
    Marque un ou plusieurs signalements du MÊME avis comme traités en un seul
    geste (voir traiterSignalement ci-dessus, même principe de regroupement
    par cible et d'explication obligatoire) — ne supprime pas l'avis
    automatiquement : c'est à l'admin de le faire séparément via supprimerAvis
    (Produits/views/avisViews.py) s'il juge l'avis à retirer. L'avis étant
    lié en SET_NULL (voir SignalementAvis.avis), la ligne de signalement — et
    donc son explication — reste consultable même après suppression de l'avis.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    explication = (data.get('explication') or '').strip()
    if not explication:
        return JsonResponse({'error': "Le champ explication est requis", 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'explication'}}, status=400)

    signalements_cibles = SignalementAvis.objects.filter(id__in=ids)
    if not signalements_cibles.exists():
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    ids_deja_traites = list(signalements_cibles.filter(admin_traitant__isnull=False).values_list('id', flat=True))

    nombre_maj = SignalementAvis.objects.filter(id__in=ids, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now(), explication_decision=explication
    )
    if nombre_maj == 0:
        return JsonResponse({'error': 'Ce ou ces signalements ont déjà été traités'}, status=409)

    signalements_traites = list(
        SignalementAvis.objects.filter(id__in=ids, admin_traitant=utilisateur).select_related('avis', 'avis__produit', 'avis__auteur', 'signaleur')
    )

    from Api.broadcast import broadcast_to_admins
    for signalement in signalements_traites:
        broadcast_to_admins('signalement_avis.traite', {'id': signalement.id})

    enregistrer_audit(
        utilisateur, 'signalement_avis.traiter',
        f"A traité {nombre_maj} signalement(s) d'avis (ids {ids}) — Raison : {explication}"
    )

    return JsonResponse({
        'signalements': [_serialiseSignalementAvis(s) for s in signalements_traites],
        'ids_deja_traites': ids_deja_traites,
    }, status=200)


# ── RAPPORT PDF D'AUDIT — SIGNALEMENTS (Tous les droits / propriétaire) ───────
@csrf_exempt
def genererRapportSignalements(request):
    """
    Génère le rapport PDF listant, sur une période choisie, chaque signalement
    déjà traité — produits, vendeurs, messages ET avis confondus (colonne
    "Type") — avec sa cible, son motif et QUI l'a traité (demande explicite :
    "voir le signalement en question et la résolution") — réservé à "Tous les
    droits" ou au propriétaire. Aucun champ "résolution" texte libre n'existe
    sur ces modèles (voir SignalementProduit/Vendeur/Avis, Produits/models/*,
    et SignalementMessage, Messagerie/models.py) : la "résolution" tracée ici
    est admin_traitant + date_traitement, seule décision que ces modèles
    conservent. Symétrique de Messagerie/views.py::genererRapportSupport
    (mêmes conventions de validation de dates/admin_id).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'super_admin'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'super_admin'}}, status=403)

    from datetime import datetime

    date_debut_str = request.GET.get('date_debut')
    date_fin_str   = request.GET.get('date_fin')
    if not date_debut_str or not date_fin_str:
        return JsonResponse({'error': 'Les champs date_debut et date_fin (AAAA-MM-JJ) sont requis', 'error_code': 'FIELD_REQUIRED'}, status=400)

    try:
        date_debut = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
        date_fin   = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Dates invalides, format attendu AAAA-MM-JJ', 'error_code': 'INVALID_DATE'}, status=400)

    if date_debut > date_fin:
        return JsonResponse({'error': 'La date de début doit précéder la date de fin'}, status=400)

    if date_fin > timezone.localdate():
        return JsonResponse({'error': 'La date de fin ne peut pas être dans le futur', 'error_code': 'INVALID_DATE'}, status=400)

    from Registration.models import Utilisateur
    from Messagerie.models import SignalementMessage

    admin_id = request.GET.get('admin_id')
    nom_admin_filtre = None
    if admin_id:
        try:
            admin_filtre = Utilisateur.objects.get(id=admin_id)
        except Utilisateur.DoesNotExist:
            return JsonResponse({'error': 'Administrateur introuvable', 'error_code': 'USER_NOT_FOUND'}, status=404)
        nom_admin_filtre = _nomVendeur(admin_filtre)

    def _filtre_periode_admin(qs):
        qs = qs.filter(
            admin_traitant__isnull=False,
            date_traitement__date__gte=date_debut, date_traitement__date__lte=date_fin,
        )
        if admin_id:
            qs = qs.filter(admin_traitant_id=admin_id)
        return qs

    entrees = []

    for s in _filtre_periode_admin(SignalementProduit.objects.select_related('produit', 'admin_traitant')):
        entrees.append({
            'type_libelle':          'Produit',
            'cible':                 s.produit.nom,
            'type_probleme_libelle': dict(SignalementProduit.TYPE_PROBLEME).get(s.type_probleme, s.type_probleme),
            'motif':                 s.motif,
            'explication':           s.explication_decision,
            'admin_nom':             _nomVendeur(s.admin_traitant),
            'admin_email':           s.admin_traitant.email,
            'date_signalement':      s.date_signalement,
            'date_traitement':       s.date_traitement,
        })

    for s in _filtre_periode_admin(SignalementVendeur.objects.select_related('vendeur', 'admin_traitant')):
        entrees.append({
            'type_libelle':          'Vendeur',
            'cible':                 _nomVendeur(s.vendeur),
            'type_probleme_libelle': dict(SignalementVendeur.TYPE_PROBLEME).get(s.type_probleme, s.type_probleme),
            'motif':                 s.motif,
            'explication':           s.explication_decision,
            'admin_nom':             _nomVendeur(s.admin_traitant),
            'admin_email':           s.admin_traitant.email,
            'date_signalement':      s.date_signalement,
            'date_traitement':       s.date_traitement,
        })

    # avis potentiellement supprimé depuis (avis_id devient None, voir
    # SignalementAvis.avis en SET_NULL) — on retombe alors sur produit_nom_snapshot
    for s in _filtre_periode_admin(SignalementAvis.objects.select_related('avis', 'avis__produit', 'admin_traitant')):
        nom_produit = s.avis.produit.nom if s.avis_id else (s.produit_nom_snapshot or 'produit inconnu')
        entrees.append({
            'type_libelle':          'Avis',
            'cible':                 f"Avis sur « {nom_produit} »" + ('' if s.avis_id else ' (avis supprimé)'),
            'type_probleme_libelle': dict(SignalementAvis.TYPE_PROBLEME).get(s.type_probleme, s.type_probleme),
            'motif':                 s.motif,
            'explication':           s.explication_decision,
            'admin_nom':             _nomVendeur(s.admin_traitant),
            'admin_email':           s.admin_traitant.email,
            'date_signalement':      s.date_signalement,
            'date_traitement':       s.date_traitement,
        })

    for s in _filtre_periode_admin(SignalementMessage.objects.select_related('message', 'message__expediteur', 'admin_traitant')):
        entrees.append({
            'type_libelle':          'Message',
            'cible':                 f"Message de {_nomVendeur(s.message.expediteur)}",
            'type_probleme_libelle': dict(SignalementMessage.TYPE_PROBLEME).get(s.type_probleme, s.type_probleme),
            'motif':                 s.motif,
            'explication':           s.explication_decision,
            'admin_nom':             _nomVendeur(s.admin_traitant),
            'admin_email':           s.admin_traitant.email,
            'date_signalement':      s.date_signalement,
            'date_traitement':       s.date_traitement,
        })

    entrees.sort(key=lambda e: e['date_traitement'])

    enregistrer_audit(
        utilisateur, 'signalement.rapport_audit',
        f"A généré le rapport d'audit signalements ({date_debut} → {date_fin}, filtre : {nom_admin_filtre or 'tous les administrateurs'})",
    )

    from ..services.rapport_signalements_service import generer_rapport_signalements
    pdf = generer_rapport_signalements(
        entrees=entrees, date_debut=date_debut, date_fin=date_fin,
        nom_admin_filtre=nom_admin_filtre,
    )

    return HttpResponse(pdf.read(), content_type='application/pdf')
