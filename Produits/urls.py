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

    # ── SOUS-CATÉGORIES ────────────────────────────────────────────────────────
    path('sous-categories/',           views.listerSousCategories),    # GET    — lister (filtre optionnel ?categorie_id=)
    path('sous-categories/creer/',     views.creerSousCategorie),      # POST   — créer une sous-catégorie (admin)
    path('sous-categories/modifier/',  views.modifierSousCategorie),   # PUT    — modifier une sous-catégorie (admin)
    path('sous-categories/supprimer/', views.supprimerSousCategorie),  # DELETE — supprimer une sous-catégorie (admin)

    # ── PRODUITS ───────────────────────────────────────────────────────────────
    path('creer/',                 views.creerProduit),                 # POST   — créer un produit (vendeur)
    path('lister/',                views.listerProduits),                # GET    — lister les produits (filtres en query string)
    path('detail/',                views.detailProduit),                 # GET    — détail d'un produit (?id=)
    path('mes-produits/',          views.mesProduits),                   # GET    — produits du vendeur connecté
    path('modifier/',              views.modifierProduit),               # PUT    — modifier un produit (propriétaire)
    path('toggle-disponibilite/',  views.toggleDisponibiliteProduit),    # PUT    — basculer la disponibilité (propriétaire)
    path('supprimer/',             views.supprimerProduit),              # DELETE — supprimer un produit (propriétaire)
    path('contacter/',             views.contacterProduit),              # POST   — enregistrer un contact acheteur->vendeur (public)
    path('contacts/historique/',   views.historiqueContactsVendeur),     # GET    — historique des contacts reçus (vendeur connecté)
    path('vendeur/',               views.infoVendeur),                   # GET    — infos publiques d'un vendeur (?vendeur_id=)
<<<<<<< Updated upstream
=======
    path('vendeurs-carte/',        views.listerVendeursCarte),           # GET    — vendeurs à positionner sur la carte d'accueil (public)
    path('admin/reactiver/',       views.reactiverProduitAdmin),         # PUT    — réactive un produit désactivé par signalements (admin)
    path('admin/desactiver/',      views.desactiverProduitAdmin),        # PUT    — désactive manuellement un produit signalé (admin)

    # ── SIGNALEMENTS ───────────────────────────────────────────────────────────
    path('signaler/',                  views.signalerProduit),           # POST   — signaler un produit incorrect/obsolète (connecté)
    path('signalements/en-attente/',   views.listerSignalementsAdmin),   # GET    — file des signalements non traités (admin)
    path('signalements/traiter/',      views.traiterSignalement),        # POST   — marquer un signalement comme traité (admin)

    # ── SIGNALEMENTS VENDEUR ───────────────────────────────────────────────────
    path('signaler-vendeur/',                  views.signalerVendeur),               # POST   — signaler un vendeur (connecté)
    path('signalements-vendeurs/en-attente/',  views.listerSignalementsVendeursAdmin),  # GET  — file des signalements vendeur non traités (admin)
    path('signalements-vendeurs/traiter/',     views.traiterSignalementVendeur),      # POST   — marquer un signalement vendeur comme traité (admin)

    # ── AVIS PRODUIT ───────────────────────────────────────────────────────────
    path('avis/creer/',      views.creerModifierAvis),   # POST   — poser/modifier son avis (connecté, sauf sur son propre produit)
    path('avis/lister/',     views.listerAvisProduit),   # GET    — avis d'un produit (?produit_id=, public)
    path('avis/supprimer/',  views.supprimerAvis),       # DELETE — supprimer un avis (auteur, ou admin quel qu'il soit)

    # ── SIGNALEMENTS AVIS ──────────────────────────────────────────────────────
    path('avis/signaler/',                  views.signalerAvis),               # POST   — signaler un avis (connecté)
    path('avis/signalements/en-attente/',   views.listerSignalementsAvisAdmin),  # GET  — file des signalements d'avis non traités (admin)
    path('avis/signalements/traiter/',      views.traiterSignalementAvis),      # POST   — marquer un signalement d'avis comme traité (admin)
>>>>>>> Stashed changes

    # ── PHOTOS DE PRODUIT ─────────────────────────────────────────────────────
    path('photos/ajouter/',        views.ajouterPhotosProduit),      # POST   — ajouter une/plusieurs photos (propriétaire, multipart)
    path('photos/lister/',         views.listerPhotosProduit),       # GET    — lister les photos d'un produit (?produit_id=)
    path('photos/reordonner/',     views.reordonnerPhotosProduit),   # PUT    — changer l'ordre d'affichage des photos (propriétaire)
    path('photos/supprimer/',      views.supprimerPhotoProduit),     # DELETE — supprimer une photo (propriétaire)
]
