from django.urls import path
from . import views

# Toutes les routes sont préfixées par /Registration/ (défini dans BackendRekoltHt/urls.py)
urlpatterns = [

    # ── AUTHENTIFICATION CLASSIQUE ────────────────────────────────────────────
    path('inscription/',          views.sinscrire),           # POST  — créer un compte
    path('connexion/',            views.seConnecter),         # POST  — se connecter (email + mdp)
    path('deconnexion/',          views.seDeconnecter),       # POST  — se déconnecter (supprime le token)

    # ── AUTHENTIFICATION GOOGLE OAUTH2 ────────────────────────────────────────
    path('google/connexion/',     views.google_connection),   # POST  — connexion via Google
    path('google/inscription/',   views.google_inscription),  # POST  — inscription via Google

    # ── PROFIL UTILISATEUR ────────────────────────────────────────────────────
    path('profil/',               views.profilAfficher),       # GET   — afficher profil + utilisateur
    path('modifier-utilisateur/', views.modifierUtilisateur),  # PUT   — modifier nom, prénom, email, tél.
    path('modifier-profil/',      views.modifierProfil),       # PUT   — modifier bio, photo, adresse, rôle…
    path('modifier-mdp/',         views.modifierMotDePasse),   # PUT   — changer le mot de passe
    path('supprimer-photo-profil/', views.supprimerPhotoProfil), # DELETE — supprimer la photo de profil

    # ── ENTREPRISE ────────────────────────────────────────────────────────────
    path('entreprise/verifier/',       views.verifierEntreprise),       # GET    — vérifier si une entreprise existe (avant création)
    path('entreprise/creer/',          views.creerEntreprise),          # POST   — créer une entreprise
    path('entreprise/lister/',         views.listerEntreprises),        # GET    — lister les entreprises de l'utilisateur
    path('entreprise/modifier/',       views.modifierEntreprise),       # PUT    — modifier une entreprise
    path('entreprise/supprimer/',      views.supprimerEntreprise),      # DELETE — supprimer une entreprise
    path('entreprise/supprimer-logo/', views.supprimerLogoEntreprise),  # DELETE — supprimer uniquement le logo

    # ── ADMINISTRATION ────────────────────────────────────────────────────────
    path('admin/utilisateurs/', views.listerUtilisateursAdmin),   # GET — liste tous les utilisateurs (rôle admin requis)

    # ── RÉINITIALISATION DU MOT DE PASSE ─────────────────────────────────────
    path('reinitialisation/demander/',      views.demanderReinitialisation),      # POST — envoyer le code PIN par email
    path('reinitialisation/verifier-code/', views.verifierCodeReinitialisation),  # POST — vérifier le code sans changer le mdp
    path('reinitialisation/valider/',       views.reinitialiserMotDePasse),       # POST — changer le mdp après validation du code
]
