import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Registration', '0008_token'),
    ]

    operations = [
        # Entreprise n'est plus un modèle autonome : elle hérite désormais
        # d'Utilisateur (héritage multi-tables). La table 'entreprise' était vide,
        # elle est donc recréée directement avec la nouvelle structure.
        migrations.DeleteModel(
            name='Entreprise',
        ),
        migrations.CreateModel(
            name='Vendeur',
            fields=[],
            options={
                'verbose_name': 'Vendeur',
                'verbose_name_plural': 'Vendeurs',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('Registration.utilisateur',),
        ),
        migrations.CreateModel(
            name='Acheteur',
            fields=[],
            options={
                'verbose_name': 'Acheteur',
                'verbose_name_plural': 'Acheteurs',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('Registration.utilisateur',),
        ),
        migrations.CreateModel(
            name='Entreprise',
            fields=[
                ('utilisateur_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='Registration.utilisateur')),
                ('nom_Entreprise', models.CharField(max_length=100, unique=True)),
                ('num_Enregistrement', models.CharField(max_length=100, unique=True)),
                ('secteur', models.CharField(choices=[('agriculture', 'Agriculture'), ('transformation', 'Transformation'), ('distribution', 'Distribution'), ('autre', 'Autre')], default='agriculture', max_length=20)),
                ('description', models.TextField(blank=True, null=True)),
                ('adresse', models.CharField(blank=True, max_length=255)),
                ('commune', models.CharField(blank=True, max_length=100)),
                ('pays', models.CharField(default='Haiti', max_length=100)),
                ('logo', models.ImageField(blank=True, null=True, upload_to='logos_entreprise/')),
                ('longitude', models.FloatField(blank=True, null=True)),
                ('latitude', models.FloatField(blank=True, null=True)),
                ('est_verifiee', models.BooleanField(default=False)),
                ('date_creation', models.DateTimeField(auto_now_add=True)),
                ('date_maj', models.DateTimeField(auto_now=True)),
                ('statut_verification', models.CharField(choices=[('en attente', 'En attente'), ('valide', 'Validé'), ('rejete', 'Rejeté')], default='en attente', max_length=20)),
                ('proprietaire', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entreprises', to='Registration.utilisateur')),
            ],
            options={
                'verbose_name': 'Entreprise',
                'verbose_name_plural': 'Entreprises',
                'db_table': 'entreprise',
                'ordering': ['id'],
            },
            bases=('Registration.utilisateur',),
        ),
        migrations.AlterField(
            model_name='profil',
            name='role',
            field=models.CharField(choices=[('acheteur', 'Acheteur'), ('vendeur', 'Vendeur'), ('entreprise', 'Entreprise'), ('admin', 'Admin')], default='acheteur', max_length=20),
        ),
    ]
