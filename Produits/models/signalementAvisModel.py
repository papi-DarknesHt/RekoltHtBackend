from django.db import models
from .avisModel import AvisProduit
from Registration.models import Utilisateur


class SignalementAvis(models.Model):
    """Signalement d'un avis (contenu inapproprié, faux avis...) par un
    acheteur ou un vendeur — transmis directement aux administrateurs,
    jamais à l'auteur de l'avis concerné (même principe que
    SignalementProduit/SignalementVendeur)."""

    TYPE_PROBLEME = [
        ('contenu_inapproprie', 'Contenu inapproprié'),
        ('faux_avis',           'Faux avis / non authentique'),
        ('hors_sujet',          'Hors sujet'),
        ('autre',               'Autre'),
    ]

    id   = models.AutoField(primary_key=True)
    # SET_NULL (et non CASCADE) : un admin peut décider de supprimer l'avis
    # signalé (voir supprimerAvis, Produits/views/avisViews.py) sans perdre la
    # trace du signalement lui-même — l'explication de la décision (voir
    # explication_decision plus bas) doit rester consultable dans l'historique
    # et le rapport PDF d'audit même après suppression de l'avis. Les champs
    # *_snapshot ci-dessous conservent alors ce qu'affichait l'avis.
    avis = models.ForeignKey(AvisProduit, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements')
    # le signalement suppose un compte connecté (voir signalerAvis) — SET_NULL
    # uniquement pour survivre à la suppression éventuelle du compte signaleur,
    # même logique que SignalementProduit.signaleur
    signaleur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_avis_effectues'
    )
    type_probleme = models.CharField(max_length=30, choices=TYPE_PROBLEME)
    motif         = models.TextField()   # explication libre, obligatoire
    date_signalement = models.DateTimeField(auto_now_add=True)

    # copie figée du contenu de l'avis au moment du signalement (voir
    # signalerAvis, Produits/views/signalementsViews.py) — même principe que
    # SignalementMessage.contenu_signale (Messagerie/models.py) : permet de
    # garder ces informations lisibles dans l'historique/le rapport PDF même
    # si l'avis est ensuite supprimé (avis_id devient alors None ci-dessus)
    avis_commentaire_snapshot = models.TextField(blank=True, default='')
    avis_note_snapshot        = models.IntegerField(null=True, blank=True)
    produit_nom_snapshot      = models.CharField(max_length=255, blank=True, default='')
    auteur_avis_nom_snapshot  = models.CharField(max_length=255, blank=True, default='')

    # prise en charge — même principe "premier arrivé premier servi" que
    # SignalementProduit.admin_traitant
    admin_traitant  = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_avis_traites'
    )
    date_traitement = models.DateTimeField(null=True, blank=True)

    # "supprimer" une entrée de l'historique masque seulement pour l'admin qui
    # a cliqué — voir SignalementProduit.historique_masque_pour, même principe
    historique_masque_pour = models.ManyToManyField(
        Utilisateur, related_name='signalements_avis_historique_masques', blank=True
    )

    # justification saisie par l'admin_traitant — voir SignalementProduit.explication_decision
    explication_decision = models.TextField(blank=True, default='')

    class Meta:
        db_table = "signalements_avis"
        verbose_name = "signalement avis"
        verbose_name_plural = "signalements avis"
        ordering = ['-date_signalement']

    def __str__(self):
        return f"Signalement avis #{self.id}"
