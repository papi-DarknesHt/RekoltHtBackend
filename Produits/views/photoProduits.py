import json

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

    photos = [
        photo_produits.objects.create(produits=produit, url_photo=fichier)
        for fichier in fichiers
    ]

    return JsonResponse({
        'message': 'Photos ajoutées avec succès',
        'photos':  [_serialisePhoto(p, request) for p in photos],
    }, status=201)


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
