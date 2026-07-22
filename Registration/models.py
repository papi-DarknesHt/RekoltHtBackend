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

    def bloquer(self):
        """Suspend le compte (accès admin, voir Registration/views.py::toggleBloquerUtilisateur)
        et invalide immédiatement toute session active en supprimant ses tokens —
        sinon un utilisateur déjà connecté garderait l'accès jusqu'à expiration
        naturelle du token (pas de TTL ici, donc indéfiniment)."""
        self.est_bloquer = True
        self.save()
        self.tokens.all().delete()

    def debloquer(self):
        """Réactive un compte suspendu (accès admin)."""
        self.est_bloquer = False
        self.save()


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
        ('en_attente_manuelle', 'En attente de revue manuelle'),  # pipeline auto incomplet (ex: guichet.mci.ht indisponible) — voir _verifier_patente_mci
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
        nom_fichier = f"contrat_{self.id}.pdf"
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
