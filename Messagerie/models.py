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
    # participants ayant supprimé cette conversation de LEUR PROPRE liste (voir
    # supprimerConversationPourMoi, Messagerie/views.py) — suppression locale à
    # chacun, jamais partagée : l'autre participant continue de la voir
    # normalement avec tous ses messages. Vidé automatiquement dès qu'un
    # nouveau message est envoyé (voir envoyerMessage) : une conversation
    # supprimée par l'un des deux redevient visible en cas de reprise de
    # contact, comme dans la plupart des messageries grand public.
    supprime_pour = models.ManyToManyField(Utilisateur, related_name='conversations_supprimees', blank=True)

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
    # porte le texte en clair si chiffre=False (produit partagé sans texte, ou
    # message legacy antérieur à l'introduction du chiffrement serveur), ou le
    # ciphertext Fernet si chiffre=True — voir Messagerie/services/
    # messages_chiffrement_service.py (MESSAGES_MASTER_KEY). Remplace l'ancien
    # chiffrement de bout en bout géré par le navigateur (décision inversée
    # par le propriétaire, voir MESSAGES_MASTER_KEY, BackendRekoltHt/settings/
    # base.py) : le serveur déchiffre lui-même à la lecture (voir
    # _serialiseMessage/_contenuMessageAffiche, Messagerie/views.py) — le
    # frontend ne reçoit et n'a jamais besoin que du texte en clair final.
    contenu      = models.TextField(blank=True)
    chiffre      = models.BooleanField(default=False)
    # vestige de l'ancien chiffrement de bout en bout (IV AES-GCM côté
    # client) — Fernet (chiffrement serveur actuel) embarque son propre nonce,
    # ce champ reste toujours vide pour tout nouveau message ; conservé
    # uniquement pour ne pas casser d'éventuels messages antérieurs au
    # changement plutôt que par une migration de suppression de colonne
    iv           = models.CharField(max_length=64, blank=True, null=True)
    # partage d'une fiche produit dans le fil (voir bouton "Contacter" de
    # ProductCard.jsx, côté frontend) — optionnel, SET_NULL : si le produit
    # est supprimé plus tard, le message texte (s'il y en a) reste lisible
    produit      = models.ForeignKey(Produits, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    # réponse à un message précis du même fil (voir bouton "Répondre" du menu
    # contextuel, Messagerie.jsx) — SET_NULL : si l'original est supprimé
    # (modération admin), ce message reste lisible, juste sans sa citation.
    # Ne porte que la référence : jamais le contenu dupliqué, le frontend
    # retrouve/déchiffre l'original depuis les messages déjà chargés du fil.
    repond_a     = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='reponses')
    date_envoi   = models.DateTimeField(auto_now_add=True)
    lu           = models.BooleanField(default=False)
    # utilisateurs ayant supprimé CE message pour eux-mêmes (voir
    # supprimerMessagePourMoi, Messagerie/views.py) — suppression locale,
    # jamais partagée : contrairement à supprimerMessageAdmin (suppression
    # définitive, réservée à la modération), rien n'est retiré en base, le
    # message reste intact et visible pour l'autre participant.
    supprime_pour = models.ManyToManyField(Utilisateur, related_name='messages_supprimes', blank=True)

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

    # "supprimer" une entrée de l'historique masque seulement pour l'admin qui
    # a cliqué — voir MessageSupport.historique_masque_pour ci-dessus, même principe
    historique_masque_pour = models.ManyToManyField(
        Utilisateur, related_name='signalements_messages_historique_masques', blank=True
    )

    # justification saisie par l'admin_traitant — voir SignalementProduit.explication_decision
    # (Produits/models/signalementModel.py)
    explication_decision = models.TextField(blank=True, default='')

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
    # `contenu`/`reponse` portent le texte legacy en clair (chiffre=False),
    # OU un ciphertext — dans l'un de DEUX formats distincts, voir
    # format_chiffrement/reponse_format_chiffrement ci-dessous :
    #   - 'coffre_serveur' (par défaut pour tout nouveau message, voir
    #     contacterAdmin/repondreMessageAdmin, Messagerie/views.py) : chiffré
    #     avec la clé maître du serveur (voir Messagerie/services/
    #     support_chiffrement_service.py, SUPPORT_MASTER_KEY) — le serveur le
    #     déchiffre lui-même pour tout admin gestion_support qui le demande,
    #     MÊME si ce droit lui a été accordé après l'envoi. Décision assumée :
    #     ces messages ne sont donc PAS chiffrés de bout en bout (le serveur
    #     applicatif peut les lire), contrairement à Message (messagerie
    #     privée 1:1) qui reste, lui, entièrement E2E.
    #   - 'e2e_client' (legacy, messages envoyés avant l'introduction du
    #     coffre serveur) : ciphertext AES-GCM base64, chiffré côté client en
    #     enveloppe séparément pour chaque admin déjà configuré à l'envoi —
    #     iv_contenu/iv_reponse + cles_contenu/cles_reponse (liste de
    #     {utilisateur_id, cle_chiffree, iv}) restent nécessaires UNIQUEMENT
    #     pour ce format. Un admin absent de l'enveloppe à l'époque (pas
    #     encore configuré, ou pas encore créé) ne peut jamais les déchiffrer
    #     rétroactivement — c'est justement ce qui a motivé le passage au
    #     coffre serveur. Voir migrerMessageVersCoffre (Messagerie/views.py) :
    #     dès qu'un admin qui possède encore la bonne clé E2E parvient à lire
    #     un message 'e2e_client', son navigateur le fait automatiquement
    #     basculer vers 'coffre_serveur' — définitivement lisible par tous,
    #     ensuite, même les admins gestion_support arrivés après coup.
    FORMAT_CHIFFREMENT_CHOICES = [
        ('e2e_client',     'Chiffré de bout en bout (client, legacy)'),
        ('coffre_serveur', 'Chiffré côté serveur (coffre support)'),
    ]
    contenu                    = models.TextField()
    chiffre                    = models.BooleanField(default=False)
    format_chiffrement         = models.CharField(max_length=20, choices=FORMAT_CHIFFREMENT_CHOICES, blank=True, null=True)
    iv_contenu                 = models.CharField(max_length=64, blank=True, null=True)
    cles_contenu                = models.JSONField(blank=True, null=True)
    date_envoi                 = models.DateTimeField(auto_now_add=True)
    # NULL tant qu'aucun admin n'a répondu — c'est ce champ qui fait
    # "disparaître" le message de la file des autres admins une fois pris en charge
    admin_repondant             = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='messages_admin_repondus')
    reponse                     = models.TextField(blank=True, default='')
    reponse_format_chiffrement = models.CharField(max_length=20, choices=FORMAT_CHIFFREMENT_CHOICES, blank=True, null=True)
    iv_reponse                  = models.CharField(max_length=64, blank=True, null=True)
    cles_reponse                = models.JSONField(blank=True, null=True)
    date_reponse                = models.DateTimeField(null=True, blank=True)

    # "supprimer" une entrée de l'historique (listerMessagesAdminRepondus,
    # Messagerie/views.py) ne retire QUE ce message de la vue de l'admin qui
    # a cliqué — jamais un vrai delete() : chaque admin a son propre
    # historique (demande explicite), donc un autre admin (y compris "Tous
    # les droits") continue de le voir normalement. Même principe que
    # Conversation.supprime_pour/Message.supprime_pour ci-dessus, appliqué
    # ici à l'historique plutôt qu'à la messagerie privée.
    historique_masque_pour = models.ManyToManyField(
        Utilisateur, related_name='messages_support_historique_masques', blank=True
    )

    class Meta:
        db_table = "messages_admin"
        verbose_name = "message admin"
        verbose_name_plural = "messages admin"
        ordering = ['-date_envoi']

    def __str__(self):
        return f"MessageSupport #{self.id} de {self.vendeur_id}"
