import json

from django.http import JsonResponse
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
        'admin_traitant_id': signalement.admin_traitant_id,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
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


# ── TRAITER UN SIGNALEMENT (admin) ────────────────────────────────────────────
@csrf_exempt
def traiterSignalement(request):
    """
    Marque un signalement comme traité — ne bloque pas le compte du vendeur
    automatiquement : c'est à l'admin de décider séparément via
    toggleBloquerUtilisateur (Registration/views.py) s'il y a lieu. Même
    prise en charge atomique "premier arrivé premier servi" que
    repondreMessageAdmin (Messagerie/views.py).
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

    signalement_id = data.get('id')
    if not signalement_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        signalement = SignalementProduit.objects.get(id=signalement_id)
    except SignalementProduit.DoesNotExist:
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    maj = SignalementProduit.objects.filter(id=signalement_id, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now()
    )
    if maj == 0:
        signalement.refresh_from_db()
        nom_autre_admin = _nomVendeur(signalement.admin_traitant) if signalement.admin_traitant_id else None
        return JsonResponse({
            'error': f"Ce signalement a déjà été traité par {nom_autre_admin}" if nom_autre_admin
                     else "Ce signalement a déjà été traité",
        }, status=409)

    signalement.refresh_from_db()

    from Api.broadcast import broadcast_to_admins
    # retire le signalement de la file des AUTRES admins
    broadcast_to_admins('signalement.traite', {'id': signalement.id})
    enregistrer_audit(utilisateur, 'signalement_produit.traiter', f"A traité le signalement du produit « {signalement.produit.nom} » (signalement id {signalement.id})")

    return JsonResponse({
        'signalement': _serialiseSignalement(signalement),
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
        'admin_traitant_id': signalement.admin_traitant_id,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
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


# ── TRAITER UN SIGNALEMENT VENDEUR (admin) ────────────────────────────────────
@csrf_exempt
def traiterSignalementVendeur(request):
    """
    Marque un signalement de vendeur comme traité — ne lève pas la suspension
    automatique du compte (le cas échéant) : c'est à l'admin de le faire
    séparément via reactiverVendeurAdmin (Registration/views.py) s'il juge la
    situation résolue. Même prise en charge atomique "premier arrivé premier
    servi" que traiterSignalement ci-dessus.
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

    signalement_id = data.get('id')
    if not signalement_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        signalement = SignalementVendeur.objects.get(id=signalement_id)
    except SignalementVendeur.DoesNotExist:
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    maj = SignalementVendeur.objects.filter(id=signalement_id, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now()
    )
    if maj == 0:
        signalement.refresh_from_db()
        nom_autre_admin = _nomVendeur(signalement.admin_traitant) if signalement.admin_traitant_id else None
        return JsonResponse({
            'error': f"Ce signalement a déjà été traité par {nom_autre_admin}" if nom_autre_admin
                     else "Ce signalement a déjà été traité",
        }, status=409)

    signalement.refresh_from_db()

    from Api.broadcast import broadcast_to_admins
    broadcast_to_admins('signalement_vendeur.traite', {'id': signalement.id})
    enregistrer_audit(utilisateur, 'signalement_vendeur.traiter', f"A traité le signalement du vendeur {_nomVendeur(signalement.vendeur)} (signalement id {signalement.id})")

    return JsonResponse({
        'signalement': _serialiseSignalementVendeur(signalement),
    }, status=200)


def _serialiseSignalementAvis(signalement):
    return {
        'id':                signalement.id,
        'avis_id':           signalement.avis_id,
        'avis_commentaire':  signalement.avis.commentaire,
        'avis_note':         signalement.avis.note,
        'produit_id':        signalement.avis.produit_id,
        'produit_nom':       signalement.avis.produit.nom,
        'auteur_avis_id':    signalement.avis.auteur_id,
        'auteur_avis_nom':   _nomVendeur(signalement.avis.auteur) if signalement.avis.auteur_id else None,
        # contexte pour l'admin avant de décider de supprimer l'avis (voir
        # Utilisateur.ajouter_avertissement, Registration/models.py) — chaque
        # avis supprimé par un admin ajoute un avertissement à son auteur
        'auteur_avis_avertissements': signalement.avis.auteur.nombre_avertissements if signalement.avis.auteur_id else None,
        'signaleur_id':      signalement.signaleur_id,
        'signaleur_nom':     _nomVendeur(signalement.signaleur) if signalement.signaleur_id else None,
        'type_probleme':     signalement.type_probleme,
        'motif':             signalement.motif,
        'date_signalement':  signalement.date_signalement.isoformat(),
        'admin_traitant_id': signalement.admin_traitant_id,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
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


# ── TRAITER UN SIGNALEMENT AVIS (admin) ───────────────────────────────────────
@csrf_exempt
def traiterSignalementAvis(request):
    """
    Marque un signalement d'avis comme traité — ne supprime pas l'avis
    automatiquement : c'est à l'admin de le faire séparément via
    supprimerAvis (Produits/views/avisViews.py, qui autorise désormais aussi
    un admin à supprimer l'avis d'un autre compte) s'il juge l'avis à retirer.
    Même prise en charge atomique "premier arrivé premier servi" que
    traiterSignalement ci-dessus.
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

    signalement_id = data.get('id')
    if not signalement_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        signalement = SignalementAvis.objects.get(id=signalement_id)
    except SignalementAvis.DoesNotExist:
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    maj = SignalementAvis.objects.filter(id=signalement_id, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now()
    )
    if maj == 0:
        signalement.refresh_from_db()
        nom_autre_admin = _nomVendeur(signalement.admin_traitant) if signalement.admin_traitant_id else None
        return JsonResponse({
            'error': f"Ce signalement a déjà été traité par {nom_autre_admin}" if nom_autre_admin
                     else "Ce signalement a déjà été traité",
        }, status=409)

    signalement.refresh_from_db()

    from Api.broadcast import broadcast_to_admins
    broadcast_to_admins('signalement_avis.traite', {'id': signalement.id})
    enregistrer_audit(utilisateur, 'signalement_avis.traiter', f"A traité le signalement de l'avis id {signalement.avis_id} (signalement id {signalement.id})")

    return JsonResponse({
        'signalement': _serialiseSignalementAvis(signalement),
    }, status=200)
