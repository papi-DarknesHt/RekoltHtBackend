# ── IMPORTS ───────────────────────────────────────────────────────────────────
from django.db import models

from Registration.models import Utilisateur


# ── MODÈLE CONFIGURATION SAUVEGARDE ────────────────────────────────────────────
class ConfigurationSauvegarde(models.Model):
    """
    Configuration de la planification des sauvegardes — une seule ligne vivante
    (pk=1 forcé, voir obtenir_configuration ci-dessous), modifiable par un admin
    ayant le droit gestion_sauvegardes (voir Registration/models.py::DroitsAdmin).
    Lue par le job périodique du scheduler (Sauvegarde/services/
    planification_service.py) pour savoir si une sauvegarde planifiée est due.
    """

    FREQUENCES = [
        ('quotidienne',  'Quotidienne'),
        ('hebdomadaire', 'Hebdomadaire'),
        ('mensuelle',    'Mensuelle'),
    ]
    TYPES_SAUVEGARDE = [
        ('complete',     'Complète'),
        ('incrementale', 'Incrémentale'),
    ]
    DESTINATIONS = [
        ('locale',       'Locale (serveur)'),
        ('google_drive', 'Google Drive'),
    ]
    JOURS_SEMAINE = [
        (0, 'Lundi'), (1, 'Mardi'), (2, 'Mercredi'), (3, 'Jeudi'),
        (4, 'Vendredi'), (5, 'Samedi'), (6, 'Dimanche'),
    ]

    id = models.AutoField(primary_key=True)

    # planification désactivée par défaut tant qu'un admin ne l'a pas
    # explicitement activée — pas de sauvegarde automatique surprise
    active = models.BooleanField(default=False)

    frequence          = models.CharField(max_length=20, choices=FREQUENCES, default='quotidienne')
    heure_declenchement = models.TimeField(default='02:00')  # heure creuse par défaut
    jour_semaine        = models.PositiveSmallIntegerField(choices=JOURS_SEMAINE, default=0)   # utilisé si hebdomadaire
    jour_mois           = models.PositiveSmallIntegerField(default=1)                            # utilisé si mensuelle, plafonné à 28 en pratique (voir clean())

    type_sauvegarde = models.CharField(max_length=20, choices=TYPES_SAUVEGARDE, default='complete')
    destination     = models.CharField(max_length=20, choices=DESTINATIONS, default='locale')

    # connexion Google Drive (voir Sauvegarde/services/google_drive_service.py)
    # — le refresh token est chiffré avec la même clé maître que les
    # sauvegardes elles-mêmes (settings.BACKUP_MASTER_KEY), jamais en clair
    google_drive_connecte             = models.BooleanField(default=False)
    google_drive_refresh_token_chiffre = models.TextField(null=True, blank=True)
    google_drive_dossier_id            = models.CharField(max_length=255, null=True, blank=True)

    # point de départ des sauvegardes incrémentales, et sert aussi à savoir si
    # la période courante (jour/semaine/mois) a déjà été couverte par une
    # exécution planifiée réussie
    derniere_execution_reussie = models.DateTimeField(null=True, blank=True)

    # verrou applicatif contre un déclenchement en double du même job planifié
    # (voir verifier_et_executer_sauvegarde_planifiee, Sauvegarde/services/
    # planification_service.py) — nécessaire car apps.py ne peut garantir
    # qu'un seul process exécute le scheduler que sous `manage.py runserver`
    # ; le projet tourne en réalité via uvicorn (voir README.md/STRUCTURE.md),
    # où plusieurs workers/process auraient chacun leur propre scheduler
    # APScheduler en mémoire, chacun capable de déclencher la même sauvegarde
    # automatique au même instant. Horodatage (pas un simple booléen) : une
    # valeur trop ancienne (voir DELAI_EXPIRATION_VERROU_SECONDES) est
    # considérée abandonnée (process tué en cours d'exécution) et n'empêche
    # plus un nouveau déclenchement.
    verification_planifiee_en_cours_depuis = models.DateTimeField(null=True, blank=True)

    modifie_par = models.ForeignKey(
                    Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                    related_name='configurations_sauvegarde_modifiees'
                  )
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table            = 'configuration_sauvegarde'
        verbose_name        = 'Configuration de sauvegarde'
        verbose_name_plural = 'Configuration de sauvegarde'

    def __str__(self):
        return f"Configuration sauvegarde (active={self.active}, {self.frequence})"

    def save(self, *args, **kwargs):
        # jour_mois plafonné à 28 : reste valide tous les mois (y compris février),
        # évite qu'un "31" configuré ne saute silencieusement certains mois
        if self.jour_mois > 28:
            self.jour_mois = 28
        self.pk = 1   # force la ligne unique — voir obtenir_configuration()
        super().save(*args, **kwargs)


def obtenir_configuration():
    """
    Retourne la configuration unique, en la créant avec les valeurs par
    défaut si elle n'existe pas encore (premier accès).

    Bug corrigé ici (trouvé en écrivant Sauvegarde/tests.py) : quand la ligne
    est CRÉÉE à l'instant par get_or_create(), l'instance retournée porte
    encore la valeur par défaut BRUTE de heure_declenchement ('02:00', une
    chaîne — voir TimeField(default='02:00') ci-dessus), pas un vrai
    datetime.time — Django ne convertit les defaults littéraux qu'au moment
    de l'écriture SQL, jamais sur l'instance Python en mémoire. Résultat
    observé : le tout premier GET /Sauvegarde/configuration/ sur une base
    neuve plantait avec `AttributeError: 'str' object has no attribute
    'strftime'` dans _serialiseConfiguration (Sauvegarde/views.py). Un
    refresh_from_db() force une relecture SQL propre, qui redonne un vrai
    datetime.time.
    """
    config, cree = ConfigurationSauvegarde.objects.get_or_create(pk=1)
    if cree:
        config.refresh_from_db()
    return config


# ── MODÈLE HISTORIQUE SAUVEGARDE ───────────────────────────────────────────────
class HistoriqueSauvegarde(models.Model):
    """
    Une ligne par exécution de sauvegarde (manuelle ou planifiée), succès ou
    échec — sert à la fois de journal et d'index des fichiers .rhtbackup
    disponibles au téléchargement/restauration (voir Sauvegarde/views.py).
    """

    STATUTS = [
        ('succes', 'Succès'),
        ('echec',  'Échec'),
    ]

    id = models.AutoField(primary_key=True)
    date_execution = models.DateTimeField(auto_now_add=True)

    type_sauvegarde = models.CharField(max_length=20, choices=ConfigurationSauvegarde.TYPES_SAUVEGARDE)
    destination     = models.CharField(max_length=20, choices=ConfigurationSauvegarde.DESTINATIONS)

    # null = déclenchée automatiquement par le scheduler (pas par un admin)
    declenche_par = models.ForeignKey(
                      Utilisateur, on_delete=models.SET_NULL, null=True, blank=True,
                      related_name='sauvegardes_declenchees'
                    )

    statut         = models.CharField(max_length=10, choices=STATUTS)
    message_erreur = models.TextField(null=True, blank=True)

    taille_octets        = models.BigIntegerField(null=True, blank=True)
    nombre_enregistrements = models.IntegerField(null=True, blank=True)
    checksum_sha256       = models.CharField(max_length=64, null=True, blank=True)

    # l'un ou l'autre selon `destination` — jamais les deux
    chemin_fichier_local = models.CharField(max_length=500, null=True, blank=True)
    google_drive_file_id = models.CharField(max_length=255, null=True, blank=True)

    # sauvegarde de sécurité prise automatiquement juste avant une
    # restauration (voir Sauvegarde/services/restauration_service.py) — pas
    # une exécution planifiée/manuelle normale, distinguée ici pour l'affichage
    est_sauvegarde_securite = models.BooleanField(default=False)

    class Meta:
        db_table            = 'historique_sauvegarde'
        verbose_name        = 'Historique de sauvegarde'
        verbose_name_plural = 'Historique des sauvegardes'
        ordering            = ['-date_execution']

    def __str__(self):
        return f"Sauvegarde {self.date_execution:%Y-%m-%d %H:%M} — {self.statut}"
