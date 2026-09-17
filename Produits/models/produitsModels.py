from django.db import models
from django.utils.translation import gettext_lazy as _
from .categoriesModels import Categories
from .sousCategoriesModel import sousCategories
from Registration.models import Utilisateur


class Produits(models.Model):

    UNITEPRIX = [
        ('US',  'Dollars'),
        ('HTG', 'Gourdes'),
    ]
    id              = models.AutoField(primary_key=True)
    vendeur         = models.ForeignKey(Utilisateur, verbose_name=_("utilisateur"), on_delete=models.CASCADE, related_name='utilisateurs')
    categorie       = models.ForeignKey(Categories, on_delete=models.CASCADE, related_name='produits')
    # SET_NULL (pas CASCADE comme categorie) : la sous-catégorie est une
    # précision secondaire du classement, pas la clé structurante du produit —
    # supprimer une sous-catégorie (admin) ne doit pas supprimer les produits
    # qui l'utilisaient. null=True : les produits créés avant l'ajout de ce
    # champ n'en ont pas.
    sous_categorie  = models.ForeignKey(sousCategories, on_delete=models.SET_NULL, null=True, blank=True, related_name='produits')
    nom             = models.CharField(max_length=100)
    description     = models.TextField(blank=True, null=True)
    prix            = models.FloatField(blank=True, null=True)
    unitePrix       = models.CharField(max_length=20, choices=UNITEPRIX, default="HTG")
    unite_De_Mesure    = models.CharField(max_length=100, blank=True)
    est_disponible  = models.BooleanField(default=False)
    # passe à True automatiquement au 5e signalement (voir signalerProduit,
    # Produits/views/signalementsViews.py) et bloque alors est_disponible à
    # False côté vendeur (modifierProduit/toggleDisponibiliteProduit) — seul
    # un admin peut lever ce blocage (reactiverProduitAdmin)
    desactive_par_signalements = models.BooleanField(default=False)
    departement     = models.CharField(max_length=100, blank=True)
    commune         = models.CharField(max_length=100, blank=True)
    section_comunale= models.CharField(max_length=100, blank=True)
    adresse         = models.CharField(max_length=255, blank=True)
    region          = models.CharField(max_length=100)
    longitude       = models.FloatField(blank=True, null=True)
    latitude        = models.FloatField(blank=True, null=True)
    date_ajout      = models.DateTimeField(auto_now_add=True)
    date_maj        = models.DateTimeField(auto_now=True)
    nombre_contacts = models.PositiveIntegerField(default=0)
    # incrémenté à chaque consultation de la fiche produit (voir detailProduit,
    # Produits/views/produitsViews.py) — distinct de nombre_contacts (clic sur
    # "Contacter"/WhatsApp) : sert à l'histogramme "produits les plus
    # consultés" du tableau de bord admin (voir dashboardAdmin, Registration/views.py)
    nombre_vues     = models.PositiveIntegerField(default=0)
    # dénormalisés à partir de AvisProduit (voir Produits/signals.py) — évite
    # de recalculer une agrégation à chaque affichage de liste/carte produit
    note_moyenne    = models.FloatField(blank=True, null=True)   # null = aucun avis
    nombre_avis     = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "produits"
        verbose_name = "produits"
        verbose_name_plural = "produits"
        ordering = ['id']

    def __str__(self):
        return self.nom
    
    def disponibilite(self):
        self.est_disponible = not self.est_disponible
        self.save()

    def obtenir_coordonnees_Produit(self):
        """Retourne les coordonnées GPS sous forme de dict."""
        return {
            'longitude': self.longitude,
            'latitude':  self.latitude,
        }

    def incrementer_contacts(self):
        """Incrémente nombre_contacts de façon atomique (F() plutôt que
        self.nombre_contacts += 1) — évite qu'un clic perdu ne soit ignoré
        si deux acheteurs contactent le même produit au même moment."""
        from django.db.models import F
        self.nombre_contacts = F('nombre_contacts') + 1
        self.save(update_fields=['nombre_contacts'])
        self.refresh_from_db(fields=['nombre_contacts'])

    def incrementer_vues(self):
        """Incrémente nombre_vues de façon atomique — via .update() (pas
        .save()) pour ne PAS déclencher broadcast_produit (Produits/signals.py) :
        contrairement à un contact, une consultation de fiche produit est un
        évènement à haute fréquence (chaque visiteur, à chaque chargement de
        page) qu'il serait inutile de diffuser en temps réel à tous les
        clients connectés."""
        from django.db.models import F
        type(self).objects.filter(id=self.id).update(nombre_vues=F('nombre_vues') + 1)
        self.nombre_vues += 1  # reflète la mise à jour ci-dessus sans SELECT supplémentaire

    def recalculer_note(self):
        """Recalcule note_moyenne/nombre_avis à partir des AvisProduit liés —
        appelé par les signaux post_save/post_delete de AvisProduit (voir
        Produits/signals.py), jamais au moment de l'affichage."""
        from django.db.models import Avg, Count
        agregat = self.avis.aggregate(moyenne=Avg('note'), total=Count('id'))
        self.note_moyenne = round(agregat['moyenne'], 2) if agregat['moyenne'] is not None else None
        self.nombre_avis = agregat['total']
        self.save(update_fields=['note_moyenne', 'nombre_avis'])