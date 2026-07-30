import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..models import Categories, sousCategories
from ._auth import _get_user_from_token


def _serialiseSousCategorie(sous_categorie):
    return {
        'id':           sous_categorie.id,
        'nom':          sous_categorie.nom,
        'categorie_id': sous_categorie.categorie_id,
    }


# ── LISTER LES SOUS-CATÉGORIES (public) ───────────────────────────────────────
@csrf_exempt
def listerSousCategories(request):
    """Liste les sous-catégories, filtrables par catégorie parente (?categorie_id=) — public."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    sous_categories = sousCategories.objects.select_related('categorie').all()

    categorie_id = request.GET.get('categorie_id')
    if categorie_id:
        sous_categories = sous_categories.filter(categorie_id=categorie_id)

    return JsonResponse({
        'sous_categories': [_serialiseSousCategorie(sc) for sc in sous_categories],
    }, status=200)


# ── CRÉER UNE SOUS-CATÉGORIE (admin) ──────────────────────────────────────────
@csrf_exempt
def creerSousCategorie(request):
    """Crée une sous-catégorie, rattachée à une catégorie existante (accès réservé au rôle admin)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    for field in ['nom', 'categorie_id']:
        if not data.get(field):
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    try:
        categorie = Categories.objects.get(id=data['categorie_id'])
    except Categories.DoesNotExist:
        return JsonResponse({'error': 'Catégorie introuvable'}, status=404)

    sous_categorie = sousCategories.objects.create(categorie=categorie, nom=data['nom'])

    return JsonResponse({
        'message':        'Sous-catégorie créée avec succès',
        'sous_categorie': _serialiseSousCategorie(sous_categorie),
    }, status=201)


# ── MODIFIER UNE SOUS-CATÉGORIE (admin) ───────────────────────────────────────
@csrf_exempt
def modifierSousCategorie(request):
    """Met à jour une sous-catégorie (nom et/ou catégorie parente) — accès réservé au rôle admin."""
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis'}, status=400)

    try:
        sous_categorie = sousCategories.objects.get(id=data['id'])
    except sousCategories.DoesNotExist:
        return JsonResponse({'error': 'Sous-catégorie introuvable'}, status=404)

    if 'categorie_id' in data:
        try:
            sous_categorie.categorie = Categories.objects.get(id=data['categorie_id'])
        except Categories.DoesNotExist:
            return JsonResponse({'error': 'Catégorie introuvable'}, status=404)

    if 'nom' in data:
        sous_categorie.nom = data['nom']

    sous_categorie.save()

    return JsonResponse({
        'message':        'Sous-catégorie mise à jour avec succès',
        'sous_categorie': _serialiseSousCategorie(sous_categorie),
    }, status=200)


# ── SUPPRIMER UNE SOUS-CATÉGORIE (admin) ──────────────────────────────────────
@csrf_exempt
def supprimerSousCategorie(request):
    """Supprime une sous-catégorie (accès réservé au rôle admin)."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis'}, status=400)

    try:
        sous_categorie = sousCategories.objects.get(id=data['id'])
    except sousCategories.DoesNotExist:
        return JsonResponse({'error': 'Sous-catégorie introuvable'}, status=404)

    sous_categorie.delete()

    return JsonResponse({'message': 'Sous-catégorie supprimée avec succès'}, status=200)
