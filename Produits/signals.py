from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from Api.broadcast import broadcast, broadcast_to_user
from .models import Produits, Categories, sousCategories, ContactProduit


def _serialiser_produit(produit):
    """Sous-ensemble des champs de Produits utile au frontend pour patcher ses
    listes en place (voir _serialiseProduit, Produits/views/produitsViews.py) —
    les photos ne sont pas incluses : elles sont ajoutées séparément après la
    création (ajouterPhotosProduit) et arriveraient toujours vides ici."""
    from .views.produitsViews import _nomVendeur

    return {
        'id':              produit.id,
        'nom':             produit.nom,
        'description':     produit.description,
        'prix':            produit.prix,
        'unitePrix':       produit.unitePrix,
        'unite_De_Mesure': produit.unite_De_Mesure,
        'est_disponible':  produit.est_disponible,
        'categorie':       {'id': produit.categorie_id, 'nom': produit.categorie.nom},
        'sous_categorie':  {'id': produit.sous_categorie_id, 'nom': produit.sous_categorie.nom} if produit.sous_categorie_id else None,
        'vendeur_id':      produit.vendeur_id,
        'vendeur_nom':     _nomVendeur(produit.vendeur),
        'departement':     produit.departement,
        'commune':         produit.commune,
        'section_comunale': produit.section_comunale,
        'adresse':         produit.adresse,
        'region':          produit.region,
        'nombre_contacts': produit.nombre_contacts,
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


# ── BROADCAST WEBSOCKET — SOUS-CATÉGORIE CRÉÉE/MODIFIÉE ──────────────────────
@receiver(post_save, sender=sousCategories)
def broadcast_sous_categorie(sender, instance, created, **kwargs):
    event_type = "sous_categorie.created" if created else "sous_categorie.updated"
    broadcast(event_type, {
        'id':           instance.id,
        'nom':          instance.nom,
        'categorie_id': instance.categorie_id,
    })


# ── BROADCAST WEBSOCKET — SOUS-CATÉGORIE SUPPRIMÉE ───────────────────────────
@receiver(post_delete, sender=sousCategories)
def broadcast_sous_categorie_supprimee(sender, instance, **kwargs):
    broadcast("sous_categorie.deleted", {'id': instance.id})


# ── BROADCAST WEBSOCKET — NOUVEAU CONTACT REÇU ───────────────────────────────
@receiver(post_save, sender=ContactProduit)
def broadcast_contact_produit(sender, instance, created, **kwargs):
    """
    Notifie uniquement le vendeur concerné (voir broadcast_to_user,
    Api/broadcast.py) qu'un nouveau contact vient d'être enregistré — alimente
    le tableau de bord vendeur en temps réel, sans diffuser cette information
    (potentiellement l'identité de l'acheteur) à "global".
    """
    if not created:
        return
    from .views.produitsViews import _nomVendeur

    broadcast_to_user(instance.produit.vendeur_id, "contact.created", {
        'id':           instance.id,
        'produit_id':   instance.produit_id,
        'produit_nom':  instance.produit.nom,
        'acheteur_id':  instance.acheteur_id,
        'acheteur_nom': _nomVendeur(instance.acheteur) if instance.acheteur_id else None,
        'date_contact': instance.date_contact.isoformat(),
    })
