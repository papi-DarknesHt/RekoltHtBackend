from django.db import models
from .produitsModels import Produits
from Registration.models import Utilisateur


class AvisProduit(models.Model):
    """Avis (note + commentaire) laissé par un acheteur ou un autre vendeur
    sur un produit — jamais par le vendeur lui-même (voir creerModifierAvis,
    Produits/views/avisViews.py). Un seul avis par (produit, auteur) : une
    nouvelle soumission modifie l'avis existant plutôt que d'en créer un
    second (contrainte d'unicité ci-dessous)."""

    NOTES = [(i, str(i)) for i in range(1, 6)]

    id      = models.AutoField(primary_key=True)
    produit = models.ForeignKey(Produits, on_delete=models.CASCADE, related_name='avis')
    # nullable : un avis reste affiché (avec son texte) même si le compte
    # auteur est supprimé plus tard — même logique que ContactProduit.acheteur
    auteur  = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='avis_donnes'
    )
    note         = models.PositiveSmallIntegerField(choices=NOTES)
    commentaire  = models.TextField(blank=True)
    date_avis    = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "avis_produits"
        verbose_name = "avis produit"
        verbose_name_plural = "avis produits"
        ordering = ['-date_avis']
        constraints = [
            models.UniqueConstraint(fields=['produit', 'auteur'], name='un_avis_par_utilisateur_et_produit'),
        ]

    def __str__(self):
        return f"{self.note}/5 — {self.produit.nom}"
