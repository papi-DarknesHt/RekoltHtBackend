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
    path('admin/repondus/',    views.listerMessagesAdminRepondus),  # GET  — historique de mes réponses (ou de tous, voir la vue)
    path('admin/repondus/supprimer/', views.supprimerHistoriqueMessagesSupport),  # DELETE — supprime des entrées de l'historique (admin)
    path('admin/rapport-audit/', views.genererRapportSupport),      # GET  — rapport PDF audit support (?date_debut=&date_fin=&admin_id=), Tous les droits/propriétaire
    path('admin/repondre/',    views.repondreMessageAdmin),         # POST — répondre / prendre en charge (admin)
    path('admin/migrer-vers-coffre/', views.migrerMessageVersCoffre),  # POST — bascule un message legacy vers le coffre support (admin)

    # ── ASSISTANT IA DU CHATBOT (voir ChatbotVendeur.jsx) ────────────────────
    path('chatbot/repondre/', views.chatbotRepondre),  # POST — question -> réponse IA contrainte à RekoltHt, pas de connexion requise

    # ── SIGNALEMENTS DE MESSAGES ─────────────────────────────────────────────
    path('messages/signaler/',              views.signalerMessage),                 # POST   — signaler un message (participant)
    path('messages/signalements/en-attente/', views.listerSignalementsMessagesAdmin), # GET  — file des signalements non traités (admin)
    path('messages/signalements/traites/',  views.listerSignalementsMessagesTraites), # GET  — historique des signalements traités (admin)
    path('messages/signalements/traites/supprimer/', views.supprimerHistoriqueSignalementsMessages), # DELETE — supprime des entrées de l'historique (admin)
    path('messages/signalements/traiter/',  views.traiterSignalementMessage),        # POST   — marquer comme traité sans supprimer (admin)
    path('messages/supprimer/',             views.supprimerMessageAdmin),            # DELETE — supprimer le message signalé (admin)
]
