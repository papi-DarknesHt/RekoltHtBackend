from django.contrib import admin

from .models import Conversation, Message, MessageSupport


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'participant_a', 'participant_b', 'date_maj']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'expediteur', 'date_envoi', 'lu']


@admin.register(MessageSupport)
class MessageSupportAdmin(admin.ModelAdmin):
    list_display = ['id', 'vendeur', 'date_envoi', 'admin_repondant', 'date_reponse']
    list_filter = ['date_envoi']
