from django.urls import path
from . import views

urlpatterns = [
    path('inscription/',        views.sinscrire),
    path('connexion/',          views.seConnecter),
    path('deconnexion/',        views.seDeconnecter),
    path('profil/',             views.profilAfficher),
    path('modifier-utilisateur/', views.modifierUtilisateur),
    path('modifier-profil/',    views.modifierProfil),
    path('modifier-mdp/',       views.modifierMotDePasse),
    path('google/connexion/',   views.google_connection),   # ← connexion
    path('google/inscription/', views.google_inscription),
    path('entreprise/verifier/',       views.verifierEntreprise),
    path('entreprise/creer/',          views.creerEntreprise),
    path('entreprise/lister/',         views.listerEntreprises),
    path('entreprise/modifier/',       views.modifierEntreprise),
    path('entreprise/supprimer/',      views.supprimerEntreprise),
    path('entreprise/supprimer-logo/', views.supprimerLogoEntreprise),
    path('supprimer-photo-profil/',    views.supprimerPhotoProfil),
    path('admin/utilisateurs/',        views.listerUtilisateursAdmin),
]