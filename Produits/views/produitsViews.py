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
        'desactive_par_signalements': produit.desactive_par_signalements,
        'categorie':        _serialiseCategorie(produit.categorie),
        'sous_categorie':   _serialiseSousCategorie(produit.sous_categorie) if produit.sous_categorie_id else None,
        'vendeur_id':       produit.vendeur_id,
        'vendeur_nom':      _nomVendeur(produit.vendeur),
        'vendeur_telephone': produit.vendeur.telephone,
        'departement':      produit.departement,
        'commune':          produit.commune,
        'section_comunale': produit.section_comunale,
        'adresse':          produit.adresse,
        'region':           produit.region,
        'coordonnees':      produit.obtenir_coordonnees_Produit(),
        'date_ajout':       produit.date_ajout.isoformat(),
        'date_maj':         produit.date_maj.isoformat(),
        'nombre_contacts':  produit.nombre_contacts,
        'nombre_vues':      produit.nombre_vues,
        'note_moyenne':     produit.note_moyenne,
        'nombre_avis':      produit.nombre_avis,
        'photos':           [_serialisePhoto(p, request) for p in produit.photos.all()],
    }


# ── CRÉER UN PRODUIT (vendeur) ────────────────────────────────────────────────
@csrf_exempt
def creerProduit(request):
    """Crée un produit (accès réservé au rôle vendeur, propriétaire = utilisateur connecté)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role != 'vendeur':
        return JsonResponse({'error': "Accès réservé aux vendeurs", 'error_code': 'VENDEUR_ONLY'}, status=403)

    # compte suspendu suite à plus de 5 signalements pour le même motif (voir
    # signalerVendeur, Produits/views/signalementsViews.py) : plus de nouveau
    # produit possible tant qu'un admin n'a pas levé la suspension
    # (reactiverVendeurAdmin, Registration/views.py)
    if utilisateur.desactive_par_signalements:
        return JsonResponse({
            'error': "Votre compte a été suspendu suite à plusieurs signalements ; "
                     "vous ne pouvez plus publier de nouveau produit tant qu'un administrateur n'aura pas levé cette suspension."
        }, status=403)

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
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    for field in ['nom', 'categorie_id', 'sous_categorie_id']:
        if not data.get(field):
            return JsonResponse({'error': f'Le champ {field} est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': field}}, status=400)

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

    # localisation du produit : reprise de celle du vendeur (profil individuel
    # ou entreprise) plutôt que ressaisie à chaque produit (voir AjouterProduit.jsx,
    # qui n'a plus de champs de localisation) — un champ explicitement fourni
    # dans la requête reste prioritaire, pour ne pas casser d'éventuels appels API
    # existants qui préciseraient encore une localisation par produit.
    from Registration.models import Entreprise
    entreprise = Entreprise.objects.filter(pk=utilisateur.id).first()
    source_localisation = entreprise if entreprise else utilisateur.profil

    departement       = data.get('departement') or source_localisation.departement
    commune           = data.get('commune') or source_localisation.commune
    section_comunale  = data.get('section_comunale') or getattr(source_localisation, 'section_communale', '')
    adresse           = data.get('adresse') or source_localisation.adresse
    region            = data.get('region') or section_comunale or commune or departement or 'Non précisé'
    longitude         = _coord_ou_none(data.get('longitude')) if data.get('longitude') is not None else source_localisation.longitude
    latitude          = _coord_ou_none(data.get('latitude')) if data.get('latitude') is not None else source_localisation.latitude

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
        departement      = departement,
        commune          = commune,
        section_comunale = section_comunale,
        adresse          = adresse,
        region           = region,
        longitude        = longitude,
        latitude         = latitude,
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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    produit_id = request.GET.get('id')
    if not produit_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        produit = Produits.objects.select_related('categorie', 'sous_categorie', 'vendeur').get(id=produit_id)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    produit.incrementer_vues()

    return JsonResponse({'produit': _serialiseProduit(produit, request)}, status=200)


# ── LISTER MES PRODUITS (vendeur connecté) ────────────────────────────────────
@csrf_exempt
def mesProduits(request):
    """Liste les produits du vendeur connecté."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    produits = Produits.objects.select_related('categorie', 'sous_categorie', 'vendeur').filter(vendeur=utilisateur)

    return JsonResponse({
        'produits': [_serialiseProduit(p, request) for p in produits],
    }, status=200)


# ── RAPPORT STATISTIQUE PDF (vendeur connecté) ────────────────────────────────
@csrf_exempt
def statistiquesVendeurPdf(request):
    """
    Génère et retourne (binaire, réponse directe — pas de JSON) le rapport
    statistique PDF du vendeur connecté : résumé chiffré, produits les plus
    consultés/contactés, détail par produit (voir Produits/services/
    rapport_service.py). Accessible depuis le tableau de bord vendeur
    (TableauDeBordVendeur.jsx), même mécanisme de téléchargement binaire que
    previsualiser_contrat (Registration/views.py).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role != 'vendeur':
        return JsonResponse({'error': "Accès réservé aux vendeurs", 'error_code': 'VENDEUR_ONLY'}, status=403)

    from django.http import HttpResponse
    from ..services.rapport_service import generer_rapport_vendeur

    produits = Produits.objects.filter(vendeur=utilisateur)

    pdf = generer_rapport_vendeur(
        vendeur=utilisateur,
        nom_affiche=_nomVendeur(utilisateur),
        produits=produits,
        nombre_vues_profil=utilisateur.nombre_vues_profil,
    )

    return HttpResponse(pdf.read(), content_type='application/pdf')


# ── CONTACTER UN VENDEUR POUR UN PRODUIT (public) ─────────────────────────────
@csrf_exempt
def contacterProduit(request):
    """
    Enregistre qu'un acheteur connecté a manifesté son intérêt pour un
    produit (bouton "Contacter" du catalogue, voir ProductCard.jsx) —
    alimente le compteur nombre_contacts affiché au vendeur sur son tableau
    de bord "Mes produits". Nécessite d'être connecté : un visiteur anonyme
    doit d'abord se connecter avant de pouvoir contacter un vendeur.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    acheteur = _get_user_from_token(request)
    if not acheteur:
        return JsonResponse({'error': "Vous devez vous connecter pour contacter un vendeur"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        produit = Produits.objects.get(id=data['id'])
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    produit.incrementer_contacts()
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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

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

    # scoper la recherche au vendeur connecté pour empêcher la modification de produits tiers
    try:
        produit = Produits.objects.get(id=data['id'], vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    if 'categorie_id' in data:
        try:
            produit.categorie = Categories.objects.get(id=data['categorie_id'])
        except Categories.DoesNotExist:
            return JsonResponse({'error': 'Catégorie introuvable', 'error_code': 'CATEGORY_NOT_FOUND'}, status=404)

    if 'sous_categorie_id' in data:
        try:
            produit.sous_categorie = sousCategories.objects.get(id=data['sous_categorie_id'], categorie=produit.categorie)
        except sousCategories.DoesNotExist:
            return JsonResponse({
                'error': "Sous-catégorie introuvable ou ne correspondant pas à la catégorie du produit"
            }, status=404)

    if 'unitePrix' in data and data['unitePrix'] not in dict(Produits.UNITEPRIX):
        return JsonResponse({'error': 'Le champ unitePrix est invalide'}, status=400)

    # un produit désactivé automatiquement après 5 signalements ne peut être
    # remis en ligne que par un admin (voir reactiverProduitAdmin) — le
    # vendeur garde le droit de modifier les autres champs du produit
    if produit.desactive_par_signalements and data.get('est_disponible'):
        return JsonResponse({
            'error': "Ce produit a été désactivé suite à plusieurs signalements ; "
                     "seul un administrateur peut le rendre à nouveau visible."
        }, status=403)

    # même règle au niveau du compte : un vendeur suspendu (voir
    # signalerVendeur, Produits/views/signalementsViews.py) ne peut remettre
    # aucun de ses produits disponible tant qu'un admin n'a pas levé la
    # suspension (reactiverVendeurAdmin, Registration/views.py)
    if utilisateur.desactive_par_signalements and data.get('est_disponible'):
        return JsonResponse({
            'error': "Votre compte a été suspendu suite à plusieurs signalements ; "
                     "vos produits resteront indisponibles tant qu'un administrateur n'aura pas levé cette suspension."
        }, status=403)

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

    try:
        produit = Produits.objects.get(id=data['id'], vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    # même règle que modifierProduit : le vendeur ne peut pas lever une
    # désactivation décidée automatiquement suite à des signalements
    if produit.desactive_par_signalements and not produit.est_disponible:
        return JsonResponse({
            'error': "Ce produit a été désactivé suite à plusieurs signalements ; "
                     "seul un administrateur peut le rendre à nouveau visible."
        }, status=403)

    # même règle au niveau du compte (voir modifierProduit ci-dessus)
    if utilisateur.desactive_par_signalements and not produit.est_disponible:
        return JsonResponse({
            'error': "Votre compte a été suspendu suite à plusieurs signalements ; "
                     "vos produits resteront indisponibles tant qu'un administrateur n'aura pas levé cette suspension."
        }, status=403)

    produit.disponibilite()  # méthode du modèle : bascule est_disponible + save()

    return JsonResponse({
        'message': 'Disponibilité mise à jour avec succès',
        'produit': _serialiseProduit(produit, request),
    }, status=200)


# ── RÉACTIVER UN PRODUIT DÉSACTIVÉ PAR SIGNALEMENTS (admin) ──────────────────
@csrf_exempt
def reactiverProduitAdmin(request):
    """
    Lève la désactivation automatique déclenchée par 5 signalements (voir
    signalerProduit, Produits/views/signalementsViews.py) — réservé aux
    administrateurs, c'est le seul moyen de rendre un tel produit à nouveau
    visible (le vendeur en est empêché, voir modifierProduit/
    toggleDisponibiliteProduit ci-dessus).
    """
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs", 'error_code': 'ADMIN_ONLY'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        produit = Produits.objects.get(id=data['id'])
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    produit.est_disponible = True
    produit.desactive_par_signalements = False
    produit.save(update_fields=['est_disponible', 'desactive_par_signalements'])

    return JsonResponse({
        'message': 'Produit réactivé avec succès',
        'produit': _serialiseProduit(produit, request),
    }, status=200)


# ── SUPPRIMER UN PRODUIT (propriétaire) ───────────────────────────────────────
@csrf_exempt
def supprimerProduit(request):
    """Supprime définitivement un produit du vendeur connecté (et ses photos)."""
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

    try:
        produit = Produits.objects.get(id=data['id'], vendeur=utilisateur)
    except Produits.DoesNotExist:
        return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    vendeur_id = request.GET.get('vendeur_id')
    if not vendeur_id:
        return JsonResponse({'error': 'Le champ vendeur_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'vendeur_id'}}, status=400)

    from Registration.models import Utilisateur, Entreprise

    try:
        vendeur = Utilisateur.objects.select_related('profil').get(id=vendeur_id)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Vendeur introuvable', 'error_code': 'SELLER_NOT_FOUND'}, status=404)

    # comptabilise la consultation (voir Utilisateur.incrementer_vues_profil,
    # Registration/models.py) — sauf si le vendeur consulte son propre profil
    # (jeton optionnel : cette route reste publique pour un visiteur non
    # connecté, mais on ne veut pas gonfler ses propres statistiques)
    visiteur = _get_user_from_token(request)
    if not visiteur or visiteur.id != vendeur.id:
        vendeur.incrementer_vues_profil()

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
            # True = compte suspendu suite à plusieurs signalements (voir
            # signalerVendeur, Produits/views/signalementsViews.py) — permet au
            # frontend de masquer le bouton "Signaler ce vendeur" (déjà pris en
            # charge) et d'afficher un avertissement
            'desactive_par_signalements': vendeur.desactive_par_signalements,
        },
    }, status=200)


# ── VENDEURS À AFFICHER SUR LA CARTE (public) ─────────────────────────────────
@csrf_exempt
def listerVendeursCarte(request):
    """
    Vendeurs ayant au moins un produit disponible ET une position GPS connue
    (profil individuel ou entreprise selon le type de compte) — alimente le
    marqueur "vendeurs" de la carte d'accueil (voir MapHaiti.jsx). Position
    exacte (pas d'arrondi) : c'est la même précision que celle déjà utilisée
    pour le lien "Appeler"/le bouton WhatsApp sur la fiche produit.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    from Registration.models import Utilisateur, Entreprise

    vendeur_ids = (
        Produits.objects.filter(est_disponible=True)
        .values_list('vendeur_id', flat=True)
        .distinct()
    )

    vendeurs = []
    for vendeur in Utilisateur.objects.filter(id__in=vendeur_ids).select_related('profil'):
        entreprise = Entreprise.objects.filter(pk=vendeur.id).first()
        source = entreprise if entreprise else vendeur.profil

        if source.latitude is None or source.longitude is None:
            continue   # pas de position connue — ne peut pas être placé sur la carte

        nom = entreprise.nom_Entreprise if entreprise else f"{vendeur.prenom} {vendeur.nom}"
        commune = source.commune
        departement = source.departement
        # logo d'entreprise ou photo de profil individuelle — affiché comme
        # icône de marqueur sur la carte d'accueil (voir MapHaiti.jsx), avec
        # repli sur le logo du site côté frontend si aucune des deux n'existe
        fichier_photo = entreprise.logo if entreprise else vendeur.profil.photo_profil
        photo = request.build_absolute_uri(fichier_photo.url) if fichier_photo else None

        vendeurs.append({
            'vendeur_id':      vendeur.id,
            'nom':             nom,
            'photo':           photo,
            'est_entreprise':  entreprise is not None,
            'latitude':        source.latitude,
            'longitude':       source.longitude,
            'commune':         commune,
            'departement':     departement,
            'nombre_produits': Produits.objects.filter(vendeur=vendeur, est_disponible=True).count(),
        })

    return JsonResponse({'vendeurs': vendeurs}, status=200)
