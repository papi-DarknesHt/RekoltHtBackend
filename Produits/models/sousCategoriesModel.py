from django.db import models
from .categoriesModels import Categories


class sousCategories(models.Model):
    id        = models.AutoField(primary_key=True)
    categorie = models.ForeignKey(Categories, on_delete=models.CASCADE, related_name='sous_categories')
    nom       = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "sous_categories"
        verbose_name = "sous categorie"
        verbose_name_plural = "sous categories"
        ordering = ['id']

    def __str__(self):
        return self.nom
