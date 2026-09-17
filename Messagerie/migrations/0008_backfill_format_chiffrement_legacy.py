from django.db import migrations


def marquer_legacy_e2e_client(apps, schema_editor):
    """Tout MessageSupport chiffré AVANT l'introduction du coffre serveur
    n'a pu l'être qu'en enveloppe E2E côté client (seul format qui existait
    alors) — voir Messagerie/models.py::MessageSupport.format_chiffrement.
    Les messages en clair (chiffre=False) n'ont pas besoin de marquage."""
    MessageSupport = apps.get_model('Messagerie', 'MessageSupport')
    MessageSupport.objects.filter(chiffre=True, format_chiffrement__isnull=True).update(
        format_chiffrement='e2e_client'
    )
    MessageSupport.objects.filter(
        reponse_format_chiffrement__isnull=True,
    ).exclude(reponse='').filter(iv_reponse__isnull=False).update(
        reponse_format_chiffrement='e2e_client'
    )


def revenir_en_arriere(apps, schema_editor):
    # rien à défaire : le champ redevient simplement NULL en supprimant la colonne
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Messagerie', '0007_messagesupport_format_chiffrement_and_more'),
    ]

    operations = [
        migrations.RunPython(marquer_legacy_e2e_client, revenir_en_arriere),
    ]
