from django.db import models
from .produitsModels import Produits

class photo_produits(models.Model):
    id = models.AutoField(primary_key=True)
    produits = models.ForeignKey(Produits, on_delete=models.CASCADE, related_name='photos')
    url_photo =  models.ImageField(upload_to='photos_produits/', blank=True, null=True)
    # position d'affichage parmi les photos du même produit (0 = première) —
    # ajustable après coup par le vendeur (voir reordonnerPhotosProduit,
    # Produits/views/photoProduits.py) ; 'id' en second critère de tri pour un
    # ordre stable/déterministe entre deux photos à égalité d'ordre (ex. juste
    # après un ajout, avant toute réorganisation explicite).
    ordre = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['ordre', 'id']
