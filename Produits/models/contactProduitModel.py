from django.db import models
from .produitsModels import Produits
from Registration.models import Utilisateur


class ContactProduit(models.Model):
    """Historique des personnes ayant manifesté leur intérêt pour un produit
    (bouton "Contacter", voir contacterProduit dans produitsViews.py) —
    Produits.nombre_contacts reste le compteur brut affiché partout ailleurs,
    cette table permet en plus au vendeur de voir QUI l'a contacté et quand,
    sur son tableau de bord."""
    id          = models.AutoField(primary_key=True)
    produit     = models.ForeignKey(Produits, on_delete=models.CASCADE, related_name='contacts')
    # nullable : le bouton "Contacter" reste accessible à un acheteur non
    # connecté (voir contacterProduit) — dans ce cas on ne peut pas savoir qui
    # c'était, seul le compteur nombre_contacts est incrémenté
    acheteur    = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='contacts_effectues')
    date_contact = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contacts_produits"
        verbose_name = "contact produit"
        verbose_name_plural = "contacts produits"
        ordering = ['-date_contact']

    def __str__(self):
        return f"{self.acheteur or 'Anonyme'} -> {self.produit.nom}"
