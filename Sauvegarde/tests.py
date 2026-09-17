import json
import secrets
import shutil
import tempfile
from pathlib import Path

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from Registration.models import Utilisateur, Token, DroitsAdmin, haser_password
from .models import ConfigurationSauvegarde, HistoriqueSauvegarde, obtenir_configuration
from .services.chiffrement_service import (
    chiffrer_archive, dechiffrer_archive, chiffrer_texte, dechiffrer_texte,
    ErreurChiffrementSauvegarde,
)
from .services import google_drive_service
from .services.google_drive_service import ErreurGoogleDrive


# ── CHIFFREMENT (Sauvegarde/services/chiffrement_service.py) ─────────────────
# Module pur (pas de DB, pas de réseau) — 0% de couverture au départ alors
# qu'il protège le contenu de TOUTES les sauvegardes. Priorité évidente.
class TestChiffrementService(TestCase):

    def test_chiffrer_puis_dechiffrer_archive(self):
        donnees = b'{"utilisateurs": []}'
        chiffre = chiffrer_archive(donnees)
        self.assertTrue(chiffre.startswith(b'RHTBK1'))
        self.assertNotIn(b'utilisateurs', chiffre)  # bien illisible en clair
        self.assertEqual(dechiffrer_archive(chiffre), donnees)

    def test_dechiffrer_archive_refuse_mauvais_en_tete(self):
        with self.assertRaises(ErreurChiffrementSauvegarde):
            dechiffrer_archive(b'PASUNRHTBK...')

    def test_dechiffrer_archive_refuse_contenu_corrompu(self):
        chiffre = chiffrer_archive(b'donnees test')
        corrompu = chiffre[:-5] + b'xxxxx'   # même en-tête, jeton altéré
        with self.assertRaises(ErreurChiffrementSauvegarde):
            dechiffrer_archive(corrompu)

    def test_chiffrer_puis_dechiffrer_texte(self):
        texte = "un_refresh_token_google_tres_secret"
        chiffre = chiffrer_texte(texte)
        self.assertNotEqual(chiffre, texte)
        self.assertEqual(dechiffrer_texte(chiffre), texte)

    def test_dechiffrer_texte_refuse_jeton_invalide(self):
        with self.assertRaises(ErreurChiffrementSauvegarde):
            dechiffrer_texte("pas_un_jeton_fernet_valide")

    @override_settings(BACKUP_MASTER_KEY=None)
    def test_cle_absente_leve_erreur_explicite(self):
        with self.assertRaises(ErreurChiffrementSauvegarde):
            chiffrer_archive(b'peu importe')


# ── MODÈLE CONFIGURATION (ligne unique + plafond jour_mois) ───────────────────
class TestConfigurationSauvegardeModel(TestCase):

    def test_obtenir_configuration_cree_une_ligne_unique(self):
        self.assertEqual(ConfigurationSauvegarde.objects.count(), 0)
        config1 = obtenir_configuration()
        config2 = obtenir_configuration()
        self.assertEqual(config1.id, 1)
        self.assertEqual(config2.id, 1)
        self.assertEqual(ConfigurationSauvegarde.objects.count(), 1)

    def test_save_plafonne_jour_mois_a_28(self):
        config = ConfigurationSauvegarde(jour_mois=31)
        config.save()
        self.assertEqual(config.jour_mois, 28)

    def test_save_force_toujours_pk_1(self):
        # même en instanciant sans pk explicite, une deuxième "nouvelle" config
        # écrase la même ligne plutôt que d'en créer une deuxième (id forcé à 1)
        ConfigurationSauvegarde(frequence='quotidienne').save()
        ConfigurationSauvegarde(frequence='mensuelle').save()
        self.assertEqual(ConfigurationSauvegarde.objects.count(), 1)
        self.assertEqual(ConfigurationSauvegarde.objects.get().frequence, 'mensuelle')


# ── VUES (configuration / historique / déclenchement manuel) ─────────────────
class SauvegardeViewsTestCase(TestCase):

    def _creer_admin(self, email="admin@example.com", **droits):
        # voir Registration/tests.py::TestDroitsAdminPermissions pour le
        # rappel sur le bootstrap du tout premier compte de la base
        utilisateur = Utilisateur.objects.create(
            nom="Admin", prenom="Test", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000000",
        )
        utilisateur.profil.role = 'admin'
        utilisateur.profil.save()
        valeurs = dict(
            super_admin=False, est_super_super_admin=False,
            gestion_utilisateurs=False, gestion_signalements=False,
            gestion_verifications=False, gestion_categories=False,
            gestion_support=False, gestion_sauvegardes=False,
            gestion_mots_de_passe=False,
        )
        valeurs.update(droits)
        DroitsAdmin.objects.update_or_create(utilisateur=utilisateur, defaults=valeurs)
        return utilisateur

    def _creer_token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle


class TestConfigurationView(SauvegardeViewsTestCase):

    def test_get_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sans_droit@example.com")
        token = self._creer_token(admin)
        response = self.client.get('/Sauvegarde/configuration/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_get_avec_droit(self):
        admin = self._creer_admin("admin_config@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.get('/Sauvegarde/configuration/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('configuration', data)

    def test_put_modifie_la_configuration(self):
        admin = self._creer_admin("admin_config2@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.put(
            '/Sauvegarde/configuration/',
            data=json.dumps({
                'active': True,
                'frequence': 'hebdomadaire',
                'heure_declenchement': '03:30',
                'destination': 'locale',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['configuration']['active'])
        self.assertEqual(data['configuration']['frequence'], 'hebdomadaire')
        self.assertEqual(data['configuration']['heure_declenchement'], '03:30')

        config = obtenir_configuration()
        self.assertEqual(config.modifie_par, admin)

    def test_put_refuse_frequence_invalide(self):
        admin = self._creer_admin("admin_config3@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.put(
            '/Sauvegarde/configuration/',
            data=json.dumps({'frequence': 'toutes-les-5-minutes'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_put_refuse_heure_mal_formee(self):
        admin = self._creer_admin("admin_config4@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.put(
            '/Sauvegarde/configuration/',
            data=json.dumps({'heure_declenchement': 'pas une heure'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'INVALID_TIME')


class TestHistoriqueView(SauvegardeViewsTestCase):

    def test_historique_refuse_sans_droit(self):
        admin = self._creer_admin("admin_hist1@example.com")
        token = self._creer_token(admin)
        response = self.client.get('/Sauvegarde/historique/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_historique_liste_les_executions(self):
        admin = self._creer_admin("admin_hist2@example.com", gestion_sauvegardes=True)
        HistoriqueSauvegarde.objects.create(
            type_sauvegarde='complete', destination='locale', statut='succes',
            taille_octets=1024, checksum_sha256='abc123',
        )
        token = self._creer_token(admin)
        response = self.client.get('/Sauvegarde/historique/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['historique']), 1)
        self.assertEqual(data['historique'][0]['statut'], 'succes')


# ── DÉCLENCHEMENT RÉEL (destination locale — pas de dépendance externe) ──────
# SAUVEGARDE_ROOT redirigé vers un dossier temporaire : ne doit jamais écrire
# dans le vrai dossier `sauvegardes/` du projet pendant les tests.
class TestDeclencherSauvegarde(SauvegardeViewsTestCase):

    def setUp(self):
        self._dossier_temp = tempfile.mkdtemp(prefix="rht_test_sauvegarde_")
        self._override = override_settings(SAUVEGARDE_ROOT=Path(self._dossier_temp))
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._dossier_temp, ignore_errors=True)

    def test_declencher_locale_succes(self):
        admin = self._creer_admin("admin_declencher@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)

        response = self.client.post(
            '/Sauvegarde/declencher/',
            data=json.dumps({'type_sauvegarde': 'complete', 'destination': 'locale'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['historique']['statut'], 'succes')
        self.assertEqual(data['historique']['destination'], 'locale')

        entree = HistoriqueSauvegarde.objects.get(id=data['historique']['id'])
        self.assertEqual(entree.declenche_par, admin)
        self.assertIsNotNone(entree.chemin_fichier_local)
        # le fichier .rhtbackup a bien été écrit sur disque (dossier temporaire)
        self.assertTrue(Path(entree.chemin_fichier_local).exists())

        # et son contenu est bien un .rhtbackup valide, déchiffrable
        contenu = Path(entree.chemin_fichier_local).read_bytes()
        self.assertTrue(contenu.startswith(b'RHTBK1'))
        donnees = dechiffrer_archive(contenu)
        self.assertIsInstance(donnees, bytes)

    def test_declencher_refuse_type_invalide(self):
        admin = self._creer_admin("admin_declencher2@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.post(
            '/Sauvegarde/declencher/',
            data=json.dumps({'type_sauvegarde': 'exotique', 'destination': 'locale'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_declencher_refuse_sans_droit(self):
        admin = self._creer_admin("admin_declencher3@example.com")  # aucun droit
        token = self._creer_token(admin)
        response = self.client.post(
            '/Sauvegarde/declencher/',
            data=json.dumps({'destination': 'locale'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)


# ── TÉLÉCHARGER UNE SAUVEGARDE (historique_telecharger) ───────────────────────
class TestHistoriqueTelecharger(SauvegardeViewsTestCase):

    def setUp(self):
        self._dossier_temp = tempfile.mkdtemp(prefix="rht_test_telecharger_")
        self._override = override_settings(SAUVEGARDE_ROOT=Path(self._dossier_temp))
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._dossier_temp, ignore_errors=True)

    def _creer_sauvegarde_locale_reelle(self, admin):
        """Déclenche une vraie sauvegarde locale (voir TestDeclencherSauvegarde
        ci-dessus) pour obtenir une HistoriqueSauvegarde avec un vrai fichier
        .rhtbackup sur disque — plus fidèle qu'un HistoriqueSauvegarde.objects.
        create() avec un chemin bidon pour tester le téléchargement."""
        token = self._creer_token(admin)
        response = self.client.post(
            '/Sauvegarde/declencher/',
            data=json.dumps({'type_sauvegarde': 'complete', 'destination': 'locale'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        return HistoriqueSauvegarde.objects.get(id=json.loads(response.content)['historique']['id'])

    def test_historique_telecharger_succes(self):
        admin = self._creer_admin("admin_telecharger1@example.com", gestion_sauvegardes=True)
        entree = self._creer_sauvegarde_locale_reelle(admin)
        token = self._creer_token(admin)

        response = self.client.get(f'/Sauvegarde/historique/{entree.id}/telecharger/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment', response['Content-Disposition'])
        contenu = b''.join(response.streaming_content)
        self.assertTrue(contenu.startswith(b'RHTBK1'))

    def test_historique_telecharger_introuvable(self):
        admin = self._creer_admin("admin_telecharger2@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.get('/Sauvegarde/historique/999999/telecharger/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 404)

    def test_historique_telecharger_refuse_si_echec(self):
        admin = self._creer_admin("admin_telecharger3@example.com", gestion_sauvegardes=True)
        entree = HistoriqueSauvegarde.objects.create(
            type_sauvegarde='complete', destination='locale', statut='echec', message_erreur="x",
        )
        token = self._creer_token(admin)
        response = self.client.get(f'/Sauvegarde/historique/{entree.id}/telecharger/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 400)

    def test_historique_telecharger_refuse_sans_droit(self):
        admin = self._creer_admin("admin_telecharger4@example.com")  # aucun droit
        entree = HistoriqueSauvegarde.objects.create(
            type_sauvegarde='complete', destination='locale', statut='succes',
            chemin_fichier_local='/chemin/bidon',
        )
        token = self._creer_token(admin)
        response = self.client.get(f'/Sauvegarde/historique/{entree.id}/telecharger/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_historique_telecharger_refuse_sans_token(self):
        response = self.client.get('/Sauvegarde/historique/1/telecharger/')
        self.assertEqual(response.status_code, 401)


# ── RESTAURATION (restaurer_analyser/restaurer_confirmer) ────────────────────
# SAUVEGARDE_ROOT et MEDIA_ROOT redirigés vers des dossiers temporaires :
# confirmer_restauration() (Sauvegarde/services/restauration_service.py)
# prend d'abord une VRAIE sauvegarde de sécurité (écriture sur
# SAUVEGARDE_ROOT) avant de charger les données, et restaurerait de vrais
# fichiers media sous MEDIA_ROOT si l'archive en contenait — jamais dans les
# dossiers réels du projet pendant les tests.
class TestRestauration(SauvegardeViewsTestCase):

    def setUp(self):
        self._dossier_sauvegarde = tempfile.mkdtemp(prefix="rht_test_restore_sauvegarde_")
        self._dossier_media = tempfile.mkdtemp(prefix="rht_test_restore_media_")
        self._override = override_settings(
            SAUVEGARDE_ROOT=Path(self._dossier_sauvegarde),
            MEDIA_ROOT=Path(self._dossier_media),
        )
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._dossier_sauvegarde, ignore_errors=True)
        shutil.rmtree(self._dossier_media, ignore_errors=True)

    def _obtenir_archive_valide(self, admin):
        """Génère une vraie archive .rhtbackup chiffrée (même contenu qu'une
        sauvegarde réelle du jeu de données de test courant) à envoyer comme
        fichier uploadé à restaurer_analyser/restaurer_confirmer — plus
        fidèle qu'une archive construite à la main."""
        token = self._creer_token(admin)
        response = self.client.post(
            '/Sauvegarde/declencher/',
            data=json.dumps({'type_sauvegarde': 'complete', 'destination': 'locale'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        entree = HistoriqueSauvegarde.objects.get(id=json.loads(response.content)['historique']['id'])
        return Path(entree.chemin_fichier_local).read_bytes()

    def test_restaurer_analyser_succes(self):
        super_admin = self._creer_admin("admin_restore1@example.com", super_admin=True)
        archive = self._obtenir_archive_valide(super_admin)
        token = self._creer_token(super_admin)

        response = self.client.post(
            '/Sauvegarde/restaurer/analyser/',
            data={'fichier': SimpleUploadedFile('sauvegarde.rhtbackup', archive)},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('nombre_enregistrements', data['resume'])
        self.assertGreater(data['resume']['nombre_enregistrements'], 0)

    def test_restaurer_analyser_refuse_archive_invalide(self):
        super_admin = self._creer_admin("admin_restore2@example.com", super_admin=True)
        token = self._creer_token(super_admin)

        response = self.client.post(
            '/Sauvegarde/restaurer/analyser/',
            data={'fichier': SimpleUploadedFile('pasunearchive.rhtbackup', b'contenu quelconque')},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'INVALID_BACKUP_FILE')

    def test_restaurer_analyser_refuse_sans_fichier(self):
        super_admin = self._creer_admin("admin_restore3@example.com", super_admin=True)
        token = self._creer_token(super_admin)
        response = self.client.post('/Sauvegarde/restaurer/analyser/', data={}, HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 400)

    def test_restaurer_analyser_refuse_gestion_sauvegardes_seul(self):
        # réservé au super admin, gestion_sauvegardes seul ne suffit pas
        # (contrairement à configuration/declencher/historique)
        admin = self._creer_admin("admin_restore4@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.post(
            '/Sauvegarde/restaurer/analyser/',
            data={'fichier': SimpleUploadedFile('x.rhtbackup', b'x')},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_restaurer_confirmer_succes(self):
        super_admin = self._creer_admin("admin_restore5@example.com", super_admin=True)
        archive = self._obtenir_archive_valide(super_admin)
        token = self._creer_token(super_admin)

        response = self.client.post(
            '/Sauvegarde/restaurer/confirmer/',
            data={'fichier': SimpleUploadedFile('sauvegarde.rhtbackup', archive)},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertGreater(data['resultat']['nombre_enregistrements_restaures'], 0)
        # une sauvegarde de sécurité de l'état d'avant a bien été prise
        self.assertTrue(HistoriqueSauvegarde.objects.filter(id=data['resultat']['sauvegarde_securite_id']).exists())
        self.assertTrue(HistoriqueSauvegarde.objects.get(id=data['resultat']['sauvegarde_securite_id']).est_sauvegarde_securite)

    def test_restaurer_confirmer_refuse_archive_invalide(self):
        super_admin = self._creer_admin("admin_restore6@example.com", super_admin=True)
        token = self._creer_token(super_admin)

        response = self.client.post(
            '/Sauvegarde/restaurer/confirmer/',
            data={'fichier': SimpleUploadedFile('pasunearchive.rhtbackup', b'contenu quelconque')},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'RESTORE_FAILED')

    def test_restaurer_confirmer_refuse_sans_droit(self):
        admin = self._creer_admin("admin_restore7@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.post(
            '/Sauvegarde/restaurer/confirmer/',
            data={'fichier': SimpleUploadedFile('x.rhtbackup', b'x')},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_restaurer_confirmer_refuse_sans_token(self):
        response = self.client.post('/Sauvegarde/restaurer/confirmer/', data={})
        self.assertEqual(response.status_code, 401)


# ── SERVICE GOOGLE DRIVE (google_drive_service.py) ────────────────────────────
# requests/Flow/Credentials.refresh entièrement mockés — aucun appel réseau
# réel vers Google. GOOGLE_DRIVE_CLIENT_ID/SECRET/REDIRECT_URI sont déjà
# configurés en .env.dev (nécessaire pour dépasser _verifier_identifiants_configures,
# voir Sauvegarde/services/google_drive_service.py), donc pas besoin de les
# fournir explicitement sauf dans les tests "non configuré" ci-dessous.
class TestGoogleDriveService(TestCase):

    def _config_connectee(self, dossier_id=None):
        config = obtenir_configuration()
        config.google_drive_connecte = True
        config.google_drive_refresh_token_chiffre = chiffrer_texte("un-refresh-token")
        config.google_drive_dossier_id = dossier_id
        config.save()
        return config

    # ── identifiants non configurés ──
    @override_settings(GOOGLE_DRIVE_CLIENT_ID=None, GOOGLE_DRIVE_CLIENT_SECRET=None)
    def test_construire_url_autorisation_refuse_sans_identifiants(self):
        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.construire_url_autorisation()

    @override_settings(GOOGLE_DRIVE_CLIENT_ID=None, GOOGLE_DRIVE_CLIENT_SECRET=None)
    def test_uploader_sauvegarde_refuse_sans_identifiants_meme_connecte(self):
        self._config_connectee()
        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.uploader_sauvegarde(b'contenu', 'x.rhtbackup')

    # ── construire_url_autorisation / echanger_code_contre_refresh_token (Flow mocké) ──
    @patch('Sauvegarde.services.google_drive_service.Flow')
    def test_construire_url_autorisation_succes(self, MockFlow):
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ('https://accounts.google.com/o/oauth2/auth?...', 'ignore')
        mock_flow.code_verifier = 'verifier-abc'
        MockFlow.from_client_config.return_value = mock_flow

        url, state, code_verifier = google_drive_service.construire_url_autorisation()
        self.assertTrue(url.startswith('https://accounts.google.com'))
        self.assertTrue(state)   # jeton aléatoire non vide
        self.assertEqual(code_verifier, 'verifier-abc')

    @patch('Sauvegarde.services.google_drive_service.Flow')
    def test_echanger_code_contre_refresh_token_succes(self, MockFlow):
        mock_flow = MagicMock()
        mock_flow.credentials.refresh_token = 'nouveau-refresh-token'
        MockFlow.from_client_config.return_value = mock_flow

        resultat = google_drive_service.echanger_code_contre_refresh_token('un-code', 'un-verifier')
        self.assertEqual(resultat, 'nouveau-refresh-token')
        mock_flow.fetch_token.assert_called_once_with(code='un-code')

    @patch('Sauvegarde.services.google_drive_service.Flow')
    def test_echanger_code_contre_refresh_token_refuse_sans_refresh_token(self, MockFlow):
        mock_flow = MagicMock()
        mock_flow.credentials.refresh_token = None  # Google n'a rien renvoyé (déjà consenti sans 'consent' forcé)
        MockFlow.from_client_config.return_value = mock_flow

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.echanger_code_contre_refresh_token('code', 'verifier')

    @patch('Sauvegarde.services.google_drive_service.Flow')
    def test_echanger_code_contre_refresh_token_echec_de_lechange(self, MockFlow):
        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = Exception("invalid_grant: code déjà utilisé")
        MockFlow.from_client_config.return_value = mock_flow

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.echanger_code_contre_refresh_token('code', 'verifier')

    # ── _obtenir_credentials (via uploader_sauvegarde/telecharger_sauvegarde_en_flux) ──
    def test_uploader_sauvegarde_refuse_si_non_connecte(self):
        config = obtenir_configuration()
        config.google_drive_connecte = False
        config.save()

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.uploader_sauvegarde(b'contenu', 'x.rhtbackup')

    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_uploader_sauvegarde_deconnecte_si_refresh_token_revoque(self, mock_refresh):
        self._config_connectee()
        mock_refresh.side_effect = Exception("invalid_grant: token has been expired or revoked")

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.uploader_sauvegarde(b'contenu', 'x.rhtbackup')

        # _obtenir_credentials a appelé deconnecter() en interne (voir docstring) —
        # une reconnexion complète sera nécessaire, jamais réessayée automatiquement
        config = obtenir_configuration()
        self.assertFalse(config.google_drive_connecte)
        self.assertIsNone(config.google_drive_refresh_token_chiffre)

    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_uploader_sauvegarde_ne_deconnecte_pas_sur_erreur_reseau_transitoire(self, mock_refresh):
        self._config_connectee()
        mock_refresh.side_effect = Exception("Connection timed out")

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.uploader_sauvegarde(b'contenu', 'x.rhtbackup')

        # erreur transitoire (ni 'invalid_grant' ni 'unauthorized_client') —
        # la connexion reste active, un nouvel essai peut réussir sans reconnexion
        config = obtenir_configuration()
        self.assertTrue(config.google_drive_connecte)

    # ── upload complet (dossier + session resumable + envoi par blocs) ──
    @patch('Sauvegarde.services.google_drive_service.requests.put')
    @patch('Sauvegarde.services.google_drive_service.requests.post')
    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_uploader_sauvegarde_succes_cree_le_dossier_et_envoie(self, mock_refresh, mock_post, mock_put):
        self._config_connectee(dossier_id=None)   # dossier pas encore créé

        # 1er appel requests.post : création du dossier ; 2e : initiation de l'upload resumable
        reponse_dossier = MagicMock(status_code=200)
        reponse_dossier.json.return_value = {'id': 'dossier-id-123'}
        reponse_init = MagicMock(status_code=200, headers={'Location': 'https://upload.example/session-abc'})
        mock_post.side_effect = [reponse_dossier, reponse_init]

        # un seul bloc (contenu petit) confirmé directement par Google (200)
        reponse_upload = MagicMock(status_code=200)
        reponse_upload.json.return_value = {'id': 'fichier-id-456'}
        mock_put.return_value = reponse_upload

        fichier_id = google_drive_service.uploader_sauvegarde(b'petit contenu', 'sauvegarde.rhtbackup')
        self.assertEqual(fichier_id, 'fichier-id-456')

        config = obtenir_configuration()
        self.assertEqual(config.google_drive_dossier_id, 'dossier-id-123')  # mémorisé pour la prochaine fois

    @patch('Sauvegarde.services.google_drive_service.requests.post')
    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_uploader_sauvegarde_reutilise_le_dossier_existant(self, mock_refresh, mock_post):
        self._config_connectee(dossier_id='dossier-deja-cree')
        reponse_init = MagicMock(status_code=400, text="peu importe, on vérifie juste l'appel")
        mock_post.return_value = reponse_init

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.uploader_sauvegarde(b'x', 'x.rhtbackup')

        # un seul appel requests.post (initiation) — pas de création de dossier,
        # google_drive_dossier_id étant déjà renseigné
        self.assertEqual(mock_post.call_count, 1)

    @patch('Sauvegarde.services.google_drive_service.requests.post')
    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_initier_upload_refuse_sans_en_tete_location(self, mock_refresh, mock_post):
        self._config_connectee(dossier_id='dossier-x')
        mock_post.return_value = MagicMock(status_code=200, headers={})   # pas de Location

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.uploader_sauvegarde(b'x', 'x.rhtbackup')

    @patch('Sauvegarde.services.google_drive_service.requests.put')
    @patch('Sauvegarde.services.google_drive_service.requests.post')
    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_envoyer_par_blocs_leve_apres_trop_dechecs_reseau(self, mock_refresh, mock_post, mock_put):
        self._config_connectee(dossier_id='dossier-x')
        mock_post.return_value = MagicMock(status_code=200, headers={'Location': 'https://upload.example/session'})
        mock_put.side_effect = google_drive_service.requests.exceptions.RequestException("coupure réseau")

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.uploader_sauvegarde(b'contenu', 'x.rhtbackup')

    # ── téléchargement depuis Drive ──
    @patch('Sauvegarde.services.google_drive_service.requests.get')
    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_telecharger_sauvegarde_en_flux_succes(self, mock_refresh, mock_get):
        self._config_connectee()
        reponse = MagicMock(status_code=200)
        reponse.raw = MagicMock()
        mock_get.return_value = reponse

        flux = google_drive_service.telecharger_sauvegarde_en_flux('fichier-id-789')
        self.assertIsNotNone(flux)
        self.assertTrue(flux.decode_content)

    @patch('Sauvegarde.services.google_drive_service.requests.get')
    @patch('Sauvegarde.services.google_drive_service.Credentials.refresh')
    def test_telecharger_sauvegarde_en_flux_refuse_si_echec_api(self, mock_refresh, mock_get):
        self._config_connectee()
        mock_get.return_value = MagicMock(status_code=404, text="File not found")

        with self.assertRaises(ErreurGoogleDrive):
            google_drive_service.telecharger_sauvegarde_en_flux('inexistant')

    # ── déconnexion ──
    def test_deconnecter_efface_la_connexion(self):
        self._config_connectee(dossier_id='dossier-x')
        google_drive_service.deconnecter()

        config = obtenir_configuration()
        self.assertFalse(config.google_drive_connecte)
        self.assertIsNone(config.google_drive_refresh_token_chiffre)
        self.assertIsNone(config.google_drive_dossier_id)


# ── VUES GOOGLE DRIVE (google_autoriser/google_callback/google_deconnecter) ──
class TestGoogleDriveViews(SauvegardeViewsTestCase):

    def test_google_autoriser_succes(self):
        admin = self._creer_admin("gdrive_v1@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)

        with patch('Sauvegarde.services.google_drive_service.Flow') as MockFlow:
            mock_flow = MagicMock()
            mock_flow.authorization_url.return_value = ('https://accounts.google.com/o/oauth2/auth?x=1', 'ignore')
            mock_flow.code_verifier = 'verifier-xyz'
            MockFlow.from_client_config.return_value = mock_flow

            response = self.client.get('/Sauvegarde/google/autoriser/', HTTP_AUTHORIZATION=f'Token {token}')

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['url_autorisation'].startswith('https://accounts.google.com'))

    def test_google_autoriser_refuse_sans_droit(self):
        admin = self._creer_admin("gdrive_v2@example.com")  # aucun droit
        token = self._creer_token(admin)
        response = self.client.get('/Sauvegarde/google/autoriser/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    @override_settings(GOOGLE_DRIVE_CLIENT_ID=None, GOOGLE_DRIVE_CLIENT_SECRET=None)
    def test_google_autoriser_refuse_si_non_configure(self):
        admin = self._creer_admin("gdrive_v3@example.com", gestion_sauvegardes=True)
        token = self._creer_token(admin)
        response = self.client.get('/Sauvegarde/google/autoriser/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'GOOGLE_DRIVE_NOT_CONFIGURED')

    def test_google_callback_succes_redirige_et_connecte(self):
        admin = self._creer_admin("gdrive_v4@example.com", gestion_sauvegardes=True)
        cache.set('gdrive_oauth_state:etat-test-1', {'admin_id': admin.id, 'code_verifier': 'verif-1'}, timeout=600)

        with patch.object(google_drive_service, 'echanger_code_contre_refresh_token', return_value='refresh-token-abc'):
            response = self.client.get('/Sauvegarde/google/callback/?code=un-code&state=etat-test-1')

        self.assertEqual(response.status_code, 302)
        self.assertIn('google=connecte', response.url)
        config = obtenir_configuration()
        self.assertTrue(config.google_drive_connecte)
        # le state est consommé (une seule utilisation possible)
        self.assertIsNone(cache.get('gdrive_oauth_state:etat-test-1'))

    def test_google_callback_redirige_refuse_si_consentement_refuse(self):
        response = self.client.get('/Sauvegarde/google/callback/?error=access_denied')
        self.assertEqual(response.status_code, 302)
        self.assertIn('google=refuse', response.url)

    def test_google_callback_redirige_erreur_si_state_inconnu(self):
        response = self.client.get('/Sauvegarde/google/callback/?code=x&state=etat-jamais-vu')
        self.assertEqual(response.status_code, 302)
        self.assertIn('google=erreur', response.url)

    def test_google_callback_redirige_erreur_si_echange_echoue(self):
        admin = self._creer_admin("gdrive_v5@example.com", gestion_sauvegardes=True)
        cache.set('gdrive_oauth_state:etat-test-2', {'admin_id': admin.id, 'code_verifier': 'verif-2'}, timeout=600)

        with patch.object(google_drive_service, 'echanger_code_contre_refresh_token', side_effect=ErreurGoogleDrive("échec")):
            response = self.client.get('/Sauvegarde/google/callback/?code=un-code&state=etat-test-2')

        self.assertEqual(response.status_code, 302)
        self.assertIn('google=erreur', response.url)

    def test_google_deconnecter_succes(self):
        admin = self._creer_admin("gdrive_v6@example.com", gestion_sauvegardes=True)
        config = obtenir_configuration()
        config.google_drive_connecte = True
        config.google_drive_refresh_token_chiffre = chiffrer_texte("x")
        config.save()
        token = self._creer_token(admin)

        response = self.client.post('/Sauvegarde/google/deconnecter/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertFalse(config.google_drive_connecte)

    def test_google_deconnecter_refuse_sans_droit(self):
        admin = self._creer_admin("gdrive_v7@example.com")
        token = self._creer_token(admin)
        response = self.client.post('/Sauvegarde/google/deconnecter/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_google_deconnecter_refuse_sans_token(self):
        response = self.client.post('/Sauvegarde/google/deconnecter/')
        self.assertEqual(response.status_code, 401)
