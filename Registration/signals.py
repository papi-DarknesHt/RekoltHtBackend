from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from Api.broadcast import broadcast, broadcast_to_admins, broadcast_to_user
from .models import Utilisateur, Entreprise, Profil, DemandeVerification, DroitsAdmin, DemandeAdministrative, creer_ou_obtenir_compte_proprietaire


# ── CRÉER LE PROFIL AUTOMATIQUEMENT ──────────────────────────────────────────
# Déclenché après chaque création d'un compte : Utilisateur (ou Entreprise, qui
# en hérite via une table distincte et envoie donc son propre signal post_save).
# Vendeur/Acheteur sont des proxys de Utilisateur : ils partagent son signal.
@receiver(post_save, sender=Utilisateur)
@receiver(post_save, sender=Entreprise)
def creer_profil(sender, instance, created, **kwargs):
    """
    Crée un Profil vide automatiquement quand un compte est créé.
    Garantit que chaque utilisateur a toujours un profil.
    Une Entreprise démarre 'acheteur' comme n'importe quel compte — elle peut
    ensuite devenir 'vendeur' via Profil.convertir_en_vendeur().

    Le tout premier compte créé sur la plateforme (aucun autre Utilisateur en
    base avant celui-ci) devient automatiquement 'admin' — sans ça, personne
    n'aurait accès au dashboard admin/à la gestion des catégories sur une
    instance fraîchement déployée, faute d'un moyen de promouvoir un compte
    autrement que par un admin déjà existant. Ce même compte reçoit aussi
    d'office TOUS les droits ET est_super_super_admin (voir DroitsAdmin,
    peut_agir_sur_admin) : c'est le SEUL moyen par lequel un compte devient
    super super admin sur toute la plateforme — jamais via l'API (voir
    _appliquer_droits, Registration/views.py, qui ne touche jamais ce champ).
    """
    if not created:
        return
    est_premier_compte = Utilisateur.objects.count() == 1
    role = 'admin' if est_premier_compte else 'acheteur'
    Profil.objects.create(utilisateur=instance, role=role)
    if est_premier_compte:
        DroitsAdmin.objects.create(
            utilisateur=instance,
            super_admin=True,
            est_super_super_admin=True,
            gestion_utilisateurs=True,
            gestion_signalements=True,
            gestion_categories=True,
            gestion_support=True,
            gestion_sauvegardes=True,
            gestion_mots_de_passe=True,
        )


# ── COMPTE PROPRIÉTAIRE PAR DÉFAUT (base de données vierge) ──────────────────
def creer_compte_proprietaire_si_base_vide(sender, **kwargs):
    """
    Sur une base de données vierge (aucun Utilisateur, ex: instance tout
    juste déployée), crée automatiquement un premier compte "RekoltHT"
    (rekoltht@gmail.com) qui devient propriétaire de la plateforme — le tout
    premier Utilisateur créé reçoit automatiquement le rôle admin +
    est_super_super_admin, voir creer_profil ci-dessus. Sans ce compte
    d'amorçage, une instance fraîchement déployée n'aurait aucun moyen
    d'accéder au dashboard admin tant que quelqu'un ne s'inscrit pas
    manuellement en premier.

    Branché sur post_migrate (voir Registration/apps.py::ready), pas sur
    AppConfig.ready() directement : garantit que les tables existent déjà
    (post_migrate se déclenche APRÈS l'application des migrations).

    Ne fait que déléguer la création elle-même à
    creer_ou_obtenir_compte_proprietaire (Registration/models.py), aussi
    appelée depuis seConnecter pour recréer ce même compte à la volée s'il a
    disparu alors que d'autres comptes existent déjà — ce que ce déclencheur
    post_migrate, réservé à une base entièrement vide, ne peut pas couvrir
    seul (incident réel : le compte avait disparu, mais d'autres comptes
    existaient déjà, empêchant ce mécanisme de le recréer).
    """
    if Utilisateur.objects.exists():
        return
    creer_ou_obtenir_compte_proprietaire()


# ── BROADCAST WEBSOCKET — NOUVEL UTILISATEUR ─────────────────────────────────
# Notifie React en temps réel quand un utilisateur est créé ou modifié
@receiver(post_save, sender=Utilisateur)
@receiver(post_save, sender=Entreprise)
def broadcast_utilisateur(sender, instance, created, **kwargs):
    """
    Envoie une notification WebSocket au frontend React
    à chaque création ou modification d'un utilisateur.
    """
    event_type = "utilisateur.created" if created else "utilisateur.updated"

    # même champs que _serialiseUtilisateur (Registration/views.py) + role,
    # pour que le dashboard admin (AdminDashboard.jsx) affiche un utilisateur
    # complet dès l'évènement temps réel, sans attendre un rechargement de
    # page pour récupérer telephone/est_bloquer/role. instance.profil existe
    # normalement déjà à ce point : creer_profil (même signal post_save,
    # déclaré avant dans ce fichier donc appelé en premier) l'a créé juste
    # avant. Filet de sécurité quand même (constaté en pratique sur le
    # compte propriétaire d'amorçage, créé avant l'ajout de ce signal) :
    # sans lui, une simple sauvegarde (ex: changement de mot de passe) sur un
    # compte sans profil lève RelatedObjectDoesNotExist ("Utilisateur has no
    # profil") en plein milieu de la transaction et fait tout échouer —
    # une notification temps réel best-effort ne doit jamais pouvoir casser
    # une opération critique comme ça.
    try:
        role = instance.profil.role
    except Profil.DoesNotExist:
        role = None

    broadcast(event_type, {
        # id en entier (comme _serialiseUtilisateur, Registration/views.py) —
        # pas de str() : Utilisateur.id est un AutoField, pas un UUID, et le
        # frontend (applyListEvent.js) compare les id avec ===, donc un
        # mismatch de type casserait le rapprochement avec la liste REST
        "id":               instance.id,
        "nom":              instance.nom,
        "prenom":           instance.prenom,
        "email":            instance.email,
        "telephone":        instance.telephone,
        "est_actif":        instance.est_actif,
        "est_bloquer":      instance.est_bloquer,
        "desactive_par_signalements": instance.desactive_par_signalements,
        "date_inscription": instance.date_inscription.isoformat(),
        "role":             role,
    })


# ── BROADCAST WEBSOCKET — UTILISATEUR SUPPRIMÉ ───────────────────────────────
@receiver(post_delete, sender=Utilisateur)
def broadcast_utilisateur_supprime(sender, instance, **kwargs):
    """
    Notifie React quand un utilisateur est supprimé.
    """
    broadcast("utilisateur.deleted", {
        "id": instance.id,
    })


# ── BROADCAST WEBSOCKET — ENTREPRISE (liste admin) ───────────────────────────
@receiver(post_save, sender=Entreprise)
def broadcast_entreprise(sender, instance, created, **kwargs):
    """
    broadcast_utilisateur ci-dessus couvre déjà Entreprise (sous-classe de
    Utilisateur, même signal post_save) mais avec les champs de
    _serialiseUtilisateur — pas nom_Entreprise/secteur/logo/
    statut_verification, affichés par l'onglet "admin_entreprises" de
    ProfilAcheteur.jsx (voir AuthentificationApi.listerEntreprises). Sans ce
    second évènement, dédié, cette liste ne reflétait jamais une entreprise
    créée ou modifiée (KYC, coordonnées...) pendant que l'onglet est ouvert.
    Diffusé à "admins" uniquement : seul un admin consulte cette liste.

    Même champs que _serialiseEntreprise (Registration/views.py), sans
    `request` (pas de requête HTTP dans un signal) : l'URL du logo est donc
    rendue absolue manuellement via BACKEND_BASE_URL — même correctif que
    Messagerie/views.py::_url_absolue_media pour broadcast_message, qui
    laissait les photos ne pas s'afficher tant que l'utilisateur ne
    rechargeait pas la page.
    """
    from django.conf import settings
    logo_url = None
    if instance.logo:
        logo_url = instance.logo.url
        if not logo_url.startswith('http'):
            logo_url = settings.BACKEND_BASE_URL.rstrip('/') + logo_url

    broadcast_to_admins("entreprise.created" if created else "entreprise.updated", {
        "id":                  instance.id,
        "proprietaire_id":     instance.proprietaire_id,
        "nom_Entreprise":      instance.nom_Entreprise,
        "secteur":             instance.secteur,
        "description":         instance.description,
        "email":               instance.email,
        "telephone":           instance.telephone,
        "adresse":             instance.adresse,
        "departement":         instance.departement,
        "commune":             instance.commune,
        "section_communale":   instance.section_communale,
        "pays":                instance.pays,
        "logo":                logo_url,
        "longitude":           instance.longitude,
        "latitude":            instance.latitude,
        "est_verifiee":        instance.est_verifiee,
        "statut_verification": instance.statut_verification,
        "date_creation":       instance.date_creation.isoformat(),
        "date_maj":            instance.date_maj.isoformat(),
    })

    # en plus de l'évènement admin ci-dessus : les coordonnées d'une
    # entreprise apparaissent aussi publiquement sur la carte d'accueil (voir
    # listerVendeursCarte, Produits/views/produitsViews.py, et MapHaiti.jsx)
    # — sans un évènement global dédié, un changement de localisation depuis
    # "Modifier mon profil" ne s'y répercutait qu'après un rechargement
    # manuel de la page. Payload volontairement réduit aux champs déjà
    # publics via cet endpoint (pas email/téléphone/adresse) — évite
    # d'exposer de nouvelles données à un visiteur non connecté.
    broadcast("entreprise.localisation_maj", {
        "vendeur_id": instance.id,
        "latitude":   instance.latitude,
        "longitude":  instance.longitude,
    })


# ── BROADCAST WEBSOCKET — PROFIL MIS À JOUR ──────────────────────────────────
@receiver(post_save, sender=Profil)
def broadcast_profil(sender, instance, created, **kwargs):
    """
    Notifie React quand un profil est mis à jour.
    On ne broadcast pas la création car elle se fait
    automatiquement avec l'utilisateur.
    """
    if not created:
        broadcast("profil.updated", {
            "user_id":   instance.utilisateur.id,
            "role":      instance.role,  # ex: acheteur -> vendeur via convertir_en_vendeur()/KYC (marquer_verifie)
            "commune":   instance.commune,
            "adresse":   instance.adresse,
            "ville":     instance.ville,
            "photo":     instance.photo_profil.url if instance.photo_profil else None,
            "pays":      instance.pays,
            "latitude":  instance.latitude,
            "longitude": instance.longitude,
        })


# ── BROADCAST WEBSOCKET — PROFIL SUPPRIMÉ ────────────────────────────────────
@receiver(post_delete, sender=Profil)
def broadcast_profil_supprime(sender, instance, **kwargs):
    """
    Notifie React quand un profil est supprimé.
    """
    broadcast("profil.deleted", {
        "user_id": instance.utilisateur.id,
    })


# ── BROADCAST WEBSOCKET — DEMANDE DE VÉRIFICATION MISE À JOUR ────────────────
@receiver(post_save, sender=DemandeVerification)
def broadcast_verification(sender, instance, created, **kwargs):
    """
    Notifie React en temps réel à chaque changement sur une demande de
    vérification KYC (étape 08) — en plus de l'email envoyé par
    marquer_verifie()/marquer_echoue() (Registration/models.py). On ne
    diffuse pas la création initiale (en_attente, rien de nouveau à afficher),
    même logique que broadcast_profil ci-dessus.
    """
    if created:
        return
    broadcast("verification.updated", {
        "utilisateur_id": str(instance.utilisateur_id),
        "type_demandeur": instance.type_demandeur,
        "statut":         instance.statut,
        "motif_echec":    instance.motif_echec,
    })

    # notifie aussi le groupe "admins" (voir Api/broadcast.py) quand une
    # demande passe en revue manuelle — sans ça, AdminDashboard.jsx ne
    # découvrirait une nouvelle demande à traiter qu'au prochain rechargement
    # de page. Import différé : Registration.views importe déjà ce module
    # (signals.py) au chargement de l'app, un import en tête créerait un cycle
    if instance.statut == 'en_attente_manuelle':
        from .views import _serialiseDemandeVerification
        broadcast_to_admins("verification.revue_manuelle.created", _serialiseDemandeVerification(instance))


# ── BROADCAST WEBSOCKET — DEMANDE ADMINISTRATIVE ─────────────────────────────
@receiver(post_save, sender=DemandeAdministrative)
def broadcast_demande_administrative(sender, instance, created, **kwargs):
    """
    Diffuse au groupe "admins" (voir Api/broadcast.py::broadcast_to_admins,
    même périmètre que les signalements) — sans ça, la section "Demandes
    administratives en attente" d'AdminDashboard.jsx n'était chargée qu'une
    fois au montage : un autre admin connecté au même moment ne voyait ni une
    nouvelle demande arriver, ni une déjà traitée disparaître de sa propre
    file, tant qu'il ne rechargeait pas la page (bug constaté, demande
    explicite de réactivité sur toutes les pages).

    Une demande peut être créée directement (creerDemandeAdministrative) ou
    automatiquement par contacterNous (compte bloqué/supprimé, voir
    DemandeAdministrative docstring) — les deux passent par .objects.create()
    donc created=True ici dans les deux cas. approuver()/rejeter()
    (Registration/models.py) appellent self.save() : created=False, statut
    déjà positionné à 'approuvee'/'rejetee' à ce moment.
    """
    from .views import _serialiseDemandeAdministrative
    if created:
        broadcast_to_admins("demande_administrative.created", _serialiseDemandeAdministrative(instance))
        return
    if instance.statut != 'en_attente':
        donnees = _serialiseDemandeAdministrative(instance)
        broadcast_to_admins("demande_administrative.traitee", {'id': instance.id})
        # notifie aussi le demandeur lui-même (groupe personnel "user_<id>",
        # pas "admins") : sa page "Mes demandes" (Support/DemandeAdministrative.jsx)
        # reflète la décision sans rechargement — sans objet si le compte a
        # depuis été supprimé (utilisateur_id nul, voir DemandeAdministrative
        # ci-dessus), il n'y a alors plus personne à qui l'envoyer
        if instance.utilisateur_id:
            broadcast_to_user(instance.utilisateur_id, "demande_administrative.traitee", donnees)