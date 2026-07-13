from django.contrib import admin
from .models import (
    Utilisateur, Vendeur, Acheteur, Profil, Entreprise,
    DemandeVerification, CodeReinitialisation, Token,
)


# ── UTILISATEUR ───────────────────────────────────────────────────────────────
@admin.register(Utilisateur)
class UtilisateurAdmin(admin.ModelAdmin):
    # colonnes affichées dans la liste
    list_display  = ('id', 'prenom', 'nom', 'email', 'telephone', 'est_actif', 'est_bloquer', 'date_inscription')
    # filtres disponibles dans la barre latérale droite
    list_filter   = ('est_actif', 'est_bloquer')
    # champs indexés pour la recherche rapide (barre de recherche en haut)
    search_fields = ('email', 'nom', 'prenom')
    ordering      = ('id',)


# ── VENDEUR / ACHETEUR ────────────────────────────────────────────────────────
# Proxys d'Utilisateur : mêmes colonnes, juste une vue filtrée par rôle dans l'admin.
@admin.register(Vendeur)
class VendeurAdmin(admin.ModelAdmin):
    list_display  = ('id', 'prenom', 'nom', 'email', 'telephone', 'est_actif', 'date_inscription')
    search_fields = ('email', 'nom', 'prenom')
    ordering      = ('id',)

    def get_queryset(self, request):
        # ne montrer que les comptes ayant effectivement le rôle vendeur
        return super().get_queryset(request).filter(profil__role='vendeur')


@admin.register(Acheteur)
class AcheteurAdmin(admin.ModelAdmin):
    list_display  = ('id', 'prenom', 'nom', 'email', 'telephone', 'est_actif', 'date_inscription')
    search_fields = ('email', 'nom', 'prenom')
    ordering      = ('id',)

    def get_queryset(self, request):
        # ne montrer que les comptes ayant effectivement le rôle acheteur
        return super().get_queryset(request).filter(profil__role='acheteur')


# ── PROFIL ────────────────────────────────────────────────────────────────────
@admin.register(Profil)
class ProfilAdmin(admin.ModelAdmin):
    list_display  = ('id', 'utilisateur', 'role', 'ville', 'pays', 'date_maj')
    list_filter   = ('role', 'pays')
    # la double barre __ permet de traverser la relation OneToOne pour chercher par email
    search_fields = ('utilisateur__email', 'utilisateur__nom', 'ville')
    ordering      = ('id',)


# ── ENTREPRISE ────────────────────────────────────────────────────────────────
# Entreprise hérite d'Utilisateur : elle possède désormais son propre email/téléphone de connexion.
@admin.register(Entreprise)
class EntrepriseAdmin(admin.ModelAdmin):
    list_display  = ('id', 'nom_Entreprise', 'num_Enregistrement', 'email', 'proprietaire',
                      'secteur', 'est_verifiee', 'statut_verification', 'date_creation')
    list_filter   = ('secteur', 'est_verifiee', 'statut_verification', 'pays')
    search_fields = ('nom_Entreprise', 'num_Enregistrement', 'email')
    ordering      = ('id',)


# ── DEMANDE DE VÉRIFICATION ───────────────────────────────────────────────────
# Dashboard léger sans écran custom : les actions groupées ci-dessous permettent
# de valider/rejeter des demandes directement depuis la liste de l'admin.
@admin.register(DemandeVerification)
class DemandeVerificationAdmin(admin.ModelAdmin):
    list_display  = ('utilisateur', 'type_demandeur', 'statut', 'numero_patente_extrait', 'date_soumission')
    list_filter   = ('statut', 'type_demandeur')
    search_fields = ('utilisateur__email', 'numero_piece_extrait', 'numero_patente_extrait')
    ordering      = ('-date_soumission',)
    actions       = ['valider_selectionnees', 'rejeter_selectionnees']

    @admin.action(description="Valider les demandes sélectionnées")
    def valider_selectionnees(self, request, queryset):
        for demande in queryset:
            demande.marquer_verifie()
        self.message_user(request, f"{queryset.count()} demande(s) validée(s).")

    @admin.action(description="Rejeter les demandes sélectionnées")
    def rejeter_selectionnees(self, request, queryset):
        for demande in queryset:
            demande.marquer_echoue("Rejeté manuellement par un administrateur")
        self.message_user(request, f"{queryset.count()} demande(s) rejetée(s).")


# ── CODE DE RÉINITIALISATION ──────────────────────────────────────────────────
@admin.register(CodeReinitialisation)
class CodeReinitialisationAdmin(admin.ModelAdmin):
    list_display  = ('id', 'utilisateur', 'code', 'utilise', 'date_creation', 'date_expiration')
    # filtrer rapidement les codes déjà consommés
    list_filter   = ('utilise',)
    search_fields = ('utilisateur__email', 'code')
    ordering      = ('-date_creation',)   # les plus récents en premier


# ── TOKEN DE SESSION ──────────────────────────────────────────────────────────
@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display  = ('id', 'utilisateur', 'cle', 'date_creation')
    search_fields = ('utilisateur__email', 'cle')
    ordering      = ('-date_creation',)   # les plus récents en premier
