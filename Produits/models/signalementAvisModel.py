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
    avis = models.ForeignKey(AvisProduit, on_delete=models.CASCADE, related_name='signalements')
    # le signalement suppose un compte connecté (voir signalerAvis) — SET_NULL
    # uniquement pour survivre à la suppression éventuelle du compte signaleur,
    # même logique que SignalementProduit.signaleur
    signaleur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_avis_effectues'
    )
    type_probleme = models.CharField(max_length=30, choices=TYPE_PROBLEME)
    motif         = models.TextField()   # explication libre, obligatoire
    date_signalement = models.DateTimeField(auto_now_add=True)

    # prise en charge — même principe "premier arrivé premier servi" que
    # SignalementProduit.admin_traitant
    admin_traitant  = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_avis_traites'
    )
    date_traitement = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "signalements_avis"
        verbose_name = "signalement avis"
        verbose_name_plural = "signalements avis"
        ordering = ['-date_signalement']

    def __str__(self):
        return f"Signalement avis #{self.id}"
