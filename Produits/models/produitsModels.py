from django.db import models
from django.utils.translation import gettext_lazy as _
from .categoriesModels import Categories
from Registration.models import Utilisateur


class Produits(models.Model):
    id              = models.AutoField(primary_key=True)
    vendeur         = models.ForeignKey(Utilisateur, verbose_name=_("utilisateur"), on_delete=models.CASCADE, related_name='utilisateurs')
    categorie       = models.ForeignKey(Categories, on_delete=models.CASCADE, related_name='produits')
    nom             = models.CharField(max_length=100)
    description     = models.TextField(blank=True, null=True)
    prix            = models.FloatField(blank=True, null=True)
    unite           = models.CharField(max_length=100, blank=True)
    est_disponible  = models.BooleanField(default=False)
    departement     = models.CharField(max_length=100, blank=True)
    commune         = models.CharField(max_length=100, blank=True)
    section_comunale= models.CharField(max_length=100, blank=True)
    adresse         = models.CharField(max_length=255, blank=True) 
    longitude       = models.FloatField(blank=True, null=True) 
    latitude        = models.FloatField(blank=True, null=True)
    date_ajout      = models.DateTimeField(auto_now_add=True)

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