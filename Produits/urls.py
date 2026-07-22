from django.urls import path
from . import views

# Toutes les routes sont préfixées par /produits/ (défini dans BackendRekoltHt/urls.py)
urlpatterns = [

    # ── CATÉGORIES ─────────────────────────────────────────────────────────────
    path('categories/',            views.listerCategories),   # GET    — lister toutes les catégories
    path('categories/creer/',      views.creerCategorie),      # POST   — créer une catégorie (admin)
    path('categories/modifier/',   views.modifierCategorie),   # PUT    — modifier une catégorie (admin)
    path('categories/supprimer/',  views.supprimerCategorie),  # DELETE — supprimer une catégorie (admin)
    path('categories/choisir/',    views.choisirCategoriesVendeur),  # POST — choisir ses catégories de vente (vendeur, obligatoire)
    path('categories/mes-categories/', views.mesCategoriesVendeur),  # GET  — catégories déjà choisies par le vendeur connecté

    # ── PRODUITS ───────────────────────────────────────────────────────────────
    path('creer/',                 views.creerProduit),                 # POST   — créer un produit (vendeur)
    path('lister/',                views.listerProduits),                # GET    — lister les produits (filtres en query string)
    path('detail/',                views.detailProduit),                 # GET    — détail d'un produit (?id=)
    path('mes-produits/',          views.mesProduits),                   # GET    — produits du vendeur connecté
    path('modifier/',              views.modifierProduit),               # PUT    — modifier un produit (propriétaire)
    path('toggle-disponibilite/',  views.toggleDisponibiliteProduit),    # PUT    — basculer la disponibilité (propriétaire)
    path('supprimer/',             views.supprimerProduit),              # DELETE — supprimer un produit (propriétaire)

    # ── PHOTOS DE PRODUIT ─────────────────────────────────────────────────────
    path('photos/ajouter/',        views.ajouterPhotosProduit),   # POST   — ajouter une/plusieurs photos (propriétaire, multipart)
    path('photos/lister/',         views.listerPhotosProduit),    # GET    — lister les photos d'un produit (?produit_id=)
    path('photos/supprimer/',      views.supprimerPhotoProduit),  # DELETE — supprimer une photo (propriétaire)
]
