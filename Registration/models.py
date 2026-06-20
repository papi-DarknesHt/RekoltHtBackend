import hashlib
import secrets
from django.db import models
from geopy.distance import geodesic
from django.core.files.base import ContentFile


# ── FONCTION HASH ─────────────────────────────────────────────────────────────
# Définie ici pour éviter l'import circulaire avec views.py
def haser_password(password):
    """Hash le mot de passe avec un sel aléatoire"""
    sel  = secrets.token_hex(16)
    hash = hashlib.sha256((password + sel).encode()).hexdigest()
    return f"{sel}${hash}"


def verifier_password(password, hashed):
    """Vérifie le mot de passe contre le hash stocké"""
    sel, hash = hashed.split('$')
    hash_entrer = hashlib.sha256((password + sel).encode()).hexdigest()
    return hash_entrer == hash


# ── MODÈLE UTILISATEUR ────────────────────────────────────────────────────────
class Utilisateur(models.Model):

    id               = models.AutoField(primary_key=True)
    nom              = models.CharField(max_length=100)
    prenom           = models.CharField(max_length=100)
    email            = models.EmailField(unique=True)
    mot_de_passe     = models.CharField(max_length=255)  # 100 trop court pour un hash
    telephone        = models.CharField(max_length=20)
    date_inscription = models.DateTimeField(auto_now_add=True)
    est_actif        = models.BooleanField(default=False)  # False par défaut

    class Meta:
        db_table            = 'utilisateur'
        verbose_name        = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'
        ordering            = ['id']

    def __str__(self):
        # un seul __str__ — le deuxième écrasait le premier
        return f"{self.prenom} {self.nom} — {self.email}"

    def modifier_est_actif(self):
        """Active ou désactive le compte"""
        self.est_actif = not self.est_actif
        self.save()

    def modifier_mot_de_passe(self, nouveau_mot_de_passe):
        """Hash et sauvegarde le nouveau mot de passe"""
        # haser_password est maintenant dans models.py — pas d'import circulaire
        self.mot_de_passe = haser_password(nouveau_mot_de_passe)
        self.save()


# ── MODÈLE PROFIL ─────────────────────────────────────────────────────────────
class Profil(models.Model):   # ← P majuscule

    # Choix du rôle — doit être un vrai champ Django
    ROLES = [
        ('acheteur', 'Acheteur'),   # ← correction orthographe 'achteur'
        ('vendeur',  'Vendeur'),
        ('admin',    'Admin'),
    ]

    id           = models.AutoField(primary_key=True)
    utilisateur  = models.OneToOneField(
                     Utilisateur,
                     on_delete    = models.CASCADE,
                     related_name = 'profil'
                   )
    bio          = models.TextField(blank=True, null=True)
    photo_profil = models.ImageField(upload_to='photos_profil/', blank=True, null=True)
    adresse      = models.CharField(max_length=255, blank=True)
    commune      = models.CharField(max_length=100, blank=True)
    ville        = models.CharField(max_length=100, blank=True)
    pays         = models.CharField(max_length=100, default='Haiti')
    longitude    = models.FloatField(blank=True, null=True)
    latitude     = models.FloatField(blank=True, null=True)
    date_maj     = models.DateTimeField(auto_now=True)

    # rôle défini comme vrai champ Django avec choices
    role         = models.CharField(
                     max_length = 20,
                     choices    = ROLES,
                     default    = 'acheteur'
                   )

    # --- Types de vendeur et statut de vérification ---
    TYPES_VENDEUR = [
        ('individu', 'Individu'),
        ('entreprise', 'Entreprise'),
    ]

    # STATUTS_VERIFICATION removed from Profil; it now lives on Entreprise

    # Le type (individu / entreprise) reste sur le profil :
    type_vendeur = models.CharField(max_length=20, choices=TYPES_VENDEUR, blank=True, null=True)

    class Meta:
        db_table            = 'profil'
        verbose_name        = 'Profil'
        verbose_name_plural = 'Profils'
        ordering            = ['id']

    def __str__(self):
        return f"Profil de {self.utilisateur.prenom} {self.utilisateur.nom}"

    def mettre_a_jour(self, **kwargs):
        """Met à jour les informations du profil"""
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.save()

    def convertir_en_vendeur(self):
        """Convertit un acheteur en vendeur"""
        self.role = 'vendeur'
        self.save()

    def convertir_en_acheteur(self):
        """Convertit un vendeur en acheteur"""
        self.role = 'acheteur'   # ← correction orthographe
        self.save()

    def soumettre_demande_vendeur(self, type_vendeur, nom_entreprise=None, nouveau_fichier=None):
        """
        Traite la demande de passage au statut vendeur selon le type choisi.
        nouveau_fichier : tuple (filename, ContentFile) ou None si aucun nouveau document fourni.
        """
        # Cocher le type de vendeur sur le profil
        self.type_vendeur = type_vendeur

        # Importer dynamiquement le modèle Entreprise via apps.get_model afin
        # d'éviter les problèmes d'import circulaire (Entreprise est défini
        # dans Registration.entreprise).
        from django.apps import apps
        Entreprise = None
        try:
            Entreprise = apps.get_model('Registration', 'Entreprise')
        except Exception:
            Entreprise = None

        if type_vendeur == 'entreprise':
            # créer ou récupérer l'objet Entreprise lié au profil
            if Entreprise is not None:
                entreprise, created = Entreprise.objects.get_or_create(
                    profil=self,
                    defaults={'nom_entreprise': nom_entreprise or ''}
                )
                # toujours mettre à jour le nom si fourni
                if nom_entreprise is not None:
                    entreprise.nom_entreprise = nom_entreprise

                if nouveau_fichier is not None:
                    # supprimer l'ancien fichier avant d'enregistrer le nouveau
                    try:
                        old = getattr(entreprise, 'piece_justificative', None)
                        if old:
                            old.delete(save=False)
                    except Exception:
                        pass
                    filename, contenu = nouveau_fichier
                    entreprise.piece_justificative.save(filename, contenu, save=False)
                    entreprise.statut_verification = 'en_attente'

                # si aucun nouveau fichier : conserver l'existant et le statut
                entreprise.save()
            else:
                # fallback : stocker temporairement sur le profil
                self.__dict__['nom_entreprise'] = nom_entreprise

        else:  # individu
            # supprimer toute pièce justificative existante et l'objet Entreprise si présent
            if Entreprise is not None:
                try:
                    entreprise = getattr(self, 'entreprise', None)
                    if entreprise is not None:
                        try:
                            old = getattr(entreprise, 'piece_justificative', None)
                            if old:
                                old.delete(save=False)
                        except Exception:
                            pass
                        entreprise.delete()
                except Exception:
                    # pas d'entreprise liée
                    pass

        # définir le rôle côté serveur uniquement
        self.role = 'vendeur'
        self.save()

    # --- Propriétés de compatibilité pour l'ancien modèle (proxy vers Entreprise) ---
    def creer_compte_entreprise(self, nom_entreprise, nouveau_fichier):
        """
        Crée l'objet Entreprise lié à ce profil au moment de l'inscription.

        Contrairement à soumettre_demande_vendeur, ne touche jamais à self.role —
        une entreprise-acheteuse reste 'acheteur' tant qu'elle ne passe pas par
        devenirVendeur explicitement.

        nouveau_fichier : tuple (filename, ContentFile), jamais None ici (déjà validé en amont).
        """
        from django.apps import apps

        Entreprise = apps.get_model('Registration', 'Entreprise')

        # Indiquer que ce profil est de type entreprise
        self.type_vendeur = 'entreprise'

        # créer l'objet Entreprise et enregistrer le fichier
        entreprise = Entreprise.objects.create(profil=self, nom_entreprise=nom_entreprise)
        filename, contenu = nouveau_fichier
        entreprise.piece_justificative.save(filename, contenu, save=False)
        entreprise.statut_verification = 'en_attente'
        entreprise.save()

        # Sauvegarder le profil (met à jour type_vendeur)
        self.save()

    @property
    def nom_entreprise(self):
        try:
            return self.entreprise.nom_entreprise
        except Exception:
            return None

    @property
    def piece_justificative(self):
        try:
            return self.entreprise.piece_justificative
        except Exception:
            return None

    @property
    def statut_verification(self):
        # Si une entreprise est liée, retourner son statut ; si le type_vendeur
        # est 'individu', considérer le statut comme 'valide'
        try:
            ent = self.entreprise
            return ent.statut_verification
        except Exception:
            if self.type_vendeur == 'individu':
                return 'valide'
            return 'non_requis'


# Importer le module entreprise pour s'assurer que le modèle Entreprise est
# enregistré par l'app Django (évite les problèmes si le modèle est défini dans
# un fichier séparé).
try:
    from . import entreprise  # noqa: F401
except Exception:
    # import silencieux en cas d'erreur d'import pour ne pas casser l'initialisation
    pass

