# ── IMPORTS ───────────────────────────────────────────────────────────────────
import hashlib       # algorithme SHA-256 — conservé UNIQUEMENT pour vérifier les hachages hérités (voir plus bas)
import re            # nettoyage du nom de fichier du contrat PDF (voir _nom_fichier_contrat)
import secrets       # générateur de nombres aléatoires cryptographiquement sûrs (sel + tokens)
import unicodedata   # retrait des accents du nom de fichier du contrat PDF (voir _nom_fichier_contrat)

from django.contrib.auth.hashers import make_password, check_password  # PBKDF2 (sel + nombreuses itérations)
from django.db    import models        # classes de base pour définir les modèles Django
from django.utils import timezone      # horodatage UTC cohérent avec USE_TZ = True
from geopy.distance import geodesic   # calcul de distance réelle entre deux points GPS


# ── FONCTIONS DE HASHAGE ──────────────────────────────────────────────────────
# Définies ici (et non dans views.py) pour éviter les imports circulaires.
#
# Audit de sécurité (demande explicite) : le schéma d'origine était un simple
# SHA-256+sel à UNE seule itération — un algorithme volontairement rapide,
# donc particulièrement mal adapté au hachage de mots de passe : en cas de
# fuite de la base, des milliards de hachages/seconde sont calculables sur un
# GPU grand public, rendant même des mots de passe moyennement complexes
# cassables très vite. Remplacé par PBKDF2 (django.contrib.auth.hashers,
# nombreuses itérations, volontairement lent) — le format Django "pbkdf2_sha256$
# <itérations>$<sel>$<hash>" se reconnaît sans ambiguïté par ses 3 caractères
# "$" (l'ancien format "sel$hash" n'en a qu'un seul, voir est_ancien_hash).
#
# Migration SANS rupture ni réinitialisation forcée : verifier_password
# continue d'accepter l'ancien format pour les comptes pas encore reconnectés
# depuis ce changement ; seConnecter (Registration/views.py) re-hache
# silencieusement avec le nouveau schéma dès qu'une connexion réussit avec un
# ancien hachage — la base se met à jour progressivement, à l'usage, sans
# script de migration ni interruption de service pour personne.
def est_ancien_hash(hashed):
    """True si `hashed` est au format hérité "sel$hash" (SHA-256, une seule
    itération) plutôt qu'un hachage PBKDF2 Django ("pbkdf2_sha256$...")."""
    return hashed.count('$') == 1 and not hashed.startswith('pbkdf2_')


def haser_password(password):
    """Hash un mot de passe en clair avec PBKDF2 (sel aléatoire + nombreuses
    itérations, voir django.contrib.auth.hashers.make_password)."""
    return make_password(password)


def verifier_password(password, hashed):
    """Vérifie qu'un mot de passe en clair correspond au hash stocké — accepte
    aussi bien le nouveau format PBKDF2 que l'ancien format hérité "sel$hash"
    (voir est_ancien_hash ci-dessus), pour ne jamais invalider un mot de
    passe déjà enregistré pendant la période de migration progressive."""
    if est_ancien_hash(hashed):
        sel, hash_stocke = hashed.split('$')
        hash_calcule = hashlib.sha256((password + sel).encode()).hexdigest()
        return hash_calcule == hash_stocke
    return check_password(password, hashed)


# au-delà de ce nombre d'avertissements (un par avis supprimé par un admin
# suite à un signalement, voir supprimerAvis, Produits/views/avisViews.py),
# le compte est bloqué automatiquement — voir Utilisateur.ajouter_avertissement
SEUIL_AVERTISSEMENTS = 10


# ── MODÈLE UTILISATEUR (CLASSE PARENTE) ───────────────────────────────────────
class Utilisateur(models.Model):

    id               = models.AutoField(primary_key=True)          # identifiant unique auto-incrémenté
    nom              = models.CharField(max_length=100)            # nom de famille (ou raison sociale pour une entreprise)
    prenom           = models.CharField(max_length=100)            # prénom
    email            = models.EmailField(unique=True)              # email unique → clé de connexion
    mot_de_passe     = models.CharField(max_length=255)            # hash SHA-256 stocké (jamais en clair)
    telephone        = models.CharField(max_length=20)             # numéro de téléphone
    date_inscription = models.DateTimeField(auto_now_add=True)     # date de création, non modifiable
    est_actif        = models.BooleanField(default=False)          # True = utilisateur en ligne sur le site
    est_bloquer      = models.BooleanField(default=False)          # True = compte suspendu par un admin
    # raison saisie par l'admin au moment du blocage (voir bloquer() plus bas)
    # — vidée au déblocage. Permet à seConnecter de la renvoyer au prochain
    # essai de connexion (demande explicite : dire précisément ce qui s'est
    # passé, pas juste "compte bloqué"), en plus de l'email déjà envoyé et de
    # la trace dans JournalAudit.
    raison_blocage   = models.TextField(blank=True, default='')
    # passe à True automatiquement dès que le compte reçoit plus de 5
    # signalements pour le même motif (voir signalerVendeur,
    # Produits/views/signalementsViews.py) : le vendeur ne peut plus publier de
    # nouveau produit et tous ses produits existants deviennent indisponibles
    # (Produits.est_disponible mis à False) — seul un admin peut lever cette
    # suspension (reactiverVendeurAdmin, Registration/views.py), ce qui rend
    # alors tous ses produits (non bannis individuellement) de nouveau
    # disponibles. Distinct de est_bloquer : ce champ-ci ne coupe pas l'accès
    # au compte, seulement la capacité à vendre.
    desactive_par_signalements = models.BooleanField(default=False)
    # incrémenté à chaque consultation du profil public d'un vendeur (voir
    # infoVendeur, Produits/views/produitsViews.py) — alimente le graphique
    # "vues du profil" du tableau de bord vendeur (voir statistiquesVendeur,
    # Produits/views/produitsViews.py)
    nombre_vues_profil = models.PositiveIntegerField(default=0)
    # incrémenté à chaque avis de ce compte supprimé par un admin suite à un
    # signalement (voir supprimerAvis, Produits/views/avisViews.py) — au-delà
    # de SEUIL_AVERTISSEMENTS, le compte est bloqué automatiquement (voir
    # ajouter_avertissement ci-dessous)
    nombre_avertissements = models.PositiveIntegerField(default=0)
    # posé à True par reinitialiserMotDePasseAdmin (Registration/views.py,
    # réservé au super super admin et aux comptes "tous les droits" agissant
    # sur un admin à droits limités — voir peut_agir_sur_admin) : le mot de
    # passe actuel reste valable (pas d'invalidation immédiate, pas de mot de
    # passe temporaire envoyé par email), mais la prochaine connexion réussie
    # doit obligatoirement passer par un changement de mot de passe avant
    # d'accéder au reste de la plateforme — voir seConnecter (inclut ce champ
    # dans la réponse) et modifierMotDePasse (le remet à False au succès)
    doit_changer_mot_de_passe = models.BooleanField(default=False)

    class Meta:
        db_table            = 'utilisateur'    # nom de la table SQL
        verbose_name        = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
        ordering            = ['id']           # tri par défaut dans les listes

    def __str__(self):
        return f"{self.prenom} {self.nom} — {self.email}"

    def modifier_est_actif(self):
        """Bascule est_actif entre True et False (indicateur de présence en ligne)."""
        self.est_actif = not self.est_actif
        self.save()

    def modifier_mot_de_passe(self, nouveau_mot_de_passe):
        """Hash le nouveau mot de passe et le sauvegarde en base."""
        # haser_password est dans models.py pour éviter l'import circulaire avec views.py
        self.mot_de_passe = haser_password(nouveau_mot_de_passe)
        self.save()

    def possede_entreprise(self):
        """Retourne True si ce compte gère au moins une entreprise enregistrée."""
        return self.entreprises.exists()   # 'entreprises' = related_name du ForeignKey proprietaire de Entreprise

    def incrementer_vues_profil(self):
        """Incrémente nombre_vues_profil de façon atomique — via .update() (pas
        .save()) pour ne PAS déclencher broadcast_utilisateur (Registration/signals.py) :
        même raisonnement que Produits.incrementer_vues (Produits/models/produitsModels.py) :
        une consultation de profil est un évènement à haute fréquence qu'il
        serait inutile de diffuser en temps réel à tous les clients connectés."""
        from django.db.models import F
        type(self).objects.filter(id=self.id).update(nombre_vues_profil=F('nombre_vues_profil') + 1)
        self.nombre_vues_profil += 1

    def bloquer(self, raison=''):
        """
        Suspend le compte (accès admin, voir Registration/views.py::
        toggleBloquerUtilisateur) — coupe complètement l'accès à la
        plateforme (demande explicite, corrige un bug réel constaté en
        production : un compte bloqué restait connecté et pouvait continuer
        à publier des produits) : invalide immédiatement toute session
        active (suppression des tokens, comme le fait déjà
        reinitialiserMotDePasseAdmin/révocation des droits admin ailleurs
        dans Registration/views.py) ET seConnecter refuse désormais toute
        nouvelle connexion pour ce compte (voir Registration/views.py),
        avec un message reprenant cette raison (demande explicite : dire à
        l'utilisateur ce qui s'est passé, pas juste qu'il est bloqué).
        Son seul recours pour demander un déblocage passe alors par le
        formulaire public "Contactez-nous" (src/pages/ContacterNous.jsx),
        qui ne nécessite pas d'être connecté.

        Un vendeur bloqué perd en plus sa capacité à vendre : tous ses
        produits déjà publiés passent indisponibles (sauvegarde individuelle,
        pas de bulk .update(), pour que chacun déclenche normalement
        broadcast_produit et reste cohérent en temps réel sur les catalogues
        déjà affichés — même principe que la suspension automatique dans
        signalerVendeur, Produits/views/signalementsViews.py) et il ne peut
        plus en publier de nouveau (voir creerProduit).
        """
        self.est_bloquer = True
        self.raison_blocage = raison
        self.save(update_fields=['est_bloquer', 'raison_blocage'])

        # révocation immédiate de toute session active — sans ça, un compte
        # déjà connecté au moment du blocage aurait continué à agir avec son
        # token existant jusqu'à expiration naturelle (jamais, ici)
        self.tokens.all().delete()

        if self.profil.role == 'vendeur':
            from Produits.models import Produits   # import différé : évite un cycle Registration <-> Produits
            for produit in Produits.objects.filter(vendeur=self, est_disponible=True):
                produit.est_disponible = False
                produit.save(update_fields=['est_disponible'])

    def debloquer(self):
        """
        Lève le blocage (accès admin). Ne réactive PAS automatiquement les
        produits d'un vendeur laissés indisponibles par bloquer() ci-dessus :
        c'est au vendeur de les rendre disponibles lui-même, un par un, une
        fois débloqué — même règle que reactiverVendeurAdmin (Registration/
        views.py) pour la suspension automatique par signalements.
        """
        self.est_bloquer = False
        self.raison_blocage = ''
        self.save(update_fields=['est_bloquer', 'raison_blocage'])

    def ajouter_avertissement(self):
        """
        Incrémente nombre_avertissements (voir supprimerAvis, Produits/views/
        avisViews.py — un avertissement par avis supprimé par un admin suite à
        un signalement). Au-delà de SEUIL_AVERTISSEMENTS, bloque
        automatiquement le compte (voir bloquer() ci-dessus). Retourne True si
        ce dépassement de seuil vient de déclencher le blocage (pour que
        l'appelant sache s'il doit prévenir l'utilisateur du blocage en plus
        de l'avertissement), False sinon.
        """
        self.nombre_avertissements += 1
        self.save(update_fields=['nombre_avertissements'])

        if self.nombre_avertissements >= SEUIL_AVERTISSEMENTS and not self.est_bloquer:
            self.bloquer()
            return True
        return False


# ── MODÈLE VENDEUR ────────────────────────────────────────────────────────────
class Vendeur(Utilisateur):
    """
    Utilisateur ayant le rôle 'vendeur' (Profil.role == 'vendeur').
    Proxy model : ne crée pas de nouvelle table, réutilise la table 'utilisateur'.
    Permet de manipuler un compte vendeur avec un type Python dédié.
    """

    class Meta:
        proxy               = True
        verbose_name        = 'Vendeur'
        verbose_name_plural  = 'Vendeurs'


# ── MODÈLE ACHETEUR ───────────────────────────────────────────────────────────
class Acheteur(Utilisateur):
    """
    Utilisateur ayant le rôle 'acheteur' (Profil.role == 'acheteur'), rôle par défaut.
    Proxy model : ne crée pas de nouvelle table, réutilise la table 'utilisateur'.
    """

    class Meta:
        proxy               = True
        verbose_name        = 'Acheteur'
        verbose_name_plural  = 'Acheteurs'


# ── MODÈLE PROFIL ─────────────────────────────────────────────────────────────
class Profil(models.Model):
    """
    Informations complémentaires liées à un Utilisateur (relation OneToOne).
    Créé automatiquement par un signal post_save à chaque nouveau compte
    (Utilisateur, Vendeur, Acheteur ou Entreprise).
    """

    # choix possibles pour le champ 'role'
    ROLES = [
        ('acheteur', 'Acheteur'),   # rôle par défaut à l'inscription, y compris pour une Entreprise
        ('vendeur',  'Vendeur'),    # peut créer une entreprise et publier des produits
        ('admin',    'Admin'),      # accès à la liste complète des utilisateurs et entreprises
    ]

    id          = models.AutoField(primary_key=True)
    utilisateur = models.OneToOneField(
                    Utilisateur,
                    on_delete    = models.CASCADE,   # supprime le profil si le compte est supprimé
                    related_name = 'profil'          # accès depuis le compte : utilisateur.profil
                  )
    bio          = models.TextField(blank=True, null=True)                               # description libre
    photo_profil = models.ImageField(upload_to='photos_profil/', blank=True, null=True)  # stockée dans /media/photos_profil/
    adresse      = models.CharField(max_length=255, blank=True)
    departement  = models.CharField(max_length=100, blank=True)   # ex. "OUEST" — voir haiti_departements.json côté frontend
    commune      = models.CharField(max_length=100, blank=True)
    section_communale = models.CharField(max_length=150, blank=True)
    ville        = models.CharField(max_length=100, blank=True)
    pays         = models.CharField(max_length=100, default='Haiti')
    longitude    = models.FloatField(blank=True, null=True)   # coordonnée GPS (axe Est-Ouest)
    latitude     = models.FloatField(blank=True, null=True)   # coordonnée GPS (axe Nord-Sud)
    date_maj     = models.DateTimeField(auto_now=True)        # mis à jour automatiquement à chaque save()

    # rôle défini comme vrai champ Django avec choices
    role         = models.CharField(
                     max_length = 20,
                     choices    = ROLES,
                     default    = 'acheteur'
                   )

    # catégories de produits que le vendeur souhaite publier — choix obligatoire
    # après validation de la vérification KYC (voir DemandeVerification.marquer_verifie
    # et Produits/views/produitsViews.py:creerProduit, qui bloque la création de
    # produit tant qu'aucune catégorie n'a été choisie). Référence par chaîne
    # ('Produits.Categories') pour éviter un import circulaire — Produits importe
    # déjà Registration.models.Utilisateur.
    categories_produits = models.ManyToManyField(
                             'Produits.Categories',
                             blank        = True,
                             related_name = 'vendeurs'
                           )

    class Meta:
        db_table            = 'profil'
        verbose_name        = 'Profil'
        verbose_name_plural = 'Profils'
        ordering            = ['id']

    def __str__(self):
        return f"Profil de {self.utilisateur.prenom} {self.utilisateur.nom}"

    def mettre_a_jour(self, **kwargs):
        """Met à jour dynamiquement les champs passés en arguments nommés."""
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.save()

    def convertir_en_vendeur(self):
        """Passe le rôle de 'acheteur' à 'vendeur'."""
        self.role = 'vendeur'
        self.save()

    def convertir_en_acheteur(self):
        """Repasse le rôle de 'vendeur' à 'acheteur'."""
        self.role = 'acheteur'
        self.save()

    def convertir_en_admin(self):
        """Nomme ce compte administrateur — voir nommerAdminUtilisateur
        (Registration/views.py), action réservée aux admins existants."""
        self.role = 'admin'
        self.save()

    def a_choisi_categories(self):
        """Vrai si le vendeur a déjà choisi au moins une catégorie de produit
        (étape obligatoire après validation KYC, voir Produits/views/produitsViews.py:creerProduit)."""
        return self.categories_produits.exists()

    def obtenir_utilisateur_type(self):
        """
        Retourne le compte casté vers sa sous-classe Python la plus précise.
        Entreprise est structurelle (indépendante du rôle) : un compte peut être
        une Entreprise tout en ayant le rôle 'acheteur' ou 'vendeur'. On vérifie
        donc d'abord si une ligne Entreprise existe pour ce compte, puis on
        retombe sur le rôle pour distinguer Vendeur/Acheteur.
        """
        entreprise = Entreprise.objects.filter(pk=self.utilisateur_id).first()
        if entreprise is not None:
            return entreprise
        if self.role == 'vendeur':
            return Vendeur.objects.get(pk=self.utilisateur_id)
        if self.role == 'acheteur':
            return Acheteur.objects.get(pk=self.utilisateur_id)
        return self.utilisateur

    def obtenir_coordonnees(self):
        """Retourne les coordonnées GPS sous forme de dict."""
        return {
            'longitude': self.longitude,
            'latitude':  self.latitude,
        }

    def supprimer_photo_profil(self):
        """Supprime le fichier physique de la photo et remet le champ à None."""
        if self.photo_profil:
            self.photo_profil.delete(save=False)   # supprime le fichier du disque sans appeler save()
            self.photo_profil = None
            self.save()

    def calculer_distance(self, autre_profil):
        """Calcule la distance en kilomètres entre ce profil et un autre via GPS."""
        if None in [self.latitude, self.longitude,
                    autre_profil.latitude, autre_profil.longitude]:
            return None   # impossible sans les deux paires de coordonnées

        coord1 = (self.latitude,           self.longitude)
        coord2 = (autre_profil.latitude,   autre_profil.longitude)
        return geodesic(coord1, coord2).kilometers   # distance sur l'ellipsoïde terrestre


# ── MODÈLE ENTREPRISE ─────────────────────────────────────────────────────────
class Entreprise(Utilisateur):
    """
    Entreprise = compte à part entière sur la plateforme (hérite d'Utilisateur :
    possède son propre email et mot de passe de connexion — table 'entreprise'
    liée 1-à-1 à 'utilisateur' via héritage multi-tables Django).
    Reste rattachée à l'utilisateur (vendeur) qui l'a enregistrée via 'proprietaire'.
    """

    # secteurs d'activité disponibles sur la plateforme
    SECTEURS = [
        ('agriculture',    'Agriculture'),
        ('transformation', 'Transformation'),
        ('distribution',   'Distribution'),
        ('autre',          'Autre'),
    ]

    # états possibles de la vérification administrative
    STATUTS_VERIFICATION = [
        ('en attente', 'En attente'),   # état initial après création
        ('valide',     'Validé'),       # approuvé par un admin
        ('rejete',     'Rejeté'),       # refusé par un admin
    ]

    proprietaire        = models.ForeignKey(
                             Utilisateur,
                             on_delete    = models.CASCADE,   # supprime l'entreprise si le propriétaire est supprimé
                             related_name = 'entreprises',    # accès : utilisateur.entreprises.all()
                             null         = True,             # une entreprise gère son propre compte par défaut
                             blank        = True,             # (proprietaire == elle-même, assigné après création)
                           )
    nom_Entreprise      = models.CharField(max_length=100, unique=True)   # nom unique sur la plateforme
    secteur             = models.CharField(
                             max_length = 20,
                             choices    = SECTEURS,
                             default    = 'agriculture'
                           )
    description         = models.TextField(blank=True, null=True)
    adresse             = models.CharField(max_length=255, blank=True)
    departement         = models.CharField(max_length=100, blank=True)
    commune             = models.CharField(max_length=100, blank=True)
    section_communale   = models.CharField(max_length=150, blank=True)
    pays                = models.CharField(max_length=100, default='Haiti')
    logo                = models.ImageField(upload_to='logos_entreprise/', blank=True, null=True)  # stocké dans /media/logos_entreprise/
    longitude           = models.FloatField(blank=True, null=True)
    latitude            = models.FloatField(blank=True, null=True)
    est_verifiee        = models.BooleanField(default=False)          # True = validée manuellement par un admin
    date_creation       = models.DateTimeField(auto_now_add=True)     # non modifiable après création
    date_maj            = models.DateTimeField(auto_now=True)         # mis à jour automatiquement
    statut_verification = models.CharField(
                               max_length = 20,
                               choices    = STATUTS_VERIFICATION,
                               default    = 'en attente'
                            )

    class Meta:
        db_table            = 'entreprise'
        verbose_name        = 'Entreprise'
        verbose_name_plural = 'Entreprises'
        ordering            = ['id']

    def __str__(self):
        return self.nom_Entreprise

    def mettre_a_jour(self, **kwargs):
        """Met à jour dynamiquement les champs passés en arguments nommés."""
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.save()

    def modifier_est_verifiee(self):
        """Bascule le statut de vérification de l'entreprise (True ↔ False)."""
        self.est_verifiee = not self.est_verifiee
        self.save()

    def obtenir_coordonnees(self):
        """Retourne les coordonnées GPS de l'entreprise sous forme de dict."""
        return {
            'longitude': self.longitude,
            'latitude':  self.latitude,
        }

    def supprimer_logo(self):
        """Supprime le fichier physique du logo et remet le champ à None."""
        if self.logo:
            self.logo.delete(save=False)   # supprime le fichier du disque sans appeler save()
            self.logo = None
            self.save()

    def calculer_distance(self, autre_entreprise):
        """Calcule la distance en kilomètres entre cette entreprise et une autre via GPS."""
        if None in [self.latitude, self.longitude,
                    autre_entreprise.latitude, autre_entreprise.longitude]:
            return None

        coord1 = (self.latitude,               self.longitude)
        coord2 = (autre_entreprise.latitude,   autre_entreprise.longitude)
        return geodesic(coord1, coord2).kilometers


def _nom_fichier_contrat(utilisateur):
    """
    Nom de fichier du contrat vendeur (pièce jointe email + fichier stocké
    servi au téléchargement, voir DemandeVerification.marquer_verifie) :
    "RekoltHT-contrat-{nom}-{prenom}-{JJMMAAAA}-{HHhMM}.pdf" (demande
    explicite). Accents retirés et caractères hors [A-Za-z0-9-] remplacés par
    un tiret — un nom de fichier avec accents/espaces pose problème dans
    certains clients email/systèmes de fichiers. Une Entreprise n'a pas de
    prénom (voir creerEntreprise, prenom='' à la création) : ce segment est
    alors simplement omis plutôt que de laisser un tiret vide. Heure au
    fuseau d'Haïti (America/Port-au-Prince, TIME_ZONE — voir
    BackendRekoltHt/settings/base.py) : timezone.now() seul renvoie
    toujours l'UTC (USE_TZ=True), décalé de l'heure d'Haïti — il faut
    explicitement convertir via timezone.localtime() (même principe déjà en
    place dans audit_rapport_service.py pour les rapports PDF).
    """
    def _nettoyer(valeur):
        sans_accents = ''.join(
            c for c in unicodedata.normalize('NFD', valeur or '') if unicodedata.category(c) != 'Mn'
        )
        return re.sub(r'[^A-Za-z0-9]+', '-', sans_accents).strip('-')

    parties = [p for p in (_nettoyer(utilisateur.nom), _nettoyer(utilisateur.prenom)) if p] or ['utilisateur']
    horodatage = timezone.localtime().strftime('%d%m%Y-%Hh%M')
    return f"RekoltHT-contrat-{'-'.join(parties)}-{horodatage}.pdf"


# ── MODÈLE DEMANDE DE VÉRIFICATION ────────────────────────────────────────────
class DemandeVerification(models.Model):
    """
    Dossier de vérification d'identité (KYC), unique par Utilisateur (OneToOne).
    Couvre à la fois le flux individuel (pièce d'identité + selfie) et le flux
    entreprise (patente) via type_demandeur, pour éviter de dupliquer un statut
    de vérification séparément dans Profil et dans Entreprise.
    """

    # type de demandeur : détermine si les champs "individuel" ou "entreprise" ci-dessous s'appliquent
    TYPE_DEMANDEUR = [
        ('individuel', 'Individuel'),
        ('entreprise', 'Entreprise'),
    ]

    # type de pièce d'identité fournie (individuel uniquement)
    TYPE_DOCUMENT = [
        ('passeport', 'Passeport'),
        ('permis',    'Permis de conduire'),
        ('cin',       "Carte d'identité"),
    ]

    # avancement du traitement de la demande
    STATUTS = [
        ('en_attente',          'En attente'),
        ('en_attente_manuelle', 'En attente de revue manuelle'),  # plus jamais produit automatiquement par le pipeline (voir Registration/views.py::_lancer_pipeline_ocr) — conservé pour un usage manuel éventuel
        ('verifie',             'Vérifié'),
        ('echoue',              'Échoué'),
    ]

    utilisateur = models.OneToOneField(
                    Utilisateur,
                    on_delete    = models.CASCADE,
                    related_name = 'demande_verification'
                  )

    type_demandeur = models.CharField(max_length=20, choices=TYPE_DEMANDEUR)

    type_document = models.CharField(max_length=20, choices=TYPE_DOCUMENT, blank=True, null=True)  # individuel uniquement

    # identifiant de la pièce SAISI par l'utilisateur (pas extrait par OCR) :
    # Paspò nimewo/N° Passeport (passeport), Numéro de carte/Nimewo kat la
    # (CIN), NIF (permis), Numéro de patente (entreprise) — comparé à la
    # valeur extraite par OCR et vérifié unique tous comptes confondus (une
    # pièce ne peut créer qu'un seul compte), voir soumettre_verification
    numero_piece_saisi = models.CharField(max_length=100, blank=True, null=True)

    document_recto = models.ImageField(upload_to='verification/documents/', blank=True, null=True)
    document_verso = models.ImageField(upload_to='verification/documents/', blank=True, null=True)  # CIN uniquement
    selfie         = models.ImageField(upload_to='verification/selfies/', blank=True, null=True)    # individuel uniquement, jamais un upload existant (liveness côté front)

    # justificatif entreprise
    certificat_patente     = models.FileField(upload_to='verification/patentes/', blank=True, null=True)  # entreprise uniquement (PDF ou image)
    numero_patente_extrait = models.CharField(max_length=100, blank=True, null=True)                      # extrait par OCR, étape 03

    # infos extraites par OCR (étape 03)
    nom_extrait             = models.CharField(max_length=150, blank=True, null=True)
    prenom_extrait          = models.CharField(max_length=150, blank=True, null=True)
    numero_piece_extrait    = models.CharField(max_length=100, blank=True, null=True)
    date_naissance_extraite = models.DateField(blank=True, null=True)
    donnees_ocr_brutes      = models.JSONField(blank=True, null=True)  # JSONField, pas ArrayField (compatible SQLite/PostgreSQL, voir étape 00)

    # résultat vérification faciale (étape 04, individuel uniquement)
    score_correspondance_visage = models.FloatField(blank=True, null=True)

    statut      = models.CharField(max_length=20, choices=STATUTS, default='en_attente')
    motif_echec = models.TextField(blank=True, null=True)

    contrat_pdf = models.FileField(upload_to='verification/contrats/', blank=True, null=True)  # généré à l'étape 06

    date_soumission = models.DateTimeField(auto_now_add=True)
    date_traitement = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table            = 'demande_verification'
        verbose_name        = 'Demande de vérification'
        verbose_name_plural = 'Demandes de vérification'
        ordering            = ['-date_soumission']

    def __str__(self):
        return f"Demande {self.type_demandeur} de {self.utilisateur.email} — {self.statut}"

    def marquer_verifie(self):
        """
        Marque la demande comme vérifiée, promeut le compte au rôle 'vendeur',
        horodate le traitement, génère le contrat vendeur (PDF signé
        électroniquement) et l'envoie par email en pièce jointe. Appelée par
        le pipeline de vérification automatique ou par
        DemandeVerificationAdmin.valider_selectionnees (Registration/admin.py).
        """
        self.statut = 'verifie'
        self.date_traitement = timezone.now()

        # la vérification KYC validée est ce qui autorise le passage
        # acheteur → vendeur (Entreprise démarre aussi 'acheteur', voir
        # Profil.convertir_en_vendeur) — jamais l'inverse, le rôle ne doit pas
        # changer tant que la demande n'est pas explicitement vérifiée
        if self.utilisateur.profil.role != 'vendeur':
            self.utilisateur.profil.convertir_en_vendeur()

        # imports différés : Registration.services.contrat_service importe ce
        # même module (Entreprise/DemandeVerification) — un import en tête de
        # fichier créerait un import circulaire au chargement de models.py
        from django.conf import settings
        from django.core.files.base import ContentFile
        from django.core.mail import EmailMessage
        from .services.contrat_service import generer_contrat

        pdf_bytes   = generer_contrat(self).read()
        nom_fichier = _nom_fichier_contrat(self.utilisateur)
        self.contrat_pdf.save(nom_fichier, ContentFile(pdf_bytes), save=False)
        self.save()

        nom_complet = f"{self.utilisateur.prenom} {self.utilisateur.nom}".strip()
        email = EmailMessage(
            subject    = "Votre vérification RekoltHt est validée",
            body       = (
                f"Bonjour {nom_complet},\n\n"
                "Votre demande de vérification a été validée. Vous trouverez "
                "ci-joint votre contrat vendeur.\n\n"
                "L'équipe RekoltHt"
            ),
            from_email = settings.DEFAULT_FROM_EMAIL,
            to         = [self.utilisateur.email],
        )
        email.attach(nom_fichier, pdf_bytes, 'application/pdf')
        # le statut est déjà enregistré (self.save() ci-dessus) : un email qui
        # échoue (SMTP mal configuré, panne temporaire...) ne doit jamais faire
        # perdre le résultat de la vérification elle-même, ni renvoyer une
        # erreur 500 au frontend qui attend juste la confirmation du statut
        try:
            email.send(fail_silently=False)
        except Exception as e:
            print(f"ERREUR envoi email de validation (demande {self.id}) :", e)

    def marquer_echoue(self, motif):
        """
        Marque la demande comme échouée avec le motif fourni, horodate le
        traitement et envoie un email expliquant le motif d'échec.
        """
        self.statut = 'echoue'
        self.motif_echec = motif
        self.date_traitement = timezone.now()
        self.save()

        from django.conf import settings   # import différé, voir marquer_verifie
        from django.core.mail import send_mail

        nom_complet = f"{self.utilisateur.prenom} {self.utilisateur.nom}".strip()
        # le statut est déjà enregistré (self.save() ci-dessus) : voir
        # marquer_verifie() pour la raison de ce try/except
        try:
            send_mail(
                subject        = "Votre vérification RekoltHt a échoué",
                message        = (
                    f"Bonjour {nom_complet},\n\n"
                    "Votre demande de vérification n'a pas pu être validée.\n\n"
                    f"Motif : {motif}\n\n"
                    "Vous pouvez soumettre une nouvelle demande à tout moment.\n\n"
                    "L'équipe RekoltHt"
                ),
                from_email     = settings.DEFAULT_FROM_EMAIL,
                recipient_list = [self.utilisateur.email],
                fail_silently  = False,
            )
        except Exception as e:
            print(f"ERREUR envoi email d'échec (demande {self.id}) :", e)


# ── MODÈLE CODE DE RÉINITIALISATION ──────────────────────────────────────────
class CodeReinitialisation(models.Model):
    """Code PIN à 4 chiffres envoyé par email pour réinitialiser un mot de passe."""

    utilisateur     = models.ForeignKey(
                         Utilisateur,
                         on_delete    = models.CASCADE,
                         related_name = 'codes_reinitialisation'   # accès : utilisateur.codes_reinitialisation.all()
                       )
    code            = models.CharField(max_length=4)               # code PIN ex: "0734"
    date_creation   = models.DateTimeField(auto_now_add=True)      # horodatage de la génération
    date_expiration = models.DateTimeField()                       # calculé à la création : now() + 15 min
    utilise         = models.BooleanField(default=False)           # True = code déjà consommé (non réutilisable)

    class Meta:
        db_table            = 'code_reinitialisation'
        verbose_name        = 'Code de réinitialisation'
        verbose_name_plural = 'Codes de réinitialisation'
        ordering            = ['-date_creation']   # les plus récents en premier

    def __str__(self):
        return f"Code {self.code} pour {self.utilisateur.email}"

    def est_valide(self):
        """Retourne True si le code n'a pas encore été utilisé et n'a pas expiré."""
        return not self.utilise and timezone.now() < self.date_expiration


# ── MODÈLE INSCRIPTION EN ATTENTE D'ACTIVATION ────────────────────────────────
class InscriptionEnAttente(models.Model):
    """
    Données d'un formulaire d'inscription classique (voir sinscrire,
    Registration/views.py) — le compte Utilisateur n'est créé qu'après clic
    sur le lien d'activation reçu par email (voir confirmerInscription).
    Demande explicite : seul un utilisateur avec un email opérationnel peut
    créer et utiliser un compte. Ne concerne pas l'inscription Google OAuth
    (google_inscription) : Google a déjà vérifié l'email, aucune activation
    supplémentaire n'y est nécessaire.
    """

    email        = models.EmailField(unique=True)   # une nouvelle demande pour le même email remplace l'ancienne
    nom          = models.CharField(max_length=100)
    prenom       = models.CharField(max_length=100)
    mot_de_passe = models.CharField(max_length=255)  # déjà haser_password(...), jamais en clair
    telephone    = models.CharField(max_length=20)
    # champs optionnels du formulaire (bio, photo_profil, adresse, commune,
    # ville, pays, role, latitude, longitude) — ré-appliqués sur le Profil à
    # la confirmation, voir confirmerInscription
    donnees_optionnelles = models.JSONField(default=dict, blank=True)

    token           = models.CharField(max_length=64, unique=True)
    date_creation   = models.DateTimeField(auto_now_add=True)
    date_expiration = models.DateTimeField()   # calculé à la création : now() + 10 min (voir sinscrire/renvoyerActivation, Registration/views.py)

    class Meta:
        db_table            = 'inscription_en_attente'
        verbose_name        = "Inscription en attente d'activation"
        verbose_name_plural = "Inscriptions en attente d'activation"
        ordering            = ['-date_creation']

    def __str__(self):
        return f"Inscription en attente — {self.email}"

    def est_valide(self):
        """Retourne True si le lien d'activation n'a pas expiré — même
        principe que CodeReinitialisation.est_valide() ci-dessus, sans champ
        `utilise` : la ligne est supprimée dès la confirmation réussie
        (voir confirmerInscription), donc son existence même signifie
        "pas encore confirmée"."""
        return timezone.now() < self.date_expiration


# ── MODÈLE TOKEN D'AUTHENTIFICATION ──────────────────────────────────────────
class Token(models.Model):
    """
    Token de session persisté en base de données.
    Remplace le dictionnaire en mémoire TOKENS = {} (non persistant entre redémarrages).
    Un seul token actif par utilisateur → connexion sur un seul navigateur à la fois.
    """

    utilisateur   = models.ForeignKey(
                        Utilisateur,
                        on_delete    = models.CASCADE,   # supprime les tokens si l'utilisateur est supprimé
                        related_name = 'tokens'          # accès : utilisateur.tokens.all()
                    )
    cle           = models.CharField(max_length=64, unique=True)   # 64 caractères hex (secrets.token_hex(32))
    date_creation = models.DateTimeField(auto_now_add=True)        # horodatage de la création du token

    class Meta:
        db_table            = 'token'
        verbose_name        = 'Token'
        verbose_name_plural = 'Tokens'

    def __str__(self):
        return f"Token de {self.utilisateur.email}"


# ── MODÈLE CLÉ DE CHIFFREMENT (messagerie de bout en bout) ───────────────────
class CleChiffrementUtilisateur(models.Model):
    """
    Matériel de chiffrement de bout en bout de la messagerie (ECDH P-256, voir
    Messagerie/models.py::Message.chiffre et MessageSupport).

    cle_publique n'est par définition pas secrète, stockée en clair.

    La clé privée, elle, n'est JAMAIS transmise ni stockée en clair. Une
    copie CHIFFRÉE en est néanmoins sauvegardée ici (cle_privee_chiffree +
    iv_cle_privee), pour qu'un utilisateur retrouve automatiquement sa
    messagerie sur un nouvel appareil sans rien avoir à saisir de plus. La
    clé d'enveloppe qui protège cette copie est dérivée via PBKDF2
    (sel_kdf/iterations_kdf) d'un secret que seul l'utilisateur peut fournir
    et que le serveur ne stocke jamais :
      - compte classique : le mot de passe du compte ;
      - compte connecté uniquement via Google : le "sub" Google (identifiant
        stable du compte, non affiché publiquement — voir google_connection/
        google_inscription ci-dessus), transmis une seule fois à la
        connexion et jamais persisté, faute de mot de passe connu.
    Dans les deux cas, ce secret est déjà obtenu par le simple fait de se
    connecter : aucune saisie supplémentaire n'est nécessaire (voir
    src/utils/e2eCrypto.js::deriverCleEnveloppe et
    src/api/e2eStore.js::garantirCleE2E). Le serveur ne voit donc jamais la
    clé privée en clair, seulement un blob qu'il est incapable de déchiffrer
    lui-même.

    En cas de mot de passe oublié réinitialisé par code (l'ancien mot de
    passe n'est alors jamais connu, donc impossible de re-envelopper la clé
    existante) : cette ligne est supprimée par reinitialiserMotDePasse et une
    nouvelle paire de clés est régénérée automatiquement à la prochaine
    connexion. En cas de changement de mot de passe classique (l'ancien ET
    le nouveau sont connus), la clé privée est simplement ré-enveloppée avec
    le nouveau mot de passe dans la même requête (voir modifierMotDePasse),
    sans rien perdre.
    """

    utilisateur = models.OneToOneField(
                    Utilisateur,
                    on_delete    = models.CASCADE,
                    related_name = 'cle_chiffrement'
                  )

    cle_publique = models.TextField()   # JWK JSON de la clé publique ECDH

    # copie de secours de la clé privée (JWK JSON), chiffrée en AES-GCM avec
    # une clé dérivée du mot de passe (ou du sub Google) du compte — reste
    # nul tant qu'aucun appareil n'a encore synchronisé sa sauvegarde
    cle_privee_chiffree = models.TextField(blank=True, null=True)
    iv_cle_privee       = models.CharField(max_length=64, blank=True, null=True)
    sel_kdf             = models.CharField(max_length=64, blank=True, null=True)
    iterations_kdf      = models.PositiveIntegerField(blank=True, null=True)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_maj      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table            = 'cle_chiffrement_utilisateur'
        verbose_name        = 'Clé de chiffrement'
        verbose_name_plural = 'Clés de chiffrement'

    def __str__(self):
        return f"Clé de chiffrement de {self.utilisateur.email}"


# ── MODÈLE DROITS ADMIN ────────────────────────────────────────────────────────
class DroitsAdmin(models.Model):
    """
    Droits granulaires d'un compte administrateur (Profil.role == 'admin').
    Un compte admin sans DroitsAdmin n'a aucun droit — un admin n'a donc accès
    à une fonctionnalité que si ce droit lui a été explicitement attribué (ou
    si super_admin est vrai, qui les implique tous). Voir verifier_droit_admin
    ci-dessous, utilisé par toutes les vues admin de tout le backend
    (Registration/Produits/Messagerie), et enregistrer_audit qui journalise
    chaque action mutante effectuée grâce à un de ces droits (voir JournalAudit).
    """

    utilisateur = models.OneToOneField(
                    Utilisateur,
                    on_delete    = models.CASCADE,
                    related_name = 'droits_admin'
                  )

    # implique TOUS les droits ci-dessous, et donne accès à la gestion des
    # autres admins (créer, modifier leurs droits, bloquer, révoquer, réinitialiser
    # leur mot de passe — voir listerAdmins/creerAdmin/promouvoirAdmin/
    # modifierDroitsAdmin/revoquerAdmin/reinitialiserMotDePasseAdmin,
    # Registration/views.py). Affiché "Tous les droits" côté frontend (pas
    # "Super admin" — ce libellé est réservé à est_super_super_admin
    # ci-dessous). Un compte "Tous les droits" ne peut PAS agir (modifier ses
    # droits, bloquer, révoquer, réinitialiser le mot de passe) sur lui-même
    # NI sur un autre compte "Tous les droits" — seulement sur un admin à
    # droits limités. Voir peut_agir_sur_admin ci-dessous, qui encode cette
    # hiérarchie et est appelée par toutes les vues de gestion des admins.
    super_admin = models.BooleanField(default=False)

    # UNIQUE sur toute la plateforme, jamais attribuable via l'API (ni à la
    # création, ni via modifierDroitsAdmin — voir _appliquer_droits qui ne
    # touche jamais ce champ), posé UNIQUEMENT par le bootstrap du tout
    # premier compte (voir Registration/signals.py::creer_profil). Rend ce
    # compte intouchable : personne — pas même un autre compte "Tous les
    # droits" — ne peut le bloquer, le révoquer, modifier ses droits ou
    # réinitialiser son mot de passe (voir peut_agir_sur_admin). En
    # contrepartie, LUI seul peut effectuer ces actions sur N'IMPORTE QUEL
    # autre admin, y compris un autre compte "Tous les droits".
    est_super_super_admin = models.BooleanField(default=False)

    # bloquer/débloquer/supprimer un compte, réactiver un vendeur suspendu
    # par signalements (voir toggleBloquerUtilisateur/supprimerUtilisateurAdmin/
    # reactiverVendeurAdmin, Registration/views.py)
    gestion_utilisateurs = models.BooleanField(default=False)

    # traiter les signalements (produits, vendeurs, messages, avis),
    # désactiver/réactiver un produit signalé, supprimer un message/avis signalé
    gestion_signalements = models.BooleanField(default=False)

    # lister les demandes de vérification KYC en attente (l'approbation/rejet
    # reste pour l'instant réservée à Django admin, hors API — voir
    # Registration/admin.py::DemandeVerificationAdmin)
    gestion_verifications = models.BooleanField(default=False)

    # créer/modifier/supprimer catégories et sous-catégories de produits
    gestion_categories = models.BooleanField(default=False)

    # répondre aux messages support (vendeur/acheteur → admins, voir
    # Messagerie/views.py::repondreMessageAdmin)
    gestion_support = models.BooleanField(default=False)

    # configurer/déclencher les sauvegardes, consulter et télécharger
    # l'historique (voir Sauvegarde/views.py) — la RESTAURATION d'un backup
    # est volontairement plus stricte et exige super_admin, pas seulement ce
    # droit (action la plus destructrice du système, voir restaurer_analyser/
    # restaurer_confirmer, Sauvegarde/views.py)
    gestion_sauvegardes = models.BooleanField(default=False)

    # réinitialiser le mot de passe d'un AUTRE admin (voir
    # reinitialiserMotDePasseAdmin, Registration/views.py) — un compte "Tous
    # les droits" peut attribuer ce droit précis à un admin à droits limités
    # (délégation descendante), mais un admin qui ne possède QUE ce droit ne
    # peut réinitialiser le mot de passe que d'un autre admin à droits
    # limités, jamais celui d'un compte "Tous les droits" ni du super super
    # admin (même hiérarchie que gestion_utilisateurs pour bloquer/révoquer,
    # voir peut_agir_sur_admin)
    gestion_mots_de_passe = models.BooleanField(default=False)

    # traçabilité : qui a attribué/modifié ces droits en dernier
    attribue_par = models.ForeignKey(
                     Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                     related_name='droits_attribues'
                   )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_maj      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table            = 'droits_admin'
        verbose_name        = 'Droits admin'
        verbose_name_plural = 'Droits admin'

    def __str__(self):
        suffixe = " (propriétaire)" if self.est_super_super_admin else " (tous les droits)" if self.super_admin else ""
        return f"Droits de {self.utilisateur.email}" + suffixe

    def a_droit(self, nom_droit):
        """True si super super admin, super_admin ("tous les droits" — implique
        les deux), ou si le droit précis (par son nom de champ) est accordé."""
        return self.est_super_super_admin or self.super_admin or bool(getattr(self, nom_droit, False))


def verifier_droit_admin(utilisateur, droit):
    """
    True si `utilisateur` est un compte admin ET possède le droit demandé
    (directement, ou via super_admin). Utilisé à la place de la simple
    vérification `role == 'admin'` dans toutes les vues admin du backend —
    voir le tableau de correspondance vue → droit dans Registration/views.py
    et les fichiers équivalents de Produits/Messagerie. Défini ici (pas dans
    views.py) pour éviter les imports circulaires, même raison que
    haser_password/verifier_password ci-dessus — importable directement
    depuis Produits/Messagerie, qui importent déjà Registration.models.
    """
    if not utilisateur or utilisateur.profil.role != 'admin':
        return False
    droits = getattr(utilisateur, 'droits_admin', None)
    return bool(droits) and droits.a_droit(droit)


def peut_agir_sur_admin(acteur, cible):
    """
    True si `acteur` (un compte admin) est autorisé à bloquer/révoquer/
    modifier les droits/réinitialiser le mot de passe de `cible` (un autre
    compte admin). Hiérarchie, du plus au moins large :
      - le super super admin (unique, voir DroitsAdmin.est_super_super_admin)
        peut agir sur n'importe quel autre admin, y compris un compte "Tous
        les droits" ;
      - personne — pas même un autre super super admin, puisqu'il n'en existe
        qu'un seul — ne peut agir sur LE super super admin : intouchable ;
      - un compte "Tous les droits" (super_admin) peut agir sur un admin à
        droits limités, mais jamais sur un autre compte "Tous les droits" ;
      - un admin à droits limités ne peut jamais agir sur un autre admin via
        cette fonction (l'appelant doit de toute façon déjà avoir vérifié un
        droit de gestion précis — gestion_utilisateurs, gestion_mots_de_passe...
        — avant même d'arriver ici).
    L'action sur SOI-MÊME est toujours refusée par l'appelant AVANT d'appeler
    cette fonction (règle universelle, indépendante des droits, vérifiée
    séparément dans chaque vue — voir toggleBloquerUtilisateur/revoquerAdmin/
    modifierDroitsAdmin/reinitialiserMotDePasseAdmin, Registration/views.py).
    """
    droits_acteur = getattr(acteur, 'droits_admin', None)
    droits_cible  = getattr(cible, 'droits_admin', None)
    if not droits_acteur or not droits_cible:
        return False
    if droits_cible.est_super_super_admin:
        return False
    if droits_acteur.est_super_super_admin:
        return True
    if droits_cible.super_admin:
        return False
    return droits_acteur.super_admin


def peut_reinitialiser_mdp(acteur, cible):
    """
    Hiérarchie SPÉCIFIQUE à la réinitialisation de mot de passe (voir
    reinitialiserMotDePasseAdmin, Registration/views.py) — plus permissive
    que peut_agir_sur_admin ci-dessus pour la partie "droits limités" :
      - le super super admin peut agir sur tout le monde ;
      - personne ne peut agir sur le super super admin ;
      - un compte "Tous les droits" peut agir sur n'importe quel admin à
        droits limités (mais jamais sur un autre compte "Tous les droits") ;
      - un admin à droits limités qui possède le droit gestion_mots_de_passe
        peut réinitialiser le mot de passe d'un AUTRE admin à droits
        limités — SAUF si celui-ci possède lui aussi gestion_mots_de_passe
        (règle explicite : deux comptes avec le même droit délégué ne
        peuvent pas agir l'un sur l'autre).
    """
    droits_acteur = getattr(acteur, 'droits_admin', None)
    droits_cible  = getattr(cible, 'droits_admin', None)
    if not droits_acteur or not droits_cible:
        return False
    if droits_cible.est_super_super_admin:
        return False
    if droits_acteur.est_super_super_admin:
        return True
    if droits_cible.super_admin:
        return False
    if droits_acteur.super_admin:
        return True
    return bool(droits_acteur.gestion_mots_de_passe) and not droits_cible.gestion_mots_de_passe


# ── MODÈLE JOURNAL D'AUDIT ────────────────────────────────────────────────────
class JournalAudit(models.Model):
    """
    Trace de chaque action mutante effectuée par un compte admin (qui, quoi,
    quand) — voir enregistrer_audit ci-dessous, appelée après chaque action
    admin qui modifie des données (pas les simples listes en lecture). Sert
    de source au rapport PDF (voir Registration/services/audit_rapport_service.py
    et genererRapportAudit, Registration/views.py).
    """

    # SET_NULL (pas CASCADE) : un admin supprimé ne doit pas faire disparaître
    # la trace de ce qu'il a fait — nom_admin_snapshot garde le nom lisible
    # même après suppression ou changement de nom du compte
    admin = models.ForeignKey(
              Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
              related_name='actions_audit'
            )
    nom_admin_snapshot = models.CharField(max_length=200)

    # code court identifiant le type d'action (ex. "utilisateur.bloquer",
    # "signalement.traiter") — sert de clé de traduction côté frontend pour
    # le libellé du rapport
    action      = models.CharField(max_length=100)
    description = models.TextField()   # phrase lisible avec les détails de l'action

    date_action = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table            = 'journal_audit'
        verbose_name        = "Entrée d'audit"
        verbose_name_plural = "Journal d'audit"
        ordering            = ['-date_action']
        indexes             = [
            models.Index(fields=['date_action', 'admin']),
        ]

    def __str__(self):
        return f"{self.nom_admin_snapshot} — {self.action} — {self.date_action:%Y-%m-%d %H:%M}"


def enregistrer_audit(utilisateur, action, description):
    """Journalise une action admin — voir JournalAudit ci-dessus. À appeler
    après le succès d'une action mutante (jamais sur un simple GET/liste)."""
    JournalAudit.objects.create(
        admin=utilisateur,
        nom_admin_snapshot=f"{utilisateur.prenom} {utilisateur.nom}",
        action=action,
        description=description,
    )


# ── MODÈLE VUE DE PROFIL VENDEUR ──────────────────────────────────────────────
class VueProfilVendeur(models.Model):
    """Une ligne par consultation du profil public d'un vendeur (voir
    infoVendeur, Produits/views/produitsViews.py) — journal horodaté distinct
    du compteur cumulé Utilisateur.nombre_vues_profil : celui-ci reste un
    total "depuis toujours", ce modèle sert uniquement à filtrer par période
    et lister les visiteurs (voir Produits/views/vuesViews.py). infoVendeur
    exige désormais une connexion, donc visiteur est en pratique toujours
    renseigné — SET_NULL uniquement pour survivre à la suppression du compte
    visiteur, même logique que JournalAudit.admin ci-dessus."""

    vendeur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='vues_profil')
    visiteur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='vues_profils_effectuees'
    )
    date_vue = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vues_profil_vendeur"
        verbose_name = "vue profil vendeur"
        verbose_name_plural = "vues profils vendeurs"
        ordering = ['-date_vue']

    def __str__(self):
        return f"Vue profil #{self.vendeur_id} — {self.date_vue}"


# ── MODÈLE DEMANDE ADMINISTRATIVE ─────────────────────────────────────────────
class DemandeAdministrative(models.Model):
    """
    Demande formelle d'un utilisateur à l'administration (objet + description),
    distincte de la messagerie support libre (MessageSupport, Messagerie/models.py) :
    ici la demande aboutit toujours à une décision — agréée ou rejetée —
    notifiée par email. Cas d'usage principal : contester un blocage de compte
    (voir toggleBloquerUtilisateur, Registration/views.py, dont l'email de
    blocage renvoie vers cette fonctionnalité), mais utilisable pour toute
    demande nécessitant une vraie décision admin. Même squelette que
    DemandeVerification ci-dessus (marquer_verifie/marquer_echoue ->
    approuver/rejeter, même pattern try/except pour l'envoi d'email).

    Créée automatiquement (en plus de la saisie directe via
    creerDemandeAdministrative, utilisateur connecté) par contacterNous
    (Registration/views.py, page publique "Contactez-nous") quand l'email
    soumis correspond à un compte actuellement bloqué OU à un compte
    supprimé (voir CompteSupprime plus bas) : un compte dans l'un de ces deux
    états ne peut plus s'authentifier (bloquer() révoque tous ses tokens,
    supprimerUtilisateurAdmin est un hard delete), donc ne peut PAS atteindre
    creerDemandeAdministrative — qui exige un token valide. Sans cette
    création automatique, ces demandes ne partaient que par email brut
    (adresse DEFAULT_FROM_EMAIL), invisibles ici et sans bouton "Agréer" pour
    débloquer — c'était le bug corrigé par cet ajout.
    """

    STATUTS = [
        ('en_attente', 'En attente'),
        ('approuvee',  'Approuvée'),
        ('rejetee',    'Rejetée'),
    ]

    # SET_NULL (pas CASCADE) : une demande créée pendant qu'un compte est
    # encore bloqué (mais pas encore traitée) ne doit pas disparaître si ce
    # compte est supprimé entre-temps par un admin — et une demande émise
    # depuis contacterNous pour un compte DÉJÀ supprimé n'a jamais eu de
    # utilisateur à lier (nul dès la création). nom_contact/email_contact
    # ci-dessous sont donc TOUJOURS renseignés à la création (jamais lus
    # depuis self.utilisateur, qui peut devenir nul plus tard) : c'est la
    # seule source fiable pour l'affichage admin (_serialiseDemandeAdministrative).
    utilisateur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='demandes_administratives'
    )
    nom_contact   = models.CharField(max_length=200, blank=True)
    email_contact = models.EmailField(blank=True)
    objet       = models.CharField(max_length=200)
    description = models.TextField()
    statut      = models.CharField(max_length=20, choices=STATUTS, default='en_attente')

    # SET_NULL (pas CASCADE) : un admin supprimé ne doit pas faire disparaître
    # la trace de qui a traité la demande — même principe que JournalAudit.admin
    admin_traitant  = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='demandes_administratives_traitees'
    )
    reponse_admin   = models.TextField(blank=True)
    date_creation   = models.DateTimeField(auto_now_add=True)
    date_traitement = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "demandes_administratives"
        verbose_name = "demande administrative"
        verbose_name_plural = "demandes administratives"
        ordering = ['-date_creation']

    def __str__(self):
        return f"Demande #{self.id} — {self.objet} ({self.email_contact or (self.utilisateur.email if self.utilisateur_id else 'compte supprimé')})"

    def _notifier(self, sujet, corps):
        """Envoie l'email de décision (approuvé/rejeté) au bon destinataire :
        via envoyer_email_decision si le compte existe encore (self.utilisateur),
        sinon directement à email_contact (compte supprimé — voir docstring de
        la classe) — même tolérance aux échecs SMTP dans les deux cas (jamais
        d'exception remontée, la décision est déjà enregistrée en base)."""
        from .services.notification_service import envoyer_email_decision
        if self.utilisateur_id:
            envoyer_email_decision(self.utilisateur, sujet, corps)
            return
        if not self.email_contact:
            return
        from django.conf import settings
        from django.core.mail import send_mail
        try:
            send_mail(sujet, corps, settings.DEFAULT_FROM_EMAIL, [self.email_contact], fail_silently=False)
        except Exception as e:
            print(f"[email décision demande #{self.id}] échec envoi à {self.email_contact} : {e}")

    def approuver(self, admin, reponse=''):
        """Agrée la demande — si elle concerne un compte actuellement bloqué,
        débloque automatiquement (décision explicite : une seule action pour
        l'admin, cohérent avec l'email de blocage qui invite à faire ce
        recours), puis notifie le demandeur par email. Un compte supprimé
        (self.utilisateur_id nul) n'a rien à débloquer — l'approbation
        équivaut alors à un simple accusé de réception favorable."""
        self.statut = 'approuvee'
        self.admin_traitant = admin
        self.reponse_admin = reponse
        self.date_traitement = timezone.now()
        self.save()

        compte_debloque = bool(self.utilisateur_id and self.utilisateur.est_bloquer)
        if compte_debloque:
            self.utilisateur.debloquer()

        from .services.notification_service import pied_de_page
        prenom = self.utilisateur.prenom if self.utilisateur_id else (self.nom_contact.split(' ')[0] if self.nom_contact else '')
        self._notifier(
            f"Votre demande « {self.objet} » a été approuvée — RekoltHt",
            f"Bonjour {prenom},\n\n"
            f"Votre demande à l'administration a été approuvée.\n\n"
            f"Objet : {self.objet}\n"
            + (f"Réponse de l'administration : {reponse}\n\n" if reponse else "\n")
            + ("Votre compte a été débloqué dans la foulée.\n" if compte_debloque else "")
            + pied_de_page(lien_demande_administrative=bool(self.utilisateur_id)),
        )

    def rejeter(self, admin, motif):
        """Rejette la demande avec un motif obligatoire, notifie par email —
        même principe que DemandeVerification.marquer_echoue ci-dessus."""
        self.statut = 'rejetee'
        self.admin_traitant = admin
        self.reponse_admin = motif
        self.date_traitement = timezone.now()
        self.save()

        from .services.notification_service import pied_de_page
        prenom = self.utilisateur.prenom if self.utilisateur_id else (self.nom_contact.split(' ')[0] if self.nom_contact else '')
        self._notifier(
            f"Votre demande « {self.objet} » a été rejetée — RekoltHt",
            f"Bonjour {prenom},\n\n"
            f"Votre demande à l'administration a été rejetée.\n\n"
            f"Objet : {self.objet}\n"
            f"Motif : {motif}\n"
            + pied_de_page(lien_demande_administrative=bool(self.utilisateur_id)),
        )


class CompteSupprime(models.Model):
    """Trace minimale ("tombstone") laissée quand un admin supprime
    définitivement un compte (supprimerUtilisateurAdmin) — l'Utilisateur lui-
    même est un hard delete (CASCADE, voir toutes les FK vers Utilisateur),
    donc sans cette trace un email supprimé redevient indiscernable d'un
    email jamais inscrit. Permet à seConnecter de reconnaître ce cas et
    d'afficher la raison de la suppression au lieu du message générique
    "email n'existe pas" (demande explicite)."""
    email             = models.EmailField(unique=True)
    nom               = models.CharField(max_length=100)
    prenom            = models.CharField(max_length=100)
    raison            = models.TextField()
    admin             = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name='comptes_supprimes')
    date_suppression  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "comptes_supprimes"
        verbose_name = "compte supprimé"
        verbose_name_plural = "comptes supprimés"
        ordering = ['-date_suppression']

    def __str__(self):
        return f"Compte supprimé — {self.prenom} {self.nom} ({self.email})"
