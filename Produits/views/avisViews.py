import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from Registration.models import verifier_droit_admin, enregistrer_audit
from ..models import Produits, AvisProduit
from ._auth import _get_user_from_token
from .produitsViews import _nomVendeur


def _serialiseAvis(avis):
    return {
        'id':                avis.id,
        'produit_id':        avis.produit_id,
        'auteur_id':         avis.auteur_id,
        'auteur_nom':        _nomVendeur(avis.auteur) if avis.auteur_id else None,
        'note':              avis.note,
        'commentaire':       avis.commentaire,
        'date_avis':         avis.date_avis.isoformat(),
        'date_modification': avis.date_modification.isoformat(),
    }


# ── CRÉER OU MODIFIER SON AVIS (acheteur ou vendeur connecté) ─────────────────
@csrf_exempt
def creerModifierAvis(request):
    """
    Pose ou met à jour l'avis (note + commentaire) du compte connecté sur un
    produit — un vendeur ne peut pas noter son propre produit. Une nouvelle
    soumission sur un produit déjà noté par ce compte MODIFIE l'avis existant
    (contrainte d'unicité produit+auteur, voir AvisProduit.Meta), elle n'en
    crée jamais un second.
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

    try:
        note = int(data.get('note'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Le champ note est requis et doit être un entier'}, status=400)
    if note < 1 or note > 5:
        return JsonResponse({'error': 'La note doit être comprise entre 1 et 5'}, status=400)

    commentaire = (data.get('commentaire') or '').strip()

    try:
        produit = Produits.objects.get(id=produit_id)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    if produit.vendeur_id == utilisateur.id:
        return JsonResponse({'error': 'Vous ne pouvez pas noter votre propre produit'}, status=400)

    avis, cree = AvisProduit.objects.update_or_create(
        produit=produit, auteur=utilisateur,
        defaults={'note': note, 'commentaire': commentaire},
    )

    return JsonResponse({
        'message': 'Avis enregistré' if cree else 'Avis mis à jour',
        'avis': _serialiseAvis(avis),
    }, status=201 if cree else 200)


# ── LISTER LES AVIS D'UN PRODUIT (public) ─────────────────────────────────────
@csrf_exempt
def listerAvisProduit(request):
    """Avis d'un produit, du plus récent au plus ancien (public — consulter
    les avis ne nécessite pas de compte)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    produit_id = request.GET.get('produit_id')
    if not produit_id:
        return JsonResponse({'error': 'Le champ produit_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'produit_id'}}, status=400)

    avis = AvisProduit.objects.filter(produit_id=produit_id).select_related('auteur')

    return JsonResponse({
        'avis': [_serialiseAvis(a) for a in avis],
    }, status=200)


# ── SUPPRIMER UN AVIS (auteur connecté, ou admin suite à un signalement) ──────
@csrf_exempt
def supprimerAvis(request):
    """
    Supprime un avis — son auteur peut toujours supprimer le sien (aucune
    conséquence dans ce cas) ; un admin peut en plus supprimer l'avis de
    n'importe qui, typiquement après avoir traité un signalement (voir
    signalementsViews.py::signalerAvis). Dans ce second cas uniquement,
    l'auteur reçoit un avertissement (Utilisateur.nombre_avertissements,
    Registration/models.py) via un message automatique de la part de
    l'admin (réutilise la messagerie existante, identité affichée "Admin",
    voir Messagerie/views.py::_nomAffiche) — au-delà de SEUIL_AVERTISSEMENTS,
    le compte est bloqué automatiquement (voir Utilisateur.ajouter_avertissement)
    et un second message en informe l'auteur.
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    # un admin sans le droit gestion_signalements est traité comme un
    # utilisateur normal : il ne peut supprimer que son propre avis
    peut_moderer = verifier_droit_admin(utilisateur, 'gestion_signalements')
    try:
        if peut_moderer:
            avis = AvisProduit.objects.select_related('auteur', 'produit').get(id=data['id'])
        else:
            avis = AvisProduit.objects.get(id=data['id'], auteur=utilisateur)
    except AvisProduit.DoesNotExist:
        return JsonResponse({'error': 'Avis introuvable', 'error_code': 'REVIEW_NOT_FOUND'}, status=404)

    auteur = avis.auteur
    nom_produit = avis.produit.nom
    id_avis = avis.id
    avis.delete()

    # avertissement uniquement quand un admin supprime l'avis de QUELQU'UN
    # D'AUTRE (jamais quand l'auteur supprime le sien lui-même, et jamais si
    # le compte auteur a depuis été supprimé — avis.auteur alors déjà None)
    if peut_moderer and auteur is not None and auteur.id != utilisateur.id:
        enregistrer_audit(utilisateur, 'avis.supprimer', f"A supprimé l'avis de {auteur.prenom} {auteur.nom} sur « {nom_produit} » (avis id {id_avis})")
        from Messagerie.models import Conversation, Message   # import différé : évite un cycle Produits <-> Messagerie
        from Registration.models import SEUIL_AVERTISSEMENTS

        a_ete_bloque = auteur.ajouter_avertissement()

        conversation = Conversation.obtenir_ou_creer(utilisateur, auteur)
        Message.objects.create(
            conversation=conversation, expediteur=utilisateur,
            contenu=(
                f"Votre avis sur le produit « {nom_produit} » a été supprimé par un administrateur "
                f"suite à un signalement. Ceci est un avertissement ({auteur.nombre_avertissements}/{SEUIL_AVERTISSEMENTS})."
            ),
        )
        if a_ete_bloque:
            Message.objects.create(
                conversation=conversation, expediteur=utilisateur,
                contenu=(
                    "Votre compte a été bloqué automatiquement après 10 avertissements. "
                    "Contactez un administrateur si vous pensez qu'il s'agit d'une erreur."
                ),
            )

    return JsonResponse({'message': 'Avis supprimé avec succès'}, status=200)
