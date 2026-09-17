from django.db import models

class Categories(models.Model):
    id = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=255, blank=True)   # nom en français — champ canonique, toujours rempli
    # traductions optionnelles — voir Produits/views/categoriesViews.py::_serialiseCategorie
    # et RekoltHtFront/src/utils/nomLocalise.js pour la logique de repli
    # (nom_ht/nom_en vide → on retombe sur `nom`, jamais de case blanche
    # affichée au frontend même si une traduction n'a pas encore été saisie)
    nom_ht = models.CharField(max_length=255, blank=True, verbose_name="nom (kreyòl)")
    nom_en = models.CharField(max_length=255, blank=True, verbose_name="nom (anglais)")
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "categories"
        verbose_name = "categories"
        verbose_name_plural = "categories"
        ordering = ['id']

    def __str__(self):
        return self.nom
