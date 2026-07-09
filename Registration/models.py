# ── IMPORTS ───────────────────────────────────────────────────────────────────
import hashlib   # algorithme SHA-256 pour hasher les mots de passe
import secrets   # générateur de nombres aléatoires cryptographiquement sûrs (sel + tokens)

from django.db    import models        # classes de base pour définir les modèles Django
from django.utils import timezone      # horodatage UTC cohérent avec USE_TZ = True
from geopy.distance import geodesic   # calcul de distance réelle entre deux points GPS


# ── FONCTIONS DE HASHAGE ──────────────────────────────────────────────────────
# Définies ici (et non dans views.py) pour éviter les imports circulaires.
def haser_password(password):
    """Hash un mot de passe en clair avec un sel aléatoire (SHA-256 + sel)."""
    sel  = secrets.token_hex(16)                                      # sel de 32 caractères hex (128 bits)
    hash = hashlib.sha256((password + sel).encode()).hexdigest()      # hash du couple mot_de_passe+sel
    return f"{sel}${hash}"   # format stocké en base : "sel$hash"


def verifier_password(password, hashed):
    """Vérifie qu'un mot de passe en clair correspond au hash stocké."""
    sel, hash   = hashed.split('$')                                   # extraire sel et hash du format stocké
    hash_entrer = hashlib.sha256((password + sel).encode()).hexdigest()  # recalculer le hash
    return hash_entrer == hash   # True si les hash correspondent


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
    commune      = models.CharField(max_length=100, blank=True)
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
    num_Enregistrement  = models.CharField(max_length=100, unique=True)   # numéro légal unique
    secteur             = models.CharField(
                             max_length = 20,
                             choices    = SECTEURS,
                             default    = 'agriculture'
                           )
    description         = models.TextField(blank=True, null=True)
    adresse             = models.CharField(max_length=255, blank=True)
    commune             = models.CharField(max_length=100, blank=True)
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
        return f"{self.nom_Entreprise} ({self.num_Enregistrement})"

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
