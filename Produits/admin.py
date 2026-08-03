from django.contrib import admin

from .models import Categories, Produits, photo_produits, ContactProduit, SignalementProduit, SignalementVendeur, AvisProduit


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


@admin.register(ContactProduit)
class ContactProduitAdmin(admin.ModelAdmin):
    list_display = ['id', 'produit', 'acheteur', 'date_contact']
    list_filter = ['date_contact']
    search_fields = ['produit__nom', 'acheteur__nom', 'acheteur__prenom']


@admin.register(SignalementProduit)
class SignalementProduitAdmin(admin.ModelAdmin):
    list_display = ['id', 'produit', 'type_probleme', 'signaleur', 'date_signalement', 'admin_traitant']
    list_filter = ['type_probleme', 'date_signalement']
    search_fields = ['produit__nom', 'signaleur__nom', 'signaleur__prenom']


@admin.register(SignalementVendeur)
class SignalementVendeurAdmin(admin.ModelAdmin):
    list_display = ['id', 'vendeur', 'type_probleme', 'signaleur', 'date_signalement', 'admin_traitant']
    list_filter = ['type_probleme', 'date_signalement']
    search_fields = ['vendeur__nom', 'vendeur__prenom', 'signaleur__nom', 'signaleur__prenom']


@admin.register(AvisProduit)
class AvisProduitAdmin(admin.ModelAdmin):
    list_display = ['id', 'produit', 'auteur', 'note', 'date_avis']
    list_filter = ['note', 'date_avis']
    search_fields = ['produit__nom', 'auteur__nom', 'auteur__prenom']
