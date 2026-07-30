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