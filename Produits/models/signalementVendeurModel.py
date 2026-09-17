from django.db import models
from Registration.models import Utilisateur


class SignalementVendeur(models.Model):
    """Signalement d'un vendeur (comportement, fiabilité...) par un acheteur ou
    un autre vendeur — transmis directement aux administrateurs, jamais au
    vendeur signalé (même principe que SignalementProduit). Au-delà de 5
    signalements pour le même motif (type_probleme), le compte est
    automatiquement suspendu (voir Utilisateur.desactive_par_signalements,
    Registration/models.py) : il ne peut plus publier de nouveau produit et
    tous ses produits existants deviennent indisponibles — seul un admin peut
    lever cette suspension (voir Registration/views.py::reactiverVendeurAdmin)."""

    TYPE_PROBLEME = [
        ('arnaque_fraude',           'Arnaque / fraude'),
        ('produits_non_conformes',   'Produits systématiquement non conformes'),
        ('comportement_inapproprie', 'Comportement inapproprié / harcèlement'),
        ('non_reponse',              'Ne répond jamais aux messages'),
        ('autre',                    'Autre'),
    ]

    id      = models.AutoField(primary_key=True)
    vendeur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='signalements_vendeurs_recus')
    # le signalement suppose un compte connecté (voir signalerVendeur) — SET_NULL
    # uniquement pour survivre à la suppression éventuelle du compte signaleur,
    # même logique que SignalementProduit.signaleur
    signaleur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_vendeurs_effectues'
    )
    type_probleme = models.CharField(max_length=30, choices=TYPE_PROBLEME)
    motif         = models.TextField()   # explication libre, obligatoire
    date_signalement = models.DateTimeField(auto_now_add=True)

    # prise en charge — même principe "premier arrivé premier servi" que
    # SignalementProduit.admin_traitant
    admin_traitant  = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_vendeurs_traites'
    )
    date_traitement = models.DateTimeField(null=True, blank=True)
    # True pour tous les signalements PRÉCÉDENTS (même vendeur + même motif)
    # dès que le seuil déclenche la suspension automatique (voir signalerVendeur,
    # Produits/views/signalementsViews.py) — exclu de la file d'attente admin
    # (listerSignalementsVendeursAdmin) pour que seul le signalement déclencheur
    # reste visible, comme entrée explicative unique plutôt que N doublons
    resolu_automatiquement = models.BooleanField(default=False)

    # "supprimer" une entrée de l'historique masque seulement pour l'admin qui
    # a cliqué — voir SignalementProduit.historique_masque_pour, même principe
    historique_masque_pour = models.ManyToManyField(
        Utilisateur, related_name='signalements_vendeurs_historique_masques', blank=True
    )

    # justification saisie par l'admin_traitant — voir SignalementProduit.explication_decision
    explication_decision = models.TextField(blank=True, default='')

    class Meta:
        db_table = "signalements_vendeurs"
        verbose_name = "signalement vendeur"
        verbose_name_plural = "signalements vendeurs"
        ordering = ['-date_signalement']

    def __str__(self):
        return f"Signalement vendeur #{self.id} — {self.vendeur_id}"
