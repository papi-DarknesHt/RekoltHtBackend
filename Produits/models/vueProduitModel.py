from django.db import models
from .produitsModels import Produits
from Registration.models import Utilisateur


class VueProduit(models.Model):
    """Une ligne par consultation de fiche produit (voir detailProduit,
    Produits/views/produitsViews.py) — journal horodaté distinct du compteur
    cumulé Produits.nombre_vues : celui-ci reste un total "depuis toujours"
    (affichage rapide sans agrégation), ce modèle sert uniquement à filtrer
    par période et tracer un graphe temporel (voir vuesViews.py). Reste
    public : detailProduit ne demande pas de connexion, visiteur peut donc
    être null."""

    id       = models.AutoField(primary_key=True)
    produit  = models.ForeignKey(Produits, on_delete=models.CASCADE, related_name='vues')
    visiteur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='vues_produits_effectuees'
    )
    date_vue = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vues_produits"
        verbose_name = "vue produit"
        verbose_name_plural = "vues produits"
        ordering = ['-date_vue']

    def __str__(self):
        return f"Vue produit #{self.produit_id} — {self.date_vue}"
