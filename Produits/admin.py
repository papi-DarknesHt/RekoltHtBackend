from django.contrib import admin

from .models import Categories, Produits, photo_produits


@admin.register(Categories)
class CategoriesAdmin(admin.ModelAdmin):
    list_display = ['id', 'nom']
    search_fields = ['nom']


@admin.register(Produits)
class ProduitsAdmin(admin.ModelAdmin):
    list_display = ['id', 'nom', 'categorie', 'vendeur', 'prix', 'est_disponible', 'date_ajout']
    list_filter = ['est_disponible', 'categorie', 'departement']
    search_fields = ['nom', 'vendeur__nom', 'vendeur__prenom']


@admin.register(photo_produits)
class PhotoProduitsAdmin(admin.ModelAdmin):
    list_display = ['id', 'produits', 'url_photo']
