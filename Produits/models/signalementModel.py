from django.db import models
from .produitsModels import Produits
from Registration.models import Utilisateur


class SignalementProduit(models.Model):
    """Signalement d'un produit incorrect/obsolète par un acheteur ou un autre
    vendeur — transmis directement aux administrateurs (jamais au vendeur
    concerné) : c'est à l'admin de juger et, le cas échéant, de bloquer le
    compte via l'action existante (Utilisateur.bloquer(), voir
    Registration/views.py::toggleBloquerUtilisateur)."""

    TYPE_PROBLEME = [
        ('information_incorrecte', 'Informations incorrectes'),
        ('produit_obsolete',       'Produit obsolète / plus disponible'),
        ('contenu_inapproprie',    'Contenu inapproprié'),
        ('autre',                  'Autre'),
    ]

    id      = models.AutoField(primary_key=True)
    produit = models.ForeignKey(Produits, on_delete=models.CASCADE, related_name='signalements')
    # le signalement suppose un compte connecté (voir signalerProduit) — SET_NULL
    # uniquement pour survivre à la suppression éventuelle du compte signaleur,
    # même logique que ContactProduit.acheteur
    signaleur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_effectues'
    )
    type_probleme = models.CharField(max_length=30, choices=TYPE_PROBLEME)
    motif         = models.TextField()   # explication libre, obligatoire
    date_signalement = models.DateTimeField(auto_now_add=True)

    # prise en charge — même principe "premier arrivé premier servi" que
    # MessageSupport.admin_repondant (Messagerie/models.py) : un signalement
    # traité par un admin disparaît de la file des autres admins
    admin_traitant  = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='signalements_traites'
    )
    date_traitement = models.DateTimeField(null=True, blank=True)
    # True pour tous les signalements PRÉCÉDENTS d'un produit dès que le 5e
    # déclenche la désactivation automatique (voir signalerProduit,
    # Produits/views/signalementsViews.py) — exclu de la file d'attente admin
    # (listerSignalementsAdmin) pour que seul le signalement déclencheur reste
    # visible, comme entrée explicative unique plutôt que 5 doublons
    resolu_automatiquement = models.BooleanField(default=False)

    class Meta:
        db_table = "signalements_produits"
        verbose_name = "signalement produit"
        verbose_name_plural = "signalements produits"
        ordering = ['-date_signalement']

    def __str__(self):
        return f"Signalement #{self.id} — {self.produit.nom}"
