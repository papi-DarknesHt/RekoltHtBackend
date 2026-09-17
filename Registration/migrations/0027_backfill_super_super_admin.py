# Généré manuellement le 2026-08-06
#
# Désigne exactement UN compte super super admin (intouchable, voir
# DroitsAdmin.est_super_super_admin / peut_agir_sur_admin, Registration/models.py)
# sur les instances qui existaient déjà avant l'introduction de ce champ — sans
# ça, aucun compte n'aurait ce statut sur un déploiement existant (contrairement
# à une instance fraîche, où Registration/signals.py::creer_profil l'attribue
# désormais automatiquement au tout premier compte créé). Choisit le compte
# "Tous les droits" (super_admin=True) le plus ancien, par date d'inscription —
# cohérent avec la règle "le premier compte de la plateforme est intouchable".
# N'a aucun effet si un compte super super admin existe déjà (idempotent).

from django.db import migrations


def backfill_super_super_admin(apps, schema_editor):
    DroitsAdmin = apps.get_model('Registration', 'DroitsAdmin')

    if DroitsAdmin.objects.filter(est_super_super_admin=True).exists():
        return

    plus_ancien = (
        DroitsAdmin.objects.filter(super_admin=True)
        .order_by('utilisateur__date_inscription')
        .first()
    )
    if plus_ancien:
        plus_ancien.est_super_super_admin = True
        plus_ancien.save(update_fields=['est_super_super_admin'])


def revenir_en_arriere(apps, schema_editor):
    # rien à défaire : retirer est_super_super_admin laisserait la plateforme
    # sans aucun compte intouchable — pas réversible
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Registration', '0026_droitsadmin_est_super_super_admin_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_super_super_admin, revenir_en_arriere),
    ]
