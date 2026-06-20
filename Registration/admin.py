from django.contrib import admin
from .models import Utilisateur, Profil
from .entreprise import Entreprise


@admin.register(Utilisateur)
class UtilisateurAdmin(admin.ModelAdmin):
	list_display = ('id', 'nom', 'prenom', 'email', 'telephone', 'est_actif')
	search_fields = ('nom', 'prenom', 'email')


@admin.register(Profil)
class ProfilAdmin(admin.ModelAdmin):
	# Afficher des colonnes provenant du modèle Profil et de l'objet Entreprise lié
	list_display = ('id', 'utilisateur', 'role', 'type_vendeur', 'get_nom_entreprise', 'get_statut_verification')
	list_filter = ('role', 'type_vendeur', 'entreprise__statut_verification')
	search_fields = ('entreprise__nom_entreprise', 'utilisateur__email')

	def get_nom_entreprise(self, obj):
		try:
			return obj.entreprise.nom_entreprise
		except Exception:
			return None
	get_nom_entreprise.short_description = 'nom_entreprise'

	def get_statut_verification(self, obj):
		try:
			return obj.entreprise.statut_verification
		except Exception:
			return None
	get_statut_verification.short_description = 'statut_verification'


@admin.register(Entreprise)
class EntrepriseAdmin(admin.ModelAdmin):
	list_display = ('id', 'profil', 'nom_entreprise', 'statut_verification', 'date_maj')
	list_filter = ('statut_verification',)
	search_fields = ('nom_entreprise', 'profil__utilisateur__email')

