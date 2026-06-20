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
    path('devenir-vendeur/',    views.devenirVendeur),
]