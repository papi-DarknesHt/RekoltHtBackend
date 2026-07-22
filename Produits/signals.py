from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from Api.broadcast import broadcast
from .models import Produits, Categories


def _serialiser_produit(produit):
    """Sous-ensemble des champs de Produits utile au frontend pour patcher ses
    listes en place (voir _serialiseProduit, Produits/views/produitsViews.py) —
    les photos ne sont pas incluses : elles sont ajoutées séparément après la
    création (ajouterPhotosProduit) et arriveraient toujours vides ici."""
    return {
        'id':              produit.id,
        'nom':             produit.nom,
        'description':     produit.description,
        'prix':            produit.prix,
        'unite':           produit.unite,
        'est_disponible':  produit.est_disponible,
        'categorie':       {'id': produit.categorie_id, 'nom': produit.categorie.nom},
        'vendeur_id':      produit.vendeur_id,
        'departement':     produit.departement,
        'commune':         produit.commune,
        'section_comunale': produit.section_comunale,
        'adresse':         produit.adresse,
    }


# ── BROADCAST WEBSOCKET — PRODUIT CRÉÉ/MODIFIÉ ───────────────────────────────
@receiver(post_save, sender=Produits)
def broadcast_produit(sender, instance, created, **kwargs):
    """
    Notifie React en temps réel à chaque création/modification d'un produit —
    permet aux listes déjà affichées (dashboard admin, catalogue) de se mettre
    à jour sans que l'utilisateur ait besoin de rafraîchir la page, même
    logique que broadcast_utilisateur (Registration/signals.py).
    """
    event_type = "produit.created" if created else "produit.updated"
    broadcast(event_type, _serialiser_produit(instance))


# ── BROADCAST WEBSOCKET — PRODUIT SUPPRIMÉ ───────────────────────────────────
@receiver(post_delete, sender=Produits)
def broadcast_produit_supprime(sender, instance, **kwargs):
    broadcast("produit.deleted", {'id': instance.id})


# ── BROADCAST WEBSOCKET — CATÉGORIE CRÉÉE/MODIFIÉE ───────────────────────────
@receiver(post_save, sender=Categories)
def broadcast_categorie(sender, instance, created, **kwargs):
    """
    Notifie React à chaque création/modification d'une catégorie — le
    formulaire "Ajouter un produit" (choix des catégories de vente) et le
    dashboard admin s'y abonnent pour rester à jour sans rechargement.
    """
    event_type = "categorie.created" if created else "categorie.updated"
    broadcast(event_type, {
        'id':          instance.id,
        'nom':         instance.nom,
        'description': instance.description,
    })


# ── BROADCAST WEBSOCKET — CATÉGORIE SUPPRIMÉE ────────────────────────────────
@receiver(post_delete, sender=Categories)
def broadcast_categorie_supprimee(sender, instance, **kwargs):
    broadcast("categorie.deleted", {'id': instance.id})
