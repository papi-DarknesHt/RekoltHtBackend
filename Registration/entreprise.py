from django.db import models


class Entreprise(models.Model):
    """Informations spécifiques à une entreprise liée à un Profil.

    On référence le modèle Profil par string 'Registration.Profil' pour éviter
    les import circulaires entre modules.
    """
    STATUTS_VERIFICATION = [
        ('en_attente', 'En attente'),
        ('valide', 'Validé'),
        ('rejete', 'Rejeté'),
    ]

    id = models.AutoField(primary_key=True)
    profil = models.OneToOneField('Registration.Profil', on_delete=models.CASCADE, related_name='entreprise')
    nom_entreprise = models.CharField(max_length=150)
    piece_justificative = models.FileField(upload_to='pieces_justificatives/', blank=True, null=True)
    statut_verification = models.CharField(max_length=20, choices=STATUTS_VERIFICATION, default='en_attente')
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'entreprise'
        verbose_name = 'Entreprise'
        verbose_name_plural = 'Entreprises'
        ordering = ['id']

    def __str__(self):
        try:
            email = self.profil.utilisateur.email
        except Exception:
            email = 'unknown'
        return f"{self.nom_entreprise} — {email}"

