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
    # porte le texte en clair pour les messages legacy (chiffre=False), ou le
    # texte chiffré (AES-GCM, base64) pour les messages envoyés depuis la mise
    # en place du chiffrement de bout en bout — voir chiffre/iv ci-dessous et
    # src/utils/e2eCrypto.js côté frontend. Le serveur ne déchiffre jamais.
    contenu      = models.TextField(blank=True)
    chiffre      = models.BooleanField(default=False)
    iv           = models.CharField(max_length=64, blank=True, null=True)  # IV (base64) du chiffrement AES-GCM, requis si chiffre=True
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


class SignalementMessage(models.Model):
    """Signalement d'un message privé par l'un des deux participants de la
    conversation — transmis directement aux administrateurs, jamais à
    l'autre participant. Même principe que SignalementProduit/SignalementVendeur
    (Produits/models/*) : l'admin décide ensuite s'il traite sans suite ou
    supprime le message (voir Messagerie/views.py::supprimerMessageAdmin —
    CASCADE sur ce modèle, donc le signalement disparaît avec le message)."""

    TYPE_PROBLEME = [
        ('contenu_inapproprie', 'Contenu inapproprié'),
        ('harcelement',         'Harcèlement / menaces'),
        ('spam',                'Spam / publicité'),
        ('arnaque_fraude',      'Arnaque / fraude'),
        ('autre',               'Autre'),
    ]

    id      = models.AutoField(primary_key=True)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='signalements')
    # le signalement suppose un compte connecté (voir signalerMessage) — SET_NULL
    # uniquement pour survivre à la suppression éventuelle du compte signaleur,
    # même logique que SignalementProduit.signaleur
    signaleur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_messages_effectues'
    )
    type_probleme = models.CharField(max_length=30, choices=TYPE_PROBLEME)
    motif         = models.TextField()
    # copie en clair du message signalé, fournie par le client du signaleur au
    # moment du signalement (il l'a déjà déchiffré pour l'afficher) — sans ça
    # l'admin ne pourrait plus rien examiner pour les messages chiffrés,
    # puisque le serveur ne peut lui-même jamais déchiffrer `message.contenu`.
    # Vide pour les messages legacy (chiffre=False) : la modération retombe
    # alors sur message.contenu directement (voir _serialiseSignalementMessage).
    contenu_signale  = models.TextField(blank=True)
    date_signalement = models.DateTimeField(auto_now_add=True)

    # prise en charge — même principe "premier arrivé premier servi" que
    # SignalementProduit.admin_traitant
    admin_traitant  = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_messages_traites'
    )
    date_traitement = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "signalements_messages"
        verbose_name = "signalement message"
        verbose_name_plural = "signalements messages"
        ordering = ['-date_signalement']

    def __str__(self):
        return f"Signalement message #{self.id} — message #{self.message_id}"


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
    # `contenu`/`reponse` portent le texte legacy en clair (chiffre=False) ou
    # le ciphertext AES-GCM base64 (chiffre=True). Comme le destinataire de
    # `contenu` n'est pas connu à l'avance (n'importe quel admin peut
    # répondre, voir listerMessagesAdminEnAttente), on ne peut pas dériver un
    # simple secret partagé 1:1 comme pour Message : la clé AES de chaque
    # champ est donc chiffrée séparément ("enveloppe") pour chaque
    # destinataire potentiel avec sa propre clé publique — cles_contenu/
    # cles_reponse contiennent une liste de {utilisateur_id, cle_chiffree, iv}.
    # Un admin qui configure sa clé E2E après l'envoi d'un message ne peut pas
    # déchiffrer les messages déjà envoyés avant lui (aucune enveloppe pour
    # lui n'existe) — seuls les nouveaux lui seront accessibles.
    contenu         = models.TextField()
    chiffre         = models.BooleanField(default=False)
    iv_contenu      = models.CharField(max_length=64, blank=True, null=True)
    cles_contenu    = models.JSONField(blank=True, null=True)
    date_envoi      = models.DateTimeField(auto_now_add=True)
    # NULL tant qu'aucun admin n'a répondu — c'est ce champ qui fait
    # "disparaître" le message de la file des autres admins une fois pris en charge
    admin_repondant = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages_admin_repondus')
    reponse         = models.TextField(blank=True, default='')
    iv_reponse      = models.CharField(max_length=64, blank=True, null=True)
    cles_reponse    = models.JSONField(blank=True, null=True)
    date_reponse    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "messages_admin"
        verbose_name = "message admin"
        verbose_name_plural = "messages admin"
        ordering = ['-date_envoi']

    def __str__(self):
        return f"MessageSupport #{self.id} de {self.vendeur_id}"
