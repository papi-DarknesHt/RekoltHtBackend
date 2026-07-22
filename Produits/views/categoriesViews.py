import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..models import Categories
from ._auth import _get_user_from_token


def _serialiseCategorie(categorie):
    return {
        'id':          categorie.id,
        'nom':         categorie.nom,
        'description': categorie.description,
    }


# ── LISTER LES CATÉGORIES (public) ────────────────────────────────────────────
@csrf_exempt
def listerCategories(request):
    """Liste toutes les catégories — public, utilisé pour peupler filtres/formulaires."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    categories = Categories.objects.all()

    return JsonResponse({
        'categories': [_serialiseCategorie(c) for c in categories],
    }, status=200)


# ── CRÉER UNE CATÉGORIE (admin) ───────────────────────────────────────────────
@csrf_exempt
def creerCategorie(request):
    """Crée une catégorie (accès réservé au rôle admin)."""
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

    if 'nom' not in data:
        return JsonResponse({'error': 'Le champ nom est requis'}, status=400)

    categorie = Categories.objects.create(
        nom         = data['nom'],
        description = data.get('description', ''),
    )

    return JsonResponse({
        'message':   'Catégorie créée avec succès',
        'categorie': _serialiseCategorie(categorie),
    }, status=201)


# ── MODIFIER UNE CATÉGORIE (admin) ────────────────────────────────────────────
@csrf_exempt
def modifierCategorie(request):
    """Met à jour une catégorie (accès réservé au rôle admin)."""
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
        categorie = Categories.objects.get(id=data['id'])
    except Categories.DoesNotExist:
        return JsonResponse({'error': 'Catégorie introuvable'}, status=404)

    for champ in ['nom', 'description']:
        if champ in data:
            setattr(categorie, champ, data[champ])
    categorie.save()

    return JsonResponse({
        'message':   'Catégorie mise à jour avec succès',
        'categorie': _serialiseCategorie(categorie),
    }, status=200)


# ── SUPPRIMER UNE CATÉGORIE (admin) ───────────────────────────────────────────
@csrf_exempt
def supprimerCategorie(request):
    """Supprime une catégorie (accès réservé au rôle admin)."""
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
        categorie = Categories.objects.get(id=data['id'])
    except Categories.DoesNotExist:
        return JsonResponse({'error': 'Catégorie introuvable'}, status=404)

    categorie.delete()

    return JsonResponse({'message': 'Catégorie supprimée avec succès'}, status=200)


# ── CHOISIR SES CATÉGORIES (vendeur) ──────────────────────────────────────────
@csrf_exempt
def choisirCategoriesVendeur(request):
    """
    Définit les catégories de produits que le vendeur souhaite publier —
    étape obligatoire après validation de la vérification KYC (voir
    DemandeVerification.marquer_verifie, Registration/models.py) : creerProduit
    (Produits/views/produitsViews.py) refuse toute création tant qu'aucune
    catégorie n'est choisie. Remplace entièrement la sélection précédente
    (pas d'ajout incrémental) — même logique que modifierProduit pour la
    simplicité côté frontend (un seul écran de sélection à re-soumettre).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'vendeur':
        return JsonResponse({'error': "Accès réservé aux vendeurs"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    categorie_ids = data.get('categorie_ids')
    if not categorie_ids or not isinstance(categorie_ids, list):
        return JsonResponse({'error': "Le champ categorie_ids (liste non vide) est requis"}, status=400)

    categories = Categories.objects.filter(id__in=categorie_ids)
    if categories.count() != len(set(categorie_ids)):
        return JsonResponse({'error': "Une ou plusieurs catégories sont introuvables"}, status=404)

    utilisateur.profil.categories_produits.set(categories)

    return JsonResponse({
        'message':    'Catégories mises à jour avec succès',
        'categories': [_serialiseCategorie(c) for c in categories],
    }, status=200)


# ── LISTER MES CATÉGORIES (vendeur connecté) ──────────────────────────────────
@csrf_exempt
def mesCategoriesVendeur(request):
    """Retourne les catégories déjà choisies par le vendeur connecté (pour que
    le frontend sache s'il doit afficher l'écran de sélection obligatoire)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    categories = utilisateur.profil.categories_produits.all()

    return JsonResponse({
        'categories': [_serialiseCategorie(c) for c in categories],
    }, status=200)
