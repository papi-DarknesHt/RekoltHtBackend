from django.db import models
from .produitsModels import Produits

class photo_produits(models.Model):
    id = models.AutoField(primary_key=True)
    produits = models.ForeignKey(Produits, on_delete=models.CASCADE, related_name='photos')
    url_photo =  models.ImageField(upload_to='photos_produits/', blank=True, null=True)
