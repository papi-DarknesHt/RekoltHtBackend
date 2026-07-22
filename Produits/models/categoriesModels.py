from django.db import models

class Categories(models.Model):
    id = models.AutoField(primary_key=True)
    nom = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "categories"
        verbose_name = "categories"
        verbose_name_plural = "categories"
        ordering = ['id']

    def __str__(self):
        return self.nom

    


