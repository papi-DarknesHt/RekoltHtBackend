import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..models import Produits, Categories, sousCategories, ContactProduit
from ._auth import _get_user_from_token
from .categoriesViews import _serialiseCategorie
from .sousCategoriesViews import _serialiseSousCategorie
from .photoProduits import _serialisePhoto


def _coord_ou_none(valeur):
    """Convertit une coordonnée GPS reçue du frontend en float, ou None si absente/vide."""
    if valeur in (None, ''):
        return None
    return float(valeur)


def _nomVendeur(vendeur):
    """Nom affiché du vendeur : raison sociale si c'est un compte entreprise,
    sinon prénom + nom — même détection que Profil.obtenir_utilisateur_type
    (Registration/models.py) : Entreprise est structurelle, indépendante du rôle."""
    from Registration.models import Entreprise
    entreprise = Entreprise.objects.filter(pk=vendeur.id).first()
    if entreprise:
        return entreprise.nom_Entreprise
    return f"{vendeur.prenom} {vendeur.nom}"


def _serialiseProduit(produit, request=None):
    return {
        'id':               produit.id,
        'nom':              produit.nom,
        'description':      produit.description,
        'prix':             produit.prix,
        'unitePrix':        produit.unitePrix,
        'unite_De_Mesure':  produit.unite_De_Mesure,
        'est_disponible':   produit.est_disponible,
        'categorie':        _serialiseCategorie(produit.categorie),
        'sous_categorie':   _serialiseSousCategorie(produit.sous_categorie) if produit.sous_categorie_id else None,
        'vendeur_id':       produit.vendeur_id,
        'vendeur_nom':      _nomVendeur(produit.vendeur),
        'departement':      produit.departement,
        'commune':          produit.commune,
        'section_comunale': produit.section_comunale,
        'adresse':          produit.adresse,
        'region':           produit.region,
        'coordonnees':      produit.obtenir_coordonnees_Produit(),
        'date_ajout':       produit.date_ajout.isoformat(),
        'date_maj':         produit.date_maj.isoformat(),
        'nombre_contacts':  produit.nombre_contacts,
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

    for field in ['nom', 'categorie_id', 'sous_categorie_id', 'region']:
        if not data.get(field):
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    try:
        categorie = utilisateur.profil.categories_produits.get(id=data['categorie_id'])
    except Categories.DoesNotExist:
        return JsonResponse({
            'error': "Catégorie introuvable ou non choisie parmi vos catégories de vente"
        }, status=404)

    # la sous-catégorie doit appartenir à la catégorie choisie ci-dessus —
    # sinon le classement affiché (et le filtre par sous-catégorie du
    # catalogue) serait incohérent avec la catégorie réelle du produit
    try:
        sous_categorie = sousCategories.objects.get(id=data['sous_categorie_id'], categorie=categorie)
    except sousCategories.DoesNotExist:
        return JsonResponse({
            'error': "Sous-catégorie introuvable ou ne correspondant pas à la catégorie choisie"
        }, status=404)

    unitePrix = data.get('unitePrix', 'HTG')
    if unitePrix not in dict(Produits.UNITEPRIX):
        return JsonResponse({'error': 'Le champ unitePrix est invalide'}, status=400)

    produit = Produits.objects.create(
        vendeur          = utilisateur,
        categorie        = categorie,
        sous_categorie   = sous_categorie,
        nom              = data['nom'],
        description      = data.get('description', ''),
        prix             = data.get('prix'),
        unitePrix        = unitePrix,
        unite_De_Mesure  = data.get('unite_De_Mesure', ''),
        est_disponible   = data.get('est_disponible', False),
        departement      = data.get('departement', ''),
        commune          = data.get('commune', ''),
        section_comunale = data.get('section_comunale', ''),
        adresse          = data.get('adresse', ''),
        region           = data['region'],
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

    produits = Produits.objects.select_related('categorie', 'sous_categorie', 'vendeur').all()

    categorie_id = request.GET.get('categorie_id')
    if categorie_id:
        produits = produits.filter(categorie_id=categorie_id)

    sous_categorie_id = request.GET.get('sous_categorie_id')
    if sous_categorie_id:
        produits = produits.filter(sous_categorie_id=sous_categorie_id)

    # utilisé par la page détail produit pour afficher "les autres produits de
    # ce vendeur" (voir DetailProduit.jsx côté frontend)
    vendeur_id = request.GET.get('vendeur_id')
    if vendeur_id:
        produits = produits.filter(vendeur_id=vendeur_id)

    departement = request.GET.get('departement')
    if departement:
        produits = produits.filter(departement__iexact=departement)

    commune = request.GET.get('commune')
    if commune:
        produits = produits.filter(commune__iexact=commune)

    if request.GET.get('disponible') == 'true':
        produits = produits.filter(est_disponible=True)

    # exclut un produit précis du résultat — utilisé par la page détail produit
    # pour ne pas se retrouver soi-même dans ses propres "produits similaires"
    exclure_id = request.GET.get('exclure_id')
    if exclure_id:
        produits = produits.exclude(id=exclure_id)

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
        produit = Produits.objects.select_related('categorie', 'sous_categorie', 'vendeur').get(id=produit_id)
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

    produits = Produits.objects.select_related('categorie', 'sous_categorie', 'vendeur').filter(vendeur=utilisateur)

    return JsonResponse({
        'produits': [_serialiseProduit(p, request) for p in produits],
    }, status=200)


# ── CONTACTER UN VENDEUR POUR UN PRODUIT (public) ─────────────────────────────
@csrf_exempt
def contacterProduit(request):
    """
    Enregistre qu'un visiteur a manifesté son intérêt pour un produit (bouton
    "Contacter" du catalogue, voir ProductCard.jsx) — alimente le compteur
    nombre_contacts affiché au vendeur sur son tableau de bord "Mes produits".
    Public : un acheteur non connecté doit pouvoir contacter un vendeur.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis'}, status=400)

    try:
        produit = Produits.objects.get(id=data['id'])
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable'}, status=404)

    produit.incrementer_contacts()

    # acheteur optionnel (voir _get_user_from_token : retourne None si le
    # visiteur n'est pas connecté) — permet au vendeur de voir QUI l'a
    # contacté sur son tableau de bord, sans bloquer un acheteur anonyme
    acheteur = _get_user_from_token(request)
    ContactProduit.objects.create(produit=produit, acheteur=acheteur)

    return JsonResponse({
        'message': 'Contact enregistré avec succès',
        'nombre_contacts': produit.nombre_contacts,
    }, status=200)


# ── HISTORIQUE DES CONTACTS (vendeur connecté) ────────────────────────────────
def _serialiseContact(contact):
    return {
        'id':           contact.id,
        'produit_id':   contact.produit_id,
        'produit_nom':  contact.produit.nom,
        'acheteur_id':  contact.acheteur_id,
        'acheteur_nom': _nomVendeur(contact.acheteur) if contact.acheteur_id else None,
        'date_contact': contact.date_contact.isoformat(),
    }


@csrf_exempt
def historiqueContactsVendeur(request):
    """
    Historique des personnes ayant contacté le vendeur connecté, tous produits
    confondus — alimente le tableau de bord vendeur (voir TableauDeBordVendeur.jsx
    côté frontend). Les contacts anonymes (acheteur non connecté au moment du
    clic "Contacter") apparaissent avec acheteur_nom = null.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    contacts = (
        ContactProduit.objects
        .filter(produit__vendeur=utilisateur)
        .select_related('produit', 'acheteur')
    )

    return JsonResponse({
        'contacts': [_serialiseContact(c) for c in contacts],
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

    if 'sous_categorie_id' in data:
        try:
            produit.sous_categorie = sousCategories.objects.get(id=data['sous_categorie_id'], categorie=produit.categorie)
        except sousCategories.DoesNotExist:
            return JsonResponse({
                'error': "Sous-catégorie introuvable ou ne correspondant pas à la catégorie du produit"
            }, status=404)

    if 'unitePrix' in data and data['unitePrix'] not in dict(Produits.UNITEPRIX):
        return JsonResponse({'error': 'Le champ unitePrix est invalide'}, status=400)

    for champ in ['nom', 'description', 'prix', 'unitePrix', 'unite_De_Mesure', 'est_disponible',
                  'departement', 'commune', 'section_comunale', 'adresse', 'region',
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


# ── INFOS PUBLIQUES D'UN VENDEUR (public) ─────────────────────────────────────
@csrf_exempt
def infoVendeur(request):
    """
    Retourne les informations publiques d'un vendeur pour la page détail
    produit (identité, photo/logo, bio, localisation, ancienneté, nombre de
    produits en vente, téléphone). Le téléphone est exposé pour le bouton
    "Appeler" (lien tel:) de DetailProduit.jsx ; contrairement à l'email, ce
    n'est pas une donnée d'authentification, et un vendeur agricole compte
    généralement sur l'appel direct comme canal de contact principal.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    vendeur_id = request.GET.get('vendeur_id')
    if not vendeur_id:
        return JsonResponse({'error': 'Le paramètre vendeur_id est requis'}, status=400)

    from Registration.models import Utilisateur, Entreprise

    try:
        vendeur = Utilisateur.objects.select_related('profil').get(id=vendeur_id)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Vendeur introuvable'}, status=404)

    nombre_produits = Produits.objects.filter(vendeur=vendeur, est_disponible=True).count()
    entreprise = Entreprise.objects.filter(pk=vendeur.id).first()

    if entreprise:
        photo = entreprise.logo.url if entreprise.logo else None
        nom, bio, commune, pays, telephone = (
            entreprise.nom_Entreprise, entreprise.description, entreprise.commune, entreprise.pays, entreprise.telephone,
        )
    else:
        profil = vendeur.profil
        photo = profil.photo_profil.url if profil.photo_profil else None
        nom, bio, commune, pays, telephone = (
            f"{vendeur.prenom} {vendeur.nom}", profil.bio, profil.commune, profil.pays, vendeur.telephone,
        )

    return JsonResponse({
        'vendeur': {
            'id':               vendeur.id,
            'nom':              nom,
            'est_entreprise':   entreprise is not None,
            'photo':            request.build_absolute_uri(photo) if photo else None,
            'bio':              bio,
            'commune':          commune,
            'pays':             pays,
            'telephone':        telephone,
            'est_actif':        vendeur.est_actif,
            'date_inscription': vendeur.date_inscription.isoformat(),
            'nombre_produits':  nombre_produits,
        },
    }, status=200)
