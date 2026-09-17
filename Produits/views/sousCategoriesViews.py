import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from Registration.models import verifier_droit_admin, enregistrer_audit
from ..models import Categories, sousCategories
from ._auth import _get_user_from_token


def _serialiseSousCategorie(sous_categorie):
    return {
        'id':           sous_categorie.id,
        'nom':          sous_categorie.nom,       # français — champ canonique, toujours rempli
        'nom_ht':       sous_categorie.nom_ht,    # optionnel — vide si pas encore traduit (voir modèle)
        'nom_en':       sous_categorie.nom_en,    # idem
        'categorie_id': sous_categorie.categorie_id,
    }


# ── LISTER LES SOUS-CATÉGORIES (public) ───────────────────────────────────────
@csrf_exempt
def listerSousCategories(request):
    """Liste les sous-catégories, filtrables par catégorie parente (?categorie_id=) — public."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_categories'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_categories'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    for field in ['nom', 'categorie_id']:
        if not data.get(field):
            return JsonResponse({'error': f'Le champ {field} est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': field}}, status=400)

    try:
        categorie = Categories.objects.get(id=data['categorie_id'])
    except Categories.DoesNotExist:
        return JsonResponse({'error': 'Catégorie introuvable', 'error_code': 'CATEGORY_NOT_FOUND'}, status=404)

    sous_categorie = sousCategories.objects.create(
        categorie = categorie,
        nom       = data['nom'],
        nom_ht    = data.get('nom_ht', ''),
        nom_en    = data.get('nom_en', ''),
    )
    enregistrer_audit(utilisateur, 'sous_categorie.creer', f"A créé la sous-catégorie « {sous_categorie.nom} » (id {sous_categorie.id})")

    return JsonResponse({
        'message':        'Sous-catégorie créée avec succès',
        'sous_categorie': _serialiseSousCategorie(sous_categorie),
    }, status=201)


# ── MODIFIER UNE SOUS-CATÉGORIE (admin) ───────────────────────────────────────
@csrf_exempt
def modifierSousCategorie(request):
    """Met à jour une sous-catégorie (nom et/ou catégorie parente) — accès réservé au rôle admin."""
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_categories'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_categories'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        sous_categorie = sousCategories.objects.get(id=data['id'])
    except sousCategories.DoesNotExist:
        return JsonResponse({'error': 'Sous-catégorie introuvable', 'error_code': 'SUBCATEGORY_NOT_FOUND'}, status=404)

    if 'categorie_id' in data:
        try:
            sous_categorie.categorie = Categories.objects.get(id=data['categorie_id'])
        except Categories.DoesNotExist:
            return JsonResponse({'error': 'Catégorie introuvable', 'error_code': 'CATEGORY_NOT_FOUND'}, status=404)

    for champ in ['nom', 'nom_ht', 'nom_en']:
        if champ in data:
            setattr(sous_categorie, champ, data[champ])

    sous_categorie.save()
    enregistrer_audit(utilisateur, 'sous_categorie.modifier', f"A modifié la sous-catégorie « {sous_categorie.nom} » (id {sous_categorie.id})")

    return JsonResponse({
        'message':        'Sous-catégorie mise à jour avec succès',
        'sous_categorie': _serialiseSousCategorie(sous_categorie),
    }, status=200)


# ── SUPPRIMER UNE SOUS-CATÉGORIE (admin) ──────────────────────────────────────
@csrf_exempt
def supprimerSousCategorie(request):
    """Supprime une sous-catégorie (accès réservé au rôle admin)."""
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_categories'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_categories'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        sous_categorie = sousCategories.objects.get(id=data['id'])
    except sousCategories.DoesNotExist:
        return JsonResponse({'error': 'Sous-catégorie introuvable', 'error_code': 'SUBCATEGORY_NOT_FOUND'}, status=404)

    nom_sous_categorie = sous_categorie.nom
    sous_categorie.delete()
    enregistrer_audit(utilisateur, 'sous_categorie.supprimer', f"A supprimé la sous-catégorie « {nom_sous_categorie} »")

    return JsonResponse({'message': 'Sous-catégorie supprimée avec succès'}, status=200)
