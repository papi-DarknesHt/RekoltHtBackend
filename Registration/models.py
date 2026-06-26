import hashlib
import secrets
from django.db import models
from geopy.distance import geodesic


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
    mot_de_passe     = models.CharField(max_length=255)
    telephone        = models.CharField(max_length=20)
    date_inscription = models.DateTimeField(auto_now_add=True)
    est_actif        = models.BooleanField(default=False)
    est_bloquer      = models.BooleanField(default=False)

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

    def possede_entreprise(self):
        """Vérifie si l'utilisateur a au moins une entreprise enregistrée"""
        return self.entreprises.exists()


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

    def obtenir_coordonnees(self):
        """Retourne les coordonnées GPS"""
        return {
            'longitude': self.longitude,
            'latitude':  self.latitude,
        }

    def supprimer_photo_profil(self):
        """Supprime la photo de profil (fichier + référence)"""
        if self.photo_profil:
            self.photo_profil.delete(save=False)
            self.photo_profil = None
            self.save()

    def calculer_distance(self, autre_profil):
        """Calcule la distance en km entre deux profils"""
        if None in [self.latitude, self.longitude,
                    autre_profil.latitude, autre_profil.longitude]:
            return None

        coord1 = (self.latitude,       self.longitude)
        coord2 = (autre_profil.latitude, autre_profil.longitude)
        return geodesic(coord1, coord2).kilometers

class Entreprise(models.Model):

    SECTEURS = [
        ('agriculture', 'Agriculture'),
        ('transformation', 'Transformation'),
        ('distribution', 'Distribution'),
        ('autre', 'Autre'),
    ]
    STATUTS_VERIFICATION = [
        ('en attente', 'En attente'),
        ('valide', 'Validé'),
        ('rejete', 'Rejeté'),
    ]

    id                  = models.AutoField(primary_key=True)
    proprietaire        = models.ForeignKey(
                             Utilisateur,
                             on_delete    = models.CASCADE,
                             related_name = 'entreprises'
                           )
    nom_Entreprise      = models.CharField(max_length=100, unique=True)
    num_Enregistrement  = models.CharField(max_length=100, unique=True)
    secteur             = models.CharField(
                             max_length = 20,
                             choices    = SECTEURS,
                             default    = 'agriculture'
                           )
    description         = models.TextField(blank=True, null=True)
    email               = models.EmailField(blank=True, null=True)
    telephone           = models.CharField(max_length=20, blank=True)
    adresse             = models.CharField(max_length=255, blank=True)
    commune             = models.CharField(max_length=100, blank=True)
    pays                = models.CharField(max_length=100, default='Haiti')
    logo                = models.ImageField(upload_to='logos_entreprise/', blank=True, null=True)
    longitude           = models.FloatField(blank=True, null=True)
    latitude            = models.FloatField(blank=True, null=True)
    est_verifiee        = models.BooleanField(default=False)
    date_creation       = models.DateTimeField(auto_now_add=True)
    date_maj            = models.DateTimeField(auto_now=True)
    statut_verification = models.CharField(
                               max_length = 20,
                               choices = STATUTS_VERIFICATION,
                               default = 'en attente' 
                            )

    class Meta:
        db_table            = 'entreprise'
        verbose_name        = 'Entreprise'
        verbose_name_plural = 'Entreprises'
        ordering            = ['id']

    def __str__(self):
        return f"{self.nom_Entreprise} ({self.num_Enregistrement})"

    def mettre_a_jour(self, **kwargs):
        """Met à jour les informations de l'entreprise"""
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.save()

    def modifier_est_verifiee(self):
        """Active ou désactive la vérification de l'entreprise"""
        self.est_verifiee = not self.est_verifiee
        self.save()

    def obtenir_coordonnees(self):
        """Retourne les coordonnées GPS de l'entreprise"""
        return {
            'longitude': self.longitude,
            'latitude':  self.latitude,
        }

    def supprimer_logo(self):
        """Supprime le logo de l'entreprise (fichier + référence)"""
        if self.logo:
            self.logo.delete(save=False)
            self.logo = None
            self.save()

    def calculer_distance(self, autre_entreprise):
        """Calcule la distance en km entre deux entreprises"""
        if None in [self.latitude, self.longitude,
                    autre_entreprise.latitude, autre_entreprise.longitude]:
            return None

        coord1 = (self.latitude,           self.longitude)
        coord2 = (autre_entreprise.latitude, autre_entreprise.longitude)
        return geodesic(coord1, coord2).kilometers