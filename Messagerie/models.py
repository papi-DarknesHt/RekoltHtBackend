from django.db import models
from Registration.models import Utilisateur
from Produits.models import Produits


class Conversation(models.Model):
    """
    Conversation privée entre deux utilisateurs. Une seule conversation par
    paire (participant_a, participant_b) — l'ordre est normalisé par id
    croissant à la création (voir obtenir_ou_creer) pour que (A,B) et (B,A)
    ne créent jamais deux lignes distinctes.
    """
    participant_a = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='conversations_a')
    participant_b = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='conversations_b')
    date_creation = models.DateTimeField(auto_now_add=True)
    date_maj      = models.DateTimeField(auto_now=True)  # touché à chaque nouveau message, sert à trier la liste

    class Meta:
        db_table = "conversations"
        verbose_name = "conversation"
        verbose_name_plural = "conversations"
        ordering = ['-date_maj']
        constraints = [
            models.UniqueConstraint(fields=['participant_a', 'participant_b'], name='conversation_unique_paire'),
        ]

    def __str__(self):
        return f"Conversation #{self.id} ({self.participant_a_id} <-> {self.participant_b_id})"

    def autre_participant(self, utilisateur):
        """Retourne l'autre personne de la conversation (pas utilisateur)."""
        return self.participant_b if self.participant_a_id == utilisateur.id else self.participant_a

    @staticmethod
    def obtenir_ou_creer(utilisateur_a, utilisateur_b):
        """
        Retourne la conversation entre ces deux utilisateurs, la crée si elle
        n'existe pas encore. Trie par id croissant avant le get_or_create :
        sans ça, la même paire de personnes pourrait se retrouver avec deux
        conversations distinctes selon qui contacte qui en premier.
        """
        a, b = sorted([utilisateur_a, utilisateur_b], key=lambda u: u.id)
        conversation, _ = Conversation.objects.get_or_create(participant_a=a, participant_b=b)
        return conversation


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    expediteur   = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='messages_envoyes')
    contenu      = models.TextField(blank=True)
    # partage d'une fiche produit dans le fil (voir bouton "Contacter" de
    # ProductCard.jsx, côté frontend) — optionnel, SET_NULL : si le produit
    # est supprimé plus tard, le message texte (s'il y en a) reste lisible
    produit      = models.ForeignKey(Produits, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    date_envoi   = models.DateTimeField(auto_now_add=True)
    lu           = models.BooleanField(default=False)

    class Meta:
        db_table = "messages"
        verbose_name = "message"
        verbose_name_plural = "messages"
        ordering = ['date_envoi']

    def __str__(self):
        return f"Message #{self.id} de {self.expediteur_id}"


class MessageSupport(models.Model):
    """
    Message d'un vendeur adressé "aux administrateurs" (pas à un admin précis)
    — distinct de Conversation/Message (toujours entre deux personnes
    identifiées) : visible par TOUS les admins tant que personne n'a répondu
    (admin_repondant IS NULL), puis disparaît de la file des autres admins dès
    qu'un admin le prend en charge (voir repondreMessageAdmin, Messagerie/views.py).
    Nommé "MessageSupport" et pas "MessageAdmin" pour ne pas entrer en
    conflit avec la classe MessageAdmin(admin.ModelAdmin) déjà existante
    dans Messagerie/admin.py (l'admin Django pour le modèle Message).
    """
    vendeur         = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='messages_admin_envoyes')
    contenu         = models.TextField()
    date_envoi      = models.DateTimeField(auto_now_add=True)
    # NULL tant qu'aucun admin n'a répondu — c'est ce champ qui fait
    # "disparaître" le message de la file des autres admins une fois pris en charge
    admin_repondant = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages_admin_repondus')
    reponse         = models.TextField(blank=True, default='')
    date_reponse    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "messages_admin"
        verbose_name = "message admin"
        verbose_name_plural = "messages admin"
        ordering = ['-date_envoi']

    def __str__(self):
        return f"MessageSupport #{self.id} de {self.vendeur_id}"
