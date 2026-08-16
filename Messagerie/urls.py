from django.urls import path
from . import views

# Toutes les routes sont préfixées par /messagerie/ (défini dans BackendRekoltHt/urls.py)
urlpatterns = [
    path('conversations/',          views.mesConversations),     # GET  — mes conversations, triées par activité
    path('conversations/demarrer/', views.demarrerConversation),  # POST — récupérer/créer une conversation (+ produit partagé en option)
    path('conversations/supprimer-pour-moi/', views.supprimerConversationPourMoi),  # DELETE — masquer une conversation pour soi (participant)
    path('messages/',               views.messagesConversation),  # GET  — messages d'une conversation (?conversation_id=)
    path('messages/envoyer/',       views.envoyerMessage),        # POST — envoyer un message
    path('messages/supprimer-pour-moi/', views.supprimerMessagePourMoi),  # DELETE — masquer un message pour soi (participant)

    # ── MESSAGES VENDEUR -> ADMINS (pas de destinataire précis) ─────────────
    path('admin/contacter/',   views.contacterAdmin),               # POST — envoyer un message aux admins (vendeur)
    path('admin/mes-messages/', views.mesMessagesAdmin),            # GET  — historique + réponses (vendeur)
    path('admin/en-attente/',  views.listerMessagesAdminEnAttente), # GET  — messages pas encore pris en charge (admin)
    path('admin/repondre/',    views.repondreMessageAdmin),         # POST — répondre / prendre en charge (admin)
]
