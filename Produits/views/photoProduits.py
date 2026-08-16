import json

from django.db import models
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..models import Produits, photo_produits
from ._auth import _get_user_from_token


def _serialisePhoto(photo, request=None):
    url = photo.url_photo.url if photo.url_photo else None
    if url and request:
        url = request.build_absolute_uri(url)

    return {
        'id':         photo.id,
        'produit_id': photo.produits_id,
        'url_photo':  url,
        'ordre':      photo.ordre,
    }


# ── AJOUTER DES PHOTOS À UN PRODUIT (propriétaire) ────────────────────────────
@csrf_exempt
def ajouterPhotosProduit(request):
    """
    Ajoute une ou plusieurs photos à un produit du vendeur connecté.
    Upload multipart/form-data (comme soumettre_verification, pas de base64) :
    champ texte 'produit_id' + fichiers sous la clé 'photos' (plusieurs possibles).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    produit_id = request.POST.get('produit_id')
    if not produit_id:
        return JsonResponse({'error': 'Le champ produit_id est requis'}, status=400)

    try:
        produit = Produits.objects.get(id=produit_id, vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable'}, status=404)

    fichiers = request.FILES.getlist('photos')
    if not fichiers:
        return JsonResponse({'error': 'Au moins un fichier photos est requis'}, status=400)

    # les nouvelles photos s'ajoutent APRÈS les photos existantes (voir
    # photo_produits.Meta.ordering) plutôt que de repartir à 0 — sinon elles
    # se retrouveraient mélangées avec les premières lors d'un ajout ultérieur
    # sur un produit qui a déjà des photos, dans l'ordre où request.FILES
    # les a reçues (voir ajouterPhotosProduit, ProduitsApi.js — préserve
    # l'ordre choisi côté formulaire, voir AjouterProduit.jsx/ModifierProduit.jsx)
    ordre_depart = (produit.photos.aggregate(models.Max('ordre'))['ordre__max'] or 0) + 1
    photos = [
        photo_produits.objects.create(produits=produit, url_photo=fichier, ordre=ordre_depart + i)
        for i, fichier in enumerate(fichiers)
    ]

    return JsonResponse({
        'message': 'Photos ajoutées avec succès',
        'photos':  [_serialisePhoto(p, request) for p in photos],
    }, status=201)


# ── RÉORDONNER LES PHOTOS D'UN PRODUIT (propriétaire) ─────────────────────────
@csrf_exempt
def reordonnerPhotosProduit(request):
    """
    Change l'ordre d'affichage des photos d'un produit du vendeur connecté.
    Reçoit la liste COMPLÈTE des id de photos du produit dans le nouvel ordre
    voulu (voir ModifierProduit.jsx) ; chaque photo prend pour 'ordre' sa
    position dans cette liste.
    """
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    produit_id = data.get('produit_id')
    ordre_ids  = data.get('ordre')
    if not produit_id or not isinstance(ordre_ids, list) or not ordre_ids:
        return JsonResponse({'error': 'Les champs produit_id et ordre (liste non vide) sont requis', 'error_code': 'FIELD_REQUIRED'}, status=400)

    try:
        produit = Produits.objects.get(id=produit_id, vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    photos = {photo.id: photo for photo in produit.photos.all()}

    # la liste reçue doit correspondre EXACTEMENT à l'ensemble des photos du
    # produit — ni plus (id étranger à ce produit), ni moins (une photo
    # oubliée se retrouverait avec un 'ordre' non défini par cette requête) —
    # sinon le tri obtenu serait incohérent avec ce que le vendeur voit
    if set(ordre_ids) != set(photos.keys()):
        return JsonResponse({
            'error': "La liste ordre doit contenir exactement les photos de ce produit"
        }, status=400)

    for position, photo_id in enumerate(ordre_ids):
        photo = photos[photo_id]
        if photo.ordre != position:
            photo.ordre = position
            photo.save()

    # le produit lui-même n'a pas changé, mais ses photos (imbriquées dans sa
    # sérialisation) si : rediffuser "produit.updated" pour que les catalogues/
    # fiches déjà affichés reflètent le nouvel ordre sans rechargement — même
    # principe que broadcast_avis (Produits/signals.py), qui rediffuse aussi
    # le produit parent après une modification d'une ressource imbriquée.
    from Api.broadcast import broadcast
    from .produitsViews import _serialiseProduit
    broadcast('produit.updated', _serialiseProduit(produit, request))

    return JsonResponse({
        'message': 'Ordre des photos mis à jour avec succès',
        'photos':  [_serialisePhoto(p, request) for p in produit.photos.all()],
    }, status=200)


# ── LISTER LES PHOTOS D'UN PRODUIT (public) ───────────────────────────────────
@csrf_exempt
def listerPhotosProduit(request):
    """Liste les photos d'un produit donné (public)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    produit_id = request.GET.get('produit_id')
    if not produit_id:
        return JsonResponse({'error': 'Le paramètre produit_id est requis'}, status=400)

    photos = photo_produits.objects.filter(produits_id=produit_id)

    return JsonResponse({
        'photos': [_serialisePhoto(p, request) for p in photos],
    }, status=200)


# ── SUPPRIMER UNE PHOTO (propriétaire) ────────────────────────────────────────
@csrf_exempt
def supprimerPhotoProduit(request):
    """Supprime une photo (fichier physique + ligne en base) d'un produit du vendeur connecté."""
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
        return JsonResponse({'error': 'Le champ id est requis'}, status=400)

    # scoper la recherche au vendeur connecté pour empêcher la suppression de photos tierces
    try:
        photo = photo_produits.objects.get(id=data['id'], produits__vendeur=utilisateur)
    except photo_produits.DoesNotExist:
        return JsonResponse({'error': 'Photo introuvable'}, status=404)

    if photo.url_photo:
        photo.url_photo.delete(save=False)   # supprime le fichier du disque
    photo.delete()

    return JsonResponse({'message': 'Photo supprimée avec succès'}, status=200)
