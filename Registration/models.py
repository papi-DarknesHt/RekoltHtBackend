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

    def calculer_distance(self, autre_profil):
        """Calcule la distance en km entre deux profils"""
        if None in [self.latitude, self.longitude,
                    autre_profil.latitude, autre_profil.longitude]:
            return None

        coord1 = (self.latitude,       self.longitude)
        coord2 = (autre_profil.latitude, autre_profil.longitude)
        return geodesic(coord1, coord2).kilometers