import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..models import Produits, Categories
from ._auth import _get_user_from_token
from .categoriesViews import _serialiseCategorie
from .photoProduits import _serialisePhoto


def _coord_ou_none(valeur):
    """Convertit une coordonnée GPS reçue du frontend en float, ou None si absente/vide."""
    if valeur in (None, ''):
        return None
    return valeur


def _serialiseProduit(produit, request=None):
    return {
        'id':               produit.id,
        'nom':              produit.nom,
        'description':      produit.description,
        'prix':             produit.prix,
        'unite':            produit.unite,
        'est_disponible':   produit.est_disponible,
        'categorie':        _serialiseCategorie(produit.categorie),
        'vendeur_id':       produit.vendeur_id,
        'departement':      produit.departement,
        'commune':          produit.commune,
        'section_comunale': produit.section_comunale,
        'adresse':          produit.adresse,
        'coordonnees':      produit.obtenir_coordonnees_Produit(),
        'date_ajout':       produit.date_ajout.isoformat(),
        'photos':           [_serialisePhoto(p, request) for p in produit.photos.all()],
    }


# ── CRÉER UN PRODUIT (vendeur) ────────────────────────────────────────────────
@csrf_exempt
def creerProduit(request):
    """Crée un produit (accès réservé au rôle vendeur, propriétaire = utilisateur connecté)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'vendeur':
        return JsonResponse({'error': "Accès réservé aux vendeurs"}, status=403)

    # choix des catégories obligatoire après validation KYC (voir
    # Profil.a_choisi_categories, Registration/models.py et
    # choisirCategoriesVendeur, Produits/views/categoriesViews.py) — un vendeur
    # ne peut pas publier de produit avant d'avoir complété cette étape
    if not utilisateur.profil.a_choisi_categories():
        return JsonResponse({
            'error': "Vous devez d'abord choisir vos catégories de produits avant de publier un produit"
        }, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    for field in ['nom', 'categorie_id']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    try:
        categorie = utilisateur.profil.categories_produits.get(id=data['categorie_id'])
    except Categories.DoesNotExist:
        return JsonResponse({
            'error': "Catégorie introuvable ou non choisie parmi vos catégories de vente"
        }, status=404)

    produit = Produits.objects.create(
        vendeur          = utilisateur,
        categorie        = categorie,
        nom              = data['nom'],
        description      = data.get('description', ''),
        prix             = data.get('prix'),
        unite            = data.get('unite', ''),
        est_disponible   = data.get('est_disponible', False),
        departement      = data.get('departement', ''),
        commune          = data.get('commune', ''),
        section_comunale = data.get('section_comunale', ''),
        adresse          = data.get('adresse', ''),
        longitude        = _coord_ou_none(data.get('longitude')),
        latitude         = _coord_ou_none(data.get('latitude')),
    )

    return JsonResponse({
        'message': 'Produit créé avec succès',
        'produit': _serialiseProduit(produit, request),
    }, status=201)


# ── LISTER LES PRODUITS (public) ──────────────────────────────────────────────
@csrf_exempt
def listerProduits(request):
    """Liste les produits, avec filtres optionnels via query string (public)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    produits = Produits.objects.select_related('categorie', 'vendeur').all()

    categorie_id = request.GET.get('categorie_id')
    if categorie_id:
        produits = produits.filter(categorie_id=categorie_id)

    departement = request.GET.get('departement')
    if departement:
        produits = produits.filter(departement__iexact=departement)

    commune = request.GET.get('commune')
    if commune:
        produits = produits.filter(commune__iexact=commune)

    if request.GET.get('disponible') == 'true':
        produits = produits.filter(est_disponible=True)

    return JsonResponse({
        'produits': [_serialiseProduit(p, request) for p in produits],
    }, status=200)


# ── DÉTAIL D'UN PRODUIT (public) ──────────────────────────────────────────────
@csrf_exempt
def detailProduit(request):
    """Retourne le détail d'un produit par son id (public)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    produit_id = request.GET.get('id')
    if not produit_id:
        return JsonResponse({'error': 'Le paramètre id est requis'}, status=400)

    try:
        produit = Produits.objects.select_related('categorie', 'vendeur').get(id=produit_id)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable'}, status=404)

    return JsonResponse({'produit': _serialiseProduit(produit, request)}, status=200)


# ── LISTER MES PRODUITS (vendeur connecté) ────────────────────────────────────
@csrf_exempt
def mesProduits(request):
    """Liste les produits du vendeur connecté."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    produits = Produits.objects.select_related('categorie').filter(vendeur=utilisateur)

    return JsonResponse({
        'produits': [_serialiseProduit(p, request) for p in produits],
    }, status=200)


# ── MODIFIER UN PRODUIT (propriétaire) ────────────────────────────────────────
@csrf_exempt
def modifierProduit(request):
    """Met à jour un produit appartenant au vendeur connecté."""
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
        return JsonResponse({'error': 'Le champ id est requis'}, status=400)

    # scoper la recherche au vendeur connecté pour empêcher la modification de produits tiers
    try:
        produit = Produits.objects.get(id=data['id'], vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable'}, status=404)

    if 'categorie_id' in data:
        try:
            produit.categorie = Categories.objects.get(id=data['categorie_id'])
        except Categories.DoesNotExist:
            return JsonResponse({'error': 'Catégorie introuvable'}, status=404)

    for champ in ['nom', 'description', 'prix', 'unite', 'est_disponible',
                  'departement', 'commune', 'section_comunale', 'adresse',
                  'longitude', 'latitude']:
        if champ in data:
            valeur = data[champ]
            if champ in ('latitude', 'longitude'):
                valeur = _coord_ou_none(valeur)
            setattr(produit, champ, valeur)

    produit.save()

    return JsonResponse({
        'message': 'Produit mis à jour avec succès',
        'produit': _serialiseProduit(produit, request),
    }, status=200)


# ── BASCULER LA DISPONIBILITÉ D'UN PRODUIT (propriétaire) ────────────────────
@csrf_exempt
def toggleDisponibiliteProduit(request):
    """Bascule est_disponible entre True et False pour un produit du vendeur connecté."""
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
        return JsonResponse({'error': 'Le champ id est requis'}, status=400)

    try:
        produit = Produits.objects.get(id=data['id'], vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable'}, status=404)

    produit.disponibilite()  # méthode du modèle : bascule est_disponible + save()

    return JsonResponse({
        'message': 'Disponibilité mise à jour avec succès',
        'produit': _serialiseProduit(produit, request),
    }, status=200)


# ── SUPPRIMER UN PRODUIT (propriétaire) ───────────────────────────────────────
@csrf_exempt
def supprimerProduit(request):
    """Supprime définitivement un produit du vendeur connecté (et ses photos)."""
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

    try:
        produit = Produits.objects.get(id=data['id'], vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable'}, status=404)

    # CASCADE supprime les lignes photo_produits en base mais pas les fichiers
    # physiques : on les supprime explicitement avant, comme
    # Entreprise.supprimer_logo()/Profil.supprimer_photo_profil (Registration/models.py)
    for photo in produit.photos.all():
        if photo.url_photo:
            photo.url_photo.delete(save=False)

    produit.delete()

    return JsonResponse({'message': 'Produit supprimé avec succès'}, status=200)
