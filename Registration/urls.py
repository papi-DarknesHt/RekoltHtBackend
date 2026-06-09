from django.urls import path
from . import views

urlpatterns = [
    path('inscription/',        views.sinscrire),
    path('connexion/',          views.seConnecter),
    path('deconnexion/',        views.seDeconnecter),
    path('profil/',             views.profilAfficher),
    path('modifier-mdp/',       views.modifierMotDePasse),
]