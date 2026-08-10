from django.urls import path
from . import views

# Toutes les routes sont préfixées par /Sauvegarde/ (défini dans BackendRekoltHt/urls.py)
urlpatterns = [

    # ── PLANIFICATION ──────────────────────────────────────────────────────────
    path('configuration/', views.configuration),   # GET/PUT — lire/modifier la configuration (droit gestion_sauvegardes)

    # ── EXÉCUTIONS ─────────────────────────────────────────────────────────────
    path('historique/',                        views.historique),              # GET  — liste des exécutions passées
    path('declencher/',                        views.declencher),              # POST — lance une sauvegarde immédiatement
    path('historique/<int:id>/telecharger/',   views.historique_telecharger),  # GET  — télécharge le fichier .rhtbackup
    # pas de suppression d'historique ici (retiré intentionnellement, demande
    # explicite) : contrairement aux historiques support/signalements,
    # l'historique de sauvegarde reste toujours complet, pour tous — voir
    # AdminDashboard.jsx (bouton "Voir l'historique" -> modal en lecture seule)

    # ── RESTAURATION (réservée au super admin) ────────────────────────────────
    path('restaurer/analyser/',  views.restaurer_analyser),   # POST — aperçu sans écriture
    path('restaurer/confirmer/', views.restaurer_confirmer),  # POST — restauration réelle

    # ── GOOGLE DRIVE ───────────────────────────────────────────────────────────
    path('google/autoriser/',   views.google_autoriser),    # GET  — URL de consentement OAuth2 (super admin)
    path('google/callback/',    views.google_callback),     # GET  — appelée directement par Google, pas par le frontend
    path('google/deconnecter/', views.google_deconnecter),  # POST — oublie la connexion Drive
]
