import base64
import json
import secrets
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from .models import (
    Utilisateur, Profil, Token, InscriptionEnAttente, CodeReinitialisation,
    DroitsAdmin, Entreprise, CompteSupprime, DemandeAdministrative, DemandeVerification,
    JournalAudit, enregistrer_audit, CleChiffrementUtilisateur,
    haser_password, verifier_password,
    verifier_droit_admin, peut_agir_sur_admin, peut_reinitialiser_mdp,
)


def _image_upload(nom="doc.png"):
    """Même principe que Produits/tests.py::_image_upload — un fichier
    multipart avec une VRAIE image PNG décodable (1x1 pixel), requis par
    valider_fichier_upload_django (soumettre_verification exige un contenu
    Pillow-décodable pour document_recto/selfie)."""
    tampon = BytesIO()
    Image.new('RGB', (1, 1), color='green').save(tampon, format='PNG')
    tampon.seek(0)
    return SimpleUploadedFile(nom, tampon.read(), content_type='image/png')


def _image_base64(nom="photo.png"):
    """Même principe que Produits/tests.py::_image_upload, mais encodé en
    base64 (format attendu par _enregistrer_photo_profil/_enregistrer_logo_entreprise,
    Registration/views.py — contrairement aux photos de produit qui sont en
    multipart)."""
    tampon = BytesIO()
    Image.new('RGB', (1, 1), color='blue').save(tampon, format='PNG')
    return {'content': base64.b64encode(tampon.getvalue()).decode(), 'filename': nom}

# Create your tests here.
class TestPasswordHash(TestCase):

    def test_hasher_et_verifier_password(self):
        password = "secret123"
        hashed = haser_password(password)

        self.assertTrue(verifier_password(password, hashed))
        self.assertFalse(verifier_password("mauvais", hashed))

class TestUtilisateurModel(TestCase):

    def test_creation_utilisateur(self):
        utilisateur = Utilisateur.objects.create(
            nom          = "Doe",
            prenom       = "John",
            email        = "john.doe@example.com",
            mot_de_passe = haser_password("secret123"),
            telephone    = "1234567890"
        )
        self.assertEqual(utilisateur.nom, "Doe")
        self.assertEqual(utilisateur.prenom, "John")
        self.assertEqual(utilisateur.email, "john.doe@example.com")
        self.assertTrue(verifier_password("secret123", utilisateur.mot_de_passe))
        self.assertFalse(utilisateur.est_actif)
        self.assertIsNotNone(utilisateur.date_inscription)
    def test_modifier_est_actif(self):
        utilisateur = Utilisateur.objects.create(
            nom          = "Doe",
            prenom       = "Jane",
            email        = "jane.doe@example.com",
            mot_de_passe = haser_password("secret123"),
            telephone    = "0987654321"
        )
        self.assertFalse(utilisateur.est_actif)
        utilisateur.modifier_est_actif()
        self.assertTrue(utilisateur.est_actif)
    def test_modifier_mot_de_passe(self):
        utilisateur = Utilisateur.objects.create(
            nom          = "Smith",
            prenom       = "Will",
            email        = "will.smith@example.com",
            mot_de_passe = haser_password("secret123"),
            telephone    = "1111111111"
        )
        self.assertTrue(verifier_password("secret123", utilisateur.mot_de_passe))
        utilisateur.modifier_mot_de_passe("nouveaumotdepasse")
        self.assertTrue(verifier_password("nouveaumotdepasse", utilisateur.mot_de_passe))
        self.assertFalse(verifier_password("secret123", utilisateur.mot_de_passe))

class TestProfilModel(TestCase):

    def test_creation_profil(self):
        utilisateur = Utilisateur.objects.create(
            nom          = "Brown",
            prenom       = "Charlie",
            email        = "charlie.brown@example.com",
            mot_de_passe = haser_password("secret123"),
            telephone    = "2222222222"
        )
        profil = utilisateur.profil
        profil.bio  = "Je suis Charlie Brown."
        profil.role = "acheteur"
        profil.save()
        self.assertEqual(profil.utilisateur, utilisateur)
        self.assertEqual(profil.bio, "Je suis Charlie Brown.")
        self.assertEqual(profil.role, "acheteur")
        self.assertIsNotNone(profil.date_maj)
    def test_modifier_role(self):
        utilisateur = Utilisateur.objects.create(
            nom          = "Green",
            prenom       = "Lucy",
            email        = "lucy.green@example.com",
            mot_de_passe = haser_password("secret123"),
            telephone    = "3333333333"
        )
        profil = utilisateur.profil
        profil.bio  = "Je suis Charlie Brown."
        profil.role = "acheteur"
        profil.save()
        self.assertEqual(profil.role, "acheteur")
        profil.convertir_en_vendeur()
        self.assertEqual(profil.role, "vendeur")
        profil.convertir_en_acheteur()
        self.assertEqual(profil.role, "acheteur")
        profil.role = "admin"
        profil.save()   
        self.assertEqual(profil.role, "admin")


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestRegistrationViews(TestCase):
    """
    EMAIL_BACKEND forcé en mémoire (locmem) pour toute cette classe — sans ça,
    sinscrire (Registration/views.py) enverrait un VRAI email via le SMTP
    Gmail configuré en .env.dev à chaque exécution des tests (lent, et
    utilise les vrais identifiants EMAIL_HOST_USER/PASSWORD pour de vrai).
    """

    def setUp(self):
        # les tokens sont des lignes en base (modèle Token, voir models.py) —
        # TestCase enveloppe déjà chaque test dans une transaction annulée à
        # la fin, plus besoin de nettoyage manuel ici
        pass

    def _create_utilisateur(self, email="john.doe@example.com"):
        utilisateur = Utilisateur.objects.create(
            nom          = "Doe",
            prenom       = "John",
            email        = email,
            mot_de_passe = haser_password("secret123"),
            telephone    = "1234567890"
        )
        profil = utilisateur.profil
        profil.bio  = "Je suis Charlie Brown."
        profil.role = "acheteur"
        profil.save()
        return utilisateur

    def _create_token_for(self, utilisateur):
        token = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=token)
        return token

    def test_sinscrire_puis_confirmerInscription_creates_user_and_returns_token(self):
        # inscription en DEUX étapes depuis l'ajout de la vérification par
        # email (voir sinscrire/confirmerInscription, Registration/views.py) :
        # sinscrire ne crée qu'une InscriptionEnAttente + envoie l'email, le
        # compte réel n'existe qu'après confirmerInscription (lien reçu par
        # email, simulé ici en lisant directement le token en base)
        payload = {
            'nom': 'Doe',
            'prenom': 'Jane',
            'email': 'jane.doe@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '0987654321',
            'adresse': 'Rue 123',
            'commune': 'Port-au-Prince',
            'ville': 'Port-au-Prince',
            'pays': 'Haiti',
            'role': 'acheteur'
        }

        response = self.client.post(
            '/Registration/inscription/',
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['email'], payload['email'])
        self.assertFalse(Utilisateur.objects.filter(email=payload['email']).exists())

        inscription = InscriptionEnAttente.objects.get(email=payload['email'])

        confirm_response = self.client.post(
            '/Registration/inscription/confirmer/',
            data=json.dumps({'token': inscription.token}),
            content_type='application/json'
        )

        self.assertEqual(confirm_response.status_code, 201)
        confirm_data = json.loads(confirm_response.content)
        self.assertEqual(confirm_data['message'], 'Compte activé avec succès')
        self.assertIn('token', confirm_data)
        self.assertTrue(Token.objects.filter(cle=confirm_data['token']).exists())
        self.assertEqual(confirm_data['utilisateur']['email'], payload['email'])
        self.assertTrue(Utilisateur.objects.filter(email=payload['email']).exists())

    def test_seConnecter_activates_user_and_returns_token(self):
        utilisateur = self._create_utilisateur(email='login@example.com')
        self.assertFalse(utilisateur.est_actif)

        response = self.client.post(
            '/Registration/connexion/',
            data=json.dumps({
                'email': utilisateur.email,
                'mot_de_passe': 'secret123'
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message'], 'Utilisateur connecté avec succès')
        self.assertIn('token', data)
        self.assertTrue(Token.objects.filter(cle=data['token']).exists())

        utilisateur.refresh_from_db()
        self.assertTrue(utilisateur.est_actif)

    def test_seConnecter_rejects_invalid_password(self):
        utilisateur = self._create_utilisateur(email='wrongpass@example.com')

        response = self.client.post(
            '/Registration/connexion/',
            data=json.dumps({
                'email': utilisateur.email,
                'mot_de_passe': 'mauvais'
            }),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 401)
        data = json.loads(response.content)
        self.assertEqual(data['error'], "Le mot de passe n'existe pas ou incorrect")

    def test_seDeconnecter_invalid_token(self):
        response = self.client.post(
            '/Registration/deconnexion/',
            HTTP_AUTHORIZATION='Token invalide'
        )

        self.assertEqual(response.status_code, 401)
        data = json.loads(response.content)
        self.assertEqual(data['error'], "Token d'authentification invalide")

    def test_seDeconnecter_deactivates_user_and_removes_token(self):
        utilisateur = self._create_utilisateur(email='logout@example.com')
        token = self._create_token_for(utilisateur)
        utilisateur.est_actif = True
        utilisateur.save()

        response = self.client.post(
            '/Registration/deconnexion/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message'], 'Utilisateur déconnecté avec succès')
        self.assertFalse(Token.objects.filter(cle=token).exists())

        utilisateur.refresh_from_db()
        self.assertFalse(utilisateur.est_actif)

    def test_profilAfficher_get(self):
        utilisateur = self._create_utilisateur(email='profile@example.com')
        token = self._create_token_for(utilisateur)

        get_response = self.client.get(
            '/Registration/profil/',
            HTTP_AUTHORIZATION=f'Token {token}'
        )
        self.assertEqual(get_response.status_code, 200)
        get_data = json.loads(get_response.content)
        self.assertEqual(get_data['utilisateur']['email'], utilisateur.email)
        self.assertEqual(get_data['profil']['role'], 'acheteur')

    def test_modifierProfil_updates_profil_fields(self):
        # profilAfficher (/Registration/profil/) est maintenant GET seul — la
        # mise à jour est un endpoint séparé, modifierProfil (/Registration/
        # modifier-profil/), qui ne touche QUE les champs du Profil (bio,
        # adresse, ville...) : nom/prenom de l'Utilisateur ne sont plus
        # modifiables ici (fixés à l'inscription/vérification KYC, voir
        # modifierProfil, Registration/views.py, qui exclut aussi 'role').
        utilisateur = self._create_utilisateur(email='profile@example.com')
        token = self._create_token_for(utilisateur)

        put_payload = {
            'bio': 'Nouvelle bio',
            'ville': 'Cap-Haïtien'
        }
        put_response = self.client.put(
            '/Registration/modifier-profil/',
            data=json.dumps(put_payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )
        self.assertEqual(put_response.status_code, 200)
        put_data = json.loads(put_response.content)
        self.assertEqual(put_data['message'], 'Profil mis à jour avec succès')
        self.assertEqual(put_data['profil']['bio'], 'Nouvelle bio')
        self.assertEqual(put_data['profil']['ville'], 'Cap-Haïtien')

    def test_modifierMotDePasse_changes_password(self):
        utilisateur = self._create_utilisateur(email='password@example.com')
        token = self._create_token_for(utilisateur)

        response = self.client.put(
            '/Registration/modifier-mdp/',
            data=json.dumps({
                'ancien_mot_de_passe': 'secret123',
                'nouveau_mot_de_passe': 'nouveau456'
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message'], 'Mot de passe modifié avec succès')

        utilisateur.refresh_from_db()
        self.assertTrue(verifier_password('nouveau456', utilisateur.mot_de_passe))

    def test_modifierMotDePasse_rejects_wrong_old_password(self):
        utilisateur = self._create_utilisateur(email='passwordfail@example.com')
        token = self._create_token_for(utilisateur)

        response = self.client.put(
            '/Registration/modifier-mdp/',
            data=json.dumps({
                'ancien_mot_de_passe': 'mauvais',
                'nouveau_mot_de_passe': 'nouveau456'
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 401)
        data = json.loads(response.content)
        self.assertEqual(data['error'], 'Ancien mot de passe incorrect')


# ── PERMISSIONS ADMIN (verifier_droit_admin / peut_agir_sur_admin / peut_reinitialiser_mdp) ──
# Logique de sécurité centrale (Registration/models.py) : toutes les vues
# admin du backend (Registration/Produits/Messagerie) s'appuient dessus.
class TestDroitsAdminPermissions(TestCase):

    # NOTE : le tout premier Utilisateur créé dans une base vide devient
    # automatiquement admin + super_super_admin avec TOUS les droits (voir
    # creer_profil, Registration/signals.py — bootstrap de la plateforme).
    # Chaque test Django tourne dans sa propre transaction annulée (base
    # vide à chaque fois), donc le PREMIER utilisateur créé par N'IMPORTE
    # quel test ci-dessous déclenche ce bootstrap — d'où le update_or_create
    # avec des valeurs par défaut explicites (jamais un simple .create(), qui
    # échouerait avec IntegrityError sur le DroitsAdmin déjà auto-créé par le
    # signal) et le role forcé explicitement (jamais laissé au signal).
    def _creer_admin(self, email, **droits):
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

    def _creer_acheteur(self, email):
        utilisateur = Utilisateur.objects.create(
            nom="Acheteur", prenom="Test", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000001",
        )
        utilisateur.profil.role = 'acheteur'   # au cas où le signal en aurait fait un admin (voir note ci-dessus)
        utilisateur.profil.save()
        return utilisateur

    # ── verifier_droit_admin ──
    def test_verifier_droit_admin_faux_si_pas_admin(self):
        acheteur = self._creer_acheteur("acheteur1@example.com")
        self.assertFalse(verifier_droit_admin(acheteur, 'gestion_utilisateurs'))

    def test_verifier_droit_admin_faux_si_admin_sans_droits_admin(self):
        # profil.role == 'admin' mais aucune ligne DroitsAdmin (cas anormal,
        # ex: promotion incomplète) — ne doit jamais lever, juste refuser
        utilisateur = self._creer_acheteur("admin_sans_droits@example.com")
        utilisateur.profil.role = 'admin'
        utilisateur.profil.save()
        # au cas où ce serait le tout premier compte de la base (bootstrap
        # automatique, voir note plus haut) : supprimer le DroitsAdmin
        # auto-créé pour retomber sur le cas testé ici (admin SANS droits_admin)
        DroitsAdmin.objects.filter(utilisateur=utilisateur).delete()
        self.assertFalse(verifier_droit_admin(utilisateur, 'gestion_utilisateurs'))

    def test_verifier_droit_admin_vrai_si_droit_precis_accorde(self):
        admin = self._creer_admin("admin_droit@example.com", gestion_categories=True)
        self.assertTrue(verifier_droit_admin(admin, 'gestion_categories'))
        self.assertFalse(verifier_droit_admin(admin, 'gestion_utilisateurs'))

    def test_verifier_droit_admin_super_admin_a_tous_les_droits(self):
        admin = self._creer_admin("admin_super@example.com", super_admin=True)
        self.assertTrue(verifier_droit_admin(admin, 'gestion_categories'))
        self.assertTrue(verifier_droit_admin(admin, 'gestion_mots_de_passe'))

    def test_verifier_droit_admin_none_ne_leve_pas(self):
        self.assertFalse(verifier_droit_admin(None, 'gestion_utilisateurs'))

    # ── peut_agir_sur_admin ──
    def test_peut_agir_sur_admin_personne_ne_touche_super_super_admin(self):
        super_super = self._creer_admin("boss@example.com", est_super_super_admin=True, super_admin=True)
        autre_super = self._creer_admin("autre_boss@example.com", super_admin=True)
        self.assertFalse(peut_agir_sur_admin(autre_super, super_super))
        self.assertFalse(peut_agir_sur_admin(super_super, super_super))

    def test_peut_agir_sur_admin_super_super_agit_sur_tout_le_monde(self):
        super_super = self._creer_admin("boss2@example.com", est_super_super_admin=True, super_admin=True)
        limite      = self._creer_admin("limite1@example.com")
        tous_droits = self._creer_admin("tousdroits1@example.com", super_admin=True)
        self.assertTrue(peut_agir_sur_admin(super_super, limite))
        self.assertTrue(peut_agir_sur_admin(super_super, tous_droits))

    def test_peut_agir_sur_admin_tous_droits_pas_sur_tous_droits(self):
        a = self._creer_admin("tousdroitsA@example.com", super_admin=True)
        b = self._creer_admin("tousdroitsB@example.com", super_admin=True)
        self.assertFalse(peut_agir_sur_admin(a, b))

    def test_peut_agir_sur_admin_tous_droits_sur_admin_limite(self):
        tous_droits = self._creer_admin("tousdroitsC@example.com", super_admin=True)
        limite      = self._creer_admin("limite2@example.com")
        self.assertTrue(peut_agir_sur_admin(tous_droits, limite))

    def test_peut_agir_sur_admin_limite_ne_peut_agir_sur_personne(self):
        limite1 = self._creer_admin("limite3@example.com")
        limite2 = self._creer_admin("limite4@example.com")
        self.assertFalse(peut_agir_sur_admin(limite1, limite2))

    # ── peut_reinitialiser_mdp (hiérarchie spécifique, plus permissive) ──
    def test_peut_reinitialiser_mdp_delegation_entre_pairs_limites(self):
        avec_droit = self._creer_admin("resetA@example.com", gestion_mots_de_passe=True)
        sans_droit = self._creer_admin("resetB@example.com")
        self.assertTrue(peut_reinitialiser_mdp(avec_droit, sans_droit))
        # l'inverse est faux : sans_droit n'a pas gestion_mots_de_passe
        self.assertFalse(peut_reinitialiser_mdp(sans_droit, avec_droit))

    def test_peut_reinitialiser_mdp_deux_memes_droits_ne_peuvent_pas_s_agir_dessus(self):
        a = self._creer_admin("resetC@example.com", gestion_mots_de_passe=True)
        b = self._creer_admin("resetD@example.com", gestion_mots_de_passe=True)
        self.assertFalse(peut_reinitialiser_mdp(a, b))

    def test_peut_reinitialiser_mdp_tous_droits_sur_limite(self):
        tous_droits = self._creer_admin("resetE@example.com", super_admin=True)
        limite      = self._creer_admin("resetF@example.com")
        self.assertTrue(peut_reinitialiser_mdp(tous_droits, limite))


# ── CODE PIN DE RÉINITIALISATION (CodeReinitialisation.est_valide) ────────────
class TestCodeReinitialisationModel(TestCase):

    def _creer_utilisateur(self, email):
        return Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000002",
        )

    def test_code_valide_si_non_utilise_et_non_expire(self):
        utilisateur = self._creer_utilisateur("code1@example.com")
        code = CodeReinitialisation.objects.create(
            utilisateur=utilisateur, code="1234",
            date_expiration=timezone.now() + timedelta(minutes=15),
        )
        self.assertTrue(code.est_valide())

    def test_code_invalide_si_expire(self):
        utilisateur = self._creer_utilisateur("code2@example.com")
        code = CodeReinitialisation.objects.create(
            utilisateur=utilisateur, code="1234",
            date_expiration=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(code.est_valide())

    def test_code_invalide_si_deja_utilise(self):
        utilisateur = self._creer_utilisateur("code3@example.com")
        code = CodeReinitialisation.objects.create(
            utilisateur=utilisateur, code="1234", utilise=True,
            date_expiration=timezone.now() + timedelta(minutes=15),
        )
        self.assertFalse(code.est_valide())


# ── RÉINITIALISATION DE MOT DE PASSE PAR CODE PIN (vue de bout en bout) ───────
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestReinitialisationMotDePasseViews(TestCase):

    def _creer_utilisateur(self, email):
        return Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("ancienmdp"), telephone="0000000003",
        )

    def test_flux_complet_demander_verifier_valider(self):
        utilisateur = self._creer_utilisateur("resetflow@example.com")

        demande_response = self.client.post(
            '/Registration/reinitialisation/demander/',
            data=json.dumps({'email': utilisateur.email}),
            content_type='application/json',
        )
        self.assertEqual(demande_response.status_code, 200)

        code_obj = CodeReinitialisation.objects.get(utilisateur=utilisateur)

        verif_response = self.client.post(
            '/Registration/reinitialisation/verifier-code/',
            data=json.dumps({'email': utilisateur.email, 'code': code_obj.code}),
            content_type='application/json',
        )
        self.assertEqual(verif_response.status_code, 200)

        valider_response = self.client.post(
            '/Registration/reinitialisation/valider/',
            data=json.dumps({
                'email': utilisateur.email,
                'code': code_obj.code,
                'nouveau_mot_de_passe': 'nouveaumdp123',
            }),
            content_type='application/json',
        )
        self.assertEqual(valider_response.status_code, 200)

        utilisateur.refresh_from_db()
        self.assertTrue(verifier_password('nouveaumdp123', utilisateur.mot_de_passe))

        code_obj.refresh_from_db()
        self.assertTrue(code_obj.utilise)

    def test_valider_rejette_code_deja_utilise(self):
        utilisateur = self._creer_utilisateur("resetflow2@example.com")
        code_obj = CodeReinitialisation.objects.create(
            utilisateur=utilisateur, code="9999", utilise=True,
            date_expiration=timezone.now() + timedelta(minutes=15),
        )

        response = self.client.post(
            '/Registration/reinitialisation/valider/',
            data=json.dumps({
                'email': utilisateur.email,
                'code': '9999',
                'nouveau_mot_de_passe': 'peuimporte',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_demander_rejette_email_inconnu(self):
        response = self.client.post(
            '/Registration/reinitialisation/demander/',
            data=json.dumps({'email': 'inconnu_xyz@example.com'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)


# ── MODIFIER UTILISATEUR / SUPPRIMER PHOTO DE PROFIL ──────────────────────────
class TestModifierUtilisateurEtPhoto(TestCase):

    def _creer_utilisateur(self, email, role='acheteur'):
        utilisateur = Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000020",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def test_modifierUtilisateur_succes_pour_un_acheteur(self):
        utilisateur = self._creer_utilisateur("modif_u1@example.com")
        token = self._token(utilisateur)

        response = self.client.put(
            '/Registration/modifier-utilisateur/',
            data=json.dumps({'nom': 'Nouveau', 'telephone': '9999999999'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        utilisateur.refresh_from_db()
        self.assertEqual(utilisateur.nom, 'Nouveau')
        self.assertEqual(utilisateur.telephone, '9999999999')

    def test_modifierUtilisateur_refuse_changement_email(self):
        utilisateur = self._creer_utilisateur("modif_u2@example.com")
        token = self._token(utilisateur)

        response = self.client.put(
            '/Registration/modifier-utilisateur/',
            data=json.dumps({'email': 'autre@example.com'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'EMAIL_LOCKED')

    def test_modifierUtilisateur_refuse_nom_pour_un_vendeur_verifie(self):
        vendeur = self._creer_utilisateur("modif_u3@example.com", role='vendeur')
        token = self._token(vendeur)

        response = self.client.put(
            '/Registration/modifier-utilisateur/',
            data=json.dumps({'nom': 'Autre nom'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'IDENTITY_LOCKED_AFTER_VERIFICATION')

    def test_modifierUtilisateur_refuse_sans_token(self):
        response = self.client.put(
            '/Registration/modifier-utilisateur/', data=json.dumps({'nom': 'X'}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_supprimerPhotoProfil_succes(self):
        utilisateur = self._creer_utilisateur("modif_u4@example.com")
        utilisateur.profil.photo_profil.save("p.png", ContentFile(b''), save=True)
        token = self._token(utilisateur)

        response = self.client.delete('/Registration/supprimer-photo-profil/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        utilisateur.profil.refresh_from_db()
        self.assertFalse(utilisateur.profil.photo_profil)

    def test_supprimerPhotoProfil_refuse_sans_token(self):
        response = self.client.delete('/Registration/supprimer-photo-profil/')
        self.assertEqual(response.status_code, 401)


# ── ENTREPRISE (CRUD complet) ──────────────────────────────────────────────────
class TestEntrepriseCRUD(TestCase):

    def _creer_utilisateur(self, email, role='vendeur'):
        utilisateur = Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000021",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def test_verifierEntreprise_existe_et_nexiste_pas(self):
        Entreprise.objects.create(
            nom="E", prenom="", email="entreprise_v1@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000022",
            nom_Entreprise="Ferme Verte",
        )
        response_existe = self.client.get('/Registration/entreprise/verifier/?nom_Entreprise=Ferme Verte')
        self.assertTrue(json.loads(response_existe.content)['existe'])

        response_libre = self.client.get('/Registration/entreprise/verifier/?nom_Entreprise=Autre Nom')
        self.assertFalse(json.loads(response_libre.content)['existe'])

    def test_verifierEntreprise_refuse_sans_nom(self):
        response = self.client.get('/Registration/entreprise/verifier/')
        self.assertEqual(response.status_code, 400)

    def test_creerEntreprise_succes(self):
        # inscription entreprise en DEUX étapes, même principe que sinscrire/
        # confirmerInscription ci-dessus (voir test_sinscrire_puis_
        # confirmerInscription_creates_user_and_returns_token) : creerEntreprise
        # ne crée qu'une InscriptionEnAttente + envoie l'email d'activation, le
        # compte Entreprise réel n'existe qu'après confirmerInscription (lien
        # reçu par email, simulé ici en lisant directement le token en base)
        response = self.client.post(
            '/Registration/entreprise/creer/',
            data=json.dumps({
                'nom_Entreprise': 'Agro Haiti', 'email': 'agro@example.com',
                'mot_de_passe': 'secret123', 'telephone': '0000000023',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['email'], 'agro@example.com')
        self.assertFalse(Entreprise.objects.filter(nom_Entreprise='Agro Haiti').exists())

        inscription = InscriptionEnAttente.objects.get(email='agro@example.com')
        confirm_response = self.client.post(
            '/Registration/inscription/confirmer/',
            data=json.dumps({'token': inscription.token}),
            content_type='application/json',
        )
        self.assertEqual(confirm_response.status_code, 201)
        confirm_data = json.loads(confirm_response.content)
        self.assertIn('token', confirm_data)
        self.assertTrue(Entreprise.objects.filter(nom_Entreprise='Agro Haiti').exists())
        self.assertTrue(Token.objects.filter(cle=confirm_data['token']).exists())

    def test_creerEntreprise_avec_logo_base64(self):
        response = self.client.post(
            '/Registration/entreprise/creer/',
            data=json.dumps({
                'nom_Entreprise': 'Agro Logo', 'email': 'agrologo@example.com',
                'mot_de_passe': 'secret123', 'telephone': '0000000024',
                'logo': _image_base64(),
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(Entreprise.objects.filter(nom_Entreprise='Agro Logo').exists())

        inscription = InscriptionEnAttente.objects.get(email='agrologo@example.com')
        confirm_response = self.client.post(
            '/Registration/inscription/confirmer/',
            data=json.dumps({'token': inscription.token}),
            content_type='application/json',
        )
        self.assertEqual(confirm_response.status_code, 201)
        entreprise = Entreprise.objects.get(nom_Entreprise='Agro Logo')
        self.assertTrue(entreprise.logo)

    def test_creerEntreprise_refuse_nom_deja_pris(self):
        Entreprise.objects.create(
            nom="E", prenom="", email="dup1@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000025",
            nom_Entreprise="Nom Pris",
        )
        response = self.client.post(
            '/Registration/entreprise/creer/',
            data=json.dumps({
                'nom_Entreprise': 'Nom Pris', 'email': 'dup2@example.com',
                'mot_de_passe': 'secret123', 'telephone': '0000000026',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_creerEntreprise_refuse_email_deja_pris(self):
        self._creer_utilisateur("email_pris@example.com")
        response = self.client.post(
            '/Registration/entreprise/creer/',
            data=json.dumps({
                'nom_Entreprise': 'Encore Une Entreprise', 'email': 'email_pris@example.com',
                'mot_de_passe': 'secret123', 'telephone': '0000000027',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_creerEntreprise_refuse_logo_invalide(self):
        # le logo n'est validé (décodage/format d'image) qu'à la confirmation
        # (voir _enregistrer_logo_entreprise, appelé depuis confirmerInscription) —
        # creerEntreprise se contente de stocker le base64 tel quel dans
        # donnees_optionnelles, l'étape 1 réussit donc toujours ici
        response = self.client.post(
            '/Registration/entreprise/creer/',
            data=json.dumps({
                'nom_Entreprise': 'Logo Invalide', 'email': 'logoinvalide@example.com',
                'mot_de_passe': 'secret123', 'telephone': '0000000028',
                'logo': {'content': base64.b64encode(b'pas une image').decode(), 'filename': 'x.png'},
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)

        inscription = InscriptionEnAttente.objects.get(email='logoinvalide@example.com')
        confirm_response = self.client.post(
            '/Registration/inscription/confirmer/',
            data=json.dumps({'token': inscription.token}),
            content_type='application/json',
        )
        self.assertEqual(confirm_response.status_code, 400)
        self.assertFalse(Entreprise.objects.filter(nom_Entreprise='Logo Invalide').exists())

    def test_listerEntreprises_scope_a_lutilisateur(self):
        proprietaire = self._creer_utilisateur("proprio1@example.com")
        entreprise = Entreprise.objects.create(
            nom="E", prenom="", email="scope1@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000029",
            nom_Entreprise="Ma Ferme", proprietaire=proprietaire,
        )
        Entreprise.objects.create(
            nom="E", prenom="", email="scope2@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000030",
            nom_Entreprise="Ferme Dun Autre",
        )
        token = self._token(proprietaire)

        response = self.client.get('/Registration/entreprise/lister/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual([e['id'] for e in data['entreprises']], [entreprise.id])

    def test_listerEntreprises_admin_voit_tout(self):
        admin = self._creer_utilisateur("admin_lister_ent@example.com", role='admin')
        Entreprise.objects.create(
            nom="E", prenom="", email="scope3@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000031",
            nom_Entreprise="Ferme Admin View",
        )
        token = self._token(admin)

        response = self.client.get('/Registration/entreprise/lister/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        self.assertEqual(len(data['entreprises']), 1)

    def test_modifierEntreprise_succes(self):
        proprietaire = self._creer_utilisateur("proprio2@example.com")
        entreprise = Entreprise.objects.create(
            nom="E", prenom="", email="mod1@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000032",
            nom_Entreprise="A Modifier", proprietaire=proprietaire,
        )
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/entreprise/modifier/',
            data=json.dumps({'id': entreprise.id, 'description': 'Nouvelle description'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        entreprise.refresh_from_db()
        self.assertEqual(entreprise.description, 'Nouvelle description')

    def test_modifierEntreprise_refuse_nom_si_verifiee(self):
        proprietaire = self._creer_utilisateur("proprio3@example.com")
        entreprise = Entreprise.objects.create(
            nom="E", prenom="", email="mod2@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000033",
            nom_Entreprise="Nom Verrouille", proprietaire=proprietaire, est_verifiee=True,
        )
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/entreprise/modifier/',
            data=json.dumps({'id': entreprise.id, 'nom_Entreprise': 'Nouveau Nom'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'IDENTITY_LOCKED_AFTER_VERIFICATION')

    def test_modifierEntreprise_refuse_entreprise_dun_autre(self):
        proprietaire = self._creer_utilisateur("proprio4@example.com")
        entreprise = Entreprise.objects.create(
            nom="E", prenom="", email="mod3@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000034",
            nom_Entreprise="Pas A Vous", proprietaire=proprietaire,
        )
        autre = self._creer_utilisateur("proprio5@example.com")
        token = self._token(autre)

        response = self.client.put(
            '/Registration/entreprise/modifier/',
            data=json.dumps({'id': entreprise.id, 'description': 'Piraté'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_supprimerEntreprise_succes(self):
        proprietaire = self._creer_utilisateur("proprio6@example.com")
        entreprise = Entreprise.objects.create(
            nom="E", prenom="", email="sup1@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000035",
            nom_Entreprise="A Supprimer", proprietaire=proprietaire,
        )
        token = self._token(proprietaire)

        response = self.client.delete(
            '/Registration/entreprise/supprimer/', data=json.dumps({'id': entreprise.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Entreprise.objects.filter(id=entreprise.id).exists())

    def test_supprimerLogoEntreprise_succes(self):
        proprietaire = self._creer_utilisateur("proprio7@example.com")
        entreprise = Entreprise.objects.create(
            nom="E", prenom="", email="logo1@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000036",
            nom_Entreprise="Avec Logo", proprietaire=proprietaire,
        )
        entreprise.logo.save("logo.png", ContentFile(b''), save=True)
        token = self._token(proprietaire)

        response = self.client.delete(
            '/Registration/entreprise/supprimer-logo/', data=json.dumps({'id': entreprise.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        entreprise.refresh_from_db()
        self.assertFalse(entreprise.logo)

    def test_supprimerLogoEntreprise_introuvable(self):
        proprietaire = self._creer_utilisateur("proprio8@example.com")
        token = self._token(proprietaire)
        response = self.client.delete(
            '/Registration/entreprise/supprimer-logo/', data=json.dumps({'id': 999999}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)


# ── ADMIN — GESTION DES UTILISATEURS ──────────────────────────────────────────
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestAdminGestionUtilisateurs(TestCase):

    def _creer_admin(self, email, **droits):
        utilisateur = Utilisateur.objects.create(
            nom="Admin", prenom="Test", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000040",
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

    def _creer_acheteur(self, email):
        utilisateur = Utilisateur.objects.create(
            nom="Ach", prenom="Eteur", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000041",
        )
        utilisateur.profil.role = 'acheteur'
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def test_listerUtilisateursAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_liste1@example.com")
        token = self._token(admin)
        response = self.client.get('/Registration/admin/utilisateurs/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_listerUtilisateursAdmin_succes(self):
        admin = self._creer_admin("admin_liste2@example.com", gestion_utilisateurs=True)
        self._creer_acheteur("acheteur_liste1@example.com")
        self._creer_acheteur("acheteur_liste2@example.com")
        token = self._token(admin)
        response = self.client.get('/Registration/admin/utilisateurs/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertGreaterEqual(len(data['utilisateurs']), 2)

    def test_toggleBloquerUtilisateur_bloque_puis_debloque(self):
        admin = self._creer_admin("admin_bloc1@example.com", gestion_utilisateurs=True)
        cible = self._creer_acheteur("cible_bloc1@example.com")
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/utilisateurs/bloquer/',
            data=json.dumps({'id': cible.id, 'raison': 'Comportement abusif'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        cible.refresh_from_db()
        self.assertTrue(cible.est_bloquer)

        response2 = self.client.put(
            '/Registration/admin/utilisateurs/bloquer/',
            data=json.dumps({'id': cible.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response2.status_code, 200)
        cible.refresh_from_db()
        self.assertFalse(cible.est_bloquer)

    def test_toggleBloquerUtilisateur_refuse_sans_raison(self):
        admin = self._creer_admin("admin_bloc2@example.com", gestion_utilisateurs=True)
        cible = self._creer_acheteur("cible_bloc2@example.com")
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/utilisateurs/bloquer/',
            data=json.dumps({'id': cible.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_toggleBloquerUtilisateur_refuse_auto_blocage(self):
        admin = self._creer_admin("admin_bloc3@example.com", gestion_utilisateurs=True)
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/utilisateurs/bloquer/',
            data=json.dumps({'id': admin.id, 'raison': 'x'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_toggleBloquerUtilisateur_admin_limite_ne_peut_pas_bloquer_un_autre_admin(self):
        admin1 = self._creer_admin("admin_bloc4@example.com", gestion_utilisateurs=True)
        admin2 = self._creer_admin("admin_bloc5@example.com", gestion_utilisateurs=True)
        token = self._token(admin1)

        response = self.client.put(
            '/Registration/admin/utilisateurs/bloquer/',
            data=json.dumps({'id': admin2.id, 'raison': 'x'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_supprimerUtilisateurAdmin_succes(self):
        admin = self._creer_admin("admin_del1@example.com", gestion_utilisateurs=True)
        cible = self._creer_acheteur("cible_del1@example.com")
        cible_id, cible_email = cible.id, cible.email
        token = self._token(admin)

        response = self.client.delete(
            '/Registration/admin/utilisateurs/supprimer/',
            data=json.dumps({'id': cible_id, 'raison': 'Fraude avérée'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Utilisateur.objects.filter(id=cible_id).exists())
        self.assertTrue(CompteSupprime.objects.filter(email=cible_email).exists())

    def test_supprimerUtilisateurAdmin_refuse_sans_raison(self):
        admin = self._creer_admin("admin_del2@example.com", gestion_utilisateurs=True)
        cible = self._creer_acheteur("cible_del2@example.com")
        token = self._token(admin)

        response = self.client.delete(
            '/Registration/admin/utilisateurs/supprimer/',
            data=json.dumps({'id': cible.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_supprimerUtilisateurAdmin_refuse_auto_suppression(self):
        admin = self._creer_admin("admin_del3@example.com", gestion_utilisateurs=True)
        token = self._token(admin)

        response = self.client.delete(
            '/Registration/admin/utilisateurs/supprimer/',
            data=json.dumps({'id': admin.id, 'raison': 'x'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_reactiverVendeurAdmin_succes(self):
        admin = self._creer_admin("admin_react1@example.com", gestion_utilisateurs=True)
        vendeur = self._creer_acheteur("vendeur_react1@example.com")
        vendeur.profil.role = 'vendeur'
        vendeur.profil.save()
        vendeur.desactive_par_signalements = True
        vendeur.save()
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/utilisateurs/reactiver-vendeur/',
            data=json.dumps({'id': vendeur.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        vendeur.refresh_from_db()
        self.assertFalse(vendeur.desactive_par_signalements)

    def test_reactiverVendeurAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_react2@example.com")
        vendeur = self._creer_acheteur("vendeur_react2@example.com")
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/utilisateurs/reactiver-vendeur/',
            data=json.dumps({'id': vendeur.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_reactiverVendeurAdmin_introuvable(self):
        admin = self._creer_admin("admin_react3@example.com", gestion_utilisateurs=True)
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/utilisateurs/reactiver-vendeur/',
            data=json.dumps({'id': 999999}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)


# ── DASHBOARD ADMIN (statistiques agrégées) ───────────────────────────────────
class TestDashboardAdmin(TestCase):

    def _creer_utilisateur(self, email, role='acheteur'):
        utilisateur = Utilisateur.objects.create(
            nom="D", prenom="B", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000050",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def test_dashboardAdmin_refuse_pour_un_acheteur(self):
        acheteur = self._creer_utilisateur("dash_acheteur1@example.com")
        token = self._token(acheteur)
        response = self.client.get('/Registration/admin/dashboard/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_dashboardAdmin_refuse_sans_token(self):
        response = self.client.get('/Registration/admin/dashboard/')
        self.assertEqual(response.status_code, 401)

    def test_dashboardAdmin_succes(self):
        admin = self._creer_utilisateur("dash_admin1@example.com", role='admin')
        self._creer_utilisateur("dash_vendeur1@example.com", role='vendeur')
        token = self._token(admin)

        response = self.client.get('/Registration/admin/dashboard/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertGreaterEqual(data['utilisateurs']['total'], 2)
        self.assertGreaterEqual(data['utilisateurs']['vendeurs'], 1)
        self.assertIn('par_sous_categorie', data['produits'])
        self.assertIn('plus_consultes', data['produits'])


# ── CONTACTER NOUS (public) ────────────────────────────────────────────────────
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestContacterNous(TestCase):

    def test_contacterNous_succes(self):
        response = self.client.post(
            '/Registration/contact/',
            data=json.dumps({'nom': 'Jean', 'email': 'jean@example.com', 'sujet': 'Question', 'message': 'Bonjour'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

    def test_contacterNous_refuse_champ_manquant(self):
        response = self.client.post(
            '/Registration/contact/',
            data=json.dumps({'nom': 'Jean', 'email': 'jean@example.com'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_contacterNous_cree_une_demande_administrative_si_compte_bloque(self):
        bloque = Utilisateur.objects.create(
            nom="Bloq", prenom="Ue", email="bloque_contact@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000060",
        )
        bloque.est_bloquer = True
        bloque.save()

        response = self.client.post(
            '/Registration/contact/',
            data=json.dumps({
                'nom': 'Bloq Ue', 'email': 'bloque_contact@example.com',
                'sujet': 'Contestation', 'message': 'Pourquoi suis-je bloqué ?',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DemandeAdministrative.objects.filter(email_contact='bloque_contact@example.com').exists())

    def test_contacterNous_ne_cree_pas_de_demande_pour_un_compte_normal(self):
        response = self.client.post(
            '/Registration/contact/',
            data=json.dumps({'nom': 'Normal', 'email': 'normal_contact@example.com', 'message': 'Salut'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DemandeAdministrative.objects.filter(email_contact='normal_contact@example.com').exists())


# ── GESTION DES COMPTES ADM (listerAdmins/creerAdmin/promouvoirAdmin/…) ──────
# EMAIL_BACKEND=locmem : creerAdmin/reinitialiserMotDePasseAdmin envoient un
# email via send_mail(fail_silently=False) directement (pas via
# envoyer_email_decision) — non bloquant pour la requête (try/except autour
# de l'appel dans la vue), mais on évite quand même un vrai essai SMTP réseau
# à chaque test. RECAPTCHA_SECRET_KEY=None : creerAdmin exige un jeton
# reCAPTCHA valide si cette clé est configurée en .env.dev (_verifier_recaptcha,
# Registration/views.py) — en son absence, la vérification est ignorée
# (comportement "dev sans clé" documenté dans la fonction elle-même).
@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    RECAPTCHA_SECRET_KEY=None,
)
class TestGestionComptesAdmin(TestCase):

    def _creer_admin(self, email, **droits):
        """Même garde-fou que TestDroitsAdminPermissions (bootstrap du tout
        premier compte, voir sa note) : role et droits toujours forcés
        explicitement via update_or_create."""
        utilisateur = Utilisateur.objects.create(
            nom="Admin", prenom="Test", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000070",
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

    def _creer_utilisateur(self, email, role='acheteur'):
        utilisateur = Utilisateur.objects.create(
            nom="U", prenom="Ser", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000071",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    # ── listerAdmins ──
    def test_listerAdmins_refuse_sans_droit(self):
        admin = self._creer_admin("liste_adm1@example.com")
        token = self._token(admin)
        response = self.client.get('/Registration/admin/adms/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_listerAdmins_succes_avec_gestion_mots_de_passe_seul(self):
        admin = self._creer_admin("liste_adm2@example.com", gestion_mots_de_passe=True)
        token = self._token(admin)
        response = self.client.get('/Registration/admin/adms/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)

    def test_listerAdmins_exclut_le_proprietaire_pour_les_autres(self):
        proprietaire = self._creer_admin("owner1@example.com", est_super_super_admin=True, super_admin=True)
        autre = self._creer_admin("liste_adm3@example.com", super_admin=True)
        token = self._token(autre)

        response = self.client.get('/Registration/admin/adms/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        ids = [a['id'] for a in data['admins']]
        self.assertNotIn(proprietaire.id, ids)
        self.assertIn(autre.id, ids)

    def test_listerAdmins_inclut_le_proprietaire_pour_lui_meme(self):
        proprietaire = self._creer_admin("owner2@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.get('/Registration/admin/adms/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        ids = [a['id'] for a in data['admins']]
        self.assertIn(proprietaire.id, ids)

    # ── creerAdmin ──
    def test_creerAdmin_succes(self):
        proprietaire = self._creer_admin("owner3@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.post(
            '/Registration/admin/adms/creer/',
            data=json.dumps({
                'nom': 'Nouveau', 'prenom': 'Adm', 'email': 'nouvel_adm1@example.com',
                'mot_de_passe': 'secret123', 'telephone': '12345678',
                'gestion_categories': True,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertTrue(data['admin']['droits']['gestion_categories'])
        nouvel_admin = Utilisateur.objects.get(email='nouvel_adm1@example.com')
        self.assertEqual(nouvel_admin.profil.role, 'admin')

    def test_creerAdmin_refuse_sans_droit_super_admin(self):
        admin = self._creer_admin("create_adm1@example.com", gestion_utilisateurs=True)
        token = self._token(admin)

        response = self.client.post(
            '/Registration/admin/adms/creer/',
            data=json.dumps({
                'nom': 'X', 'prenom': 'Y', 'email': 'refus1@example.com',
                'mot_de_passe': 'secret123', 'telephone': '12345678',
                'gestion_categories': True,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_creerAdmin_refuse_sans_aucun_droit(self):
        proprietaire = self._creer_admin("owner4@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.post(
            '/Registration/admin/adms/creer/',
            data=json.dumps({
                'nom': 'X', 'prenom': 'Y', 'email': 'refus2@example.com',
                'mot_de_passe': 'secret123', 'telephone': '12345678',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'DROIT_MANQUANT')

    def test_creerAdmin_refuse_nom_avec_chiffre(self):
        proprietaire = self._creer_admin("owner5@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.post(
            '/Registration/admin/adms/creer/',
            data=json.dumps({
                'nom': 'X3', 'prenom': 'Y', 'email': 'refus3@example.com',
                'mot_de_passe': 'secret123', 'telephone': '12345678',
                'gestion_categories': True,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'INVALID_NAME')

    def test_creerAdmin_refuse_telephone_invalide(self):
        proprietaire = self._creer_admin("owner6@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.post(
            '/Registration/admin/adms/creer/',
            data=json.dumps({
                'nom': 'X', 'prenom': 'Y', 'email': 'refus4@example.com',
                'mot_de_passe': 'secret123', 'telephone': '123',
                'gestion_categories': True,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'INVALID_PHONE')

    def test_creerAdmin_refuse_email_deja_pris(self):
        proprietaire = self._creer_admin("owner7@example.com", est_super_super_admin=True, super_admin=True)
        self._creer_utilisateur("deja_pris@example.com")
        token = self._token(proprietaire)

        response = self.client.post(
            '/Registration/admin/adms/creer/',
            data=json.dumps({
                'nom': 'X', 'prenom': 'Y', 'email': 'deja_pris@example.com',
                'mot_de_passe': 'secret123', 'telephone': '12345678',
                'gestion_categories': True,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_creerAdmin_super_admin_ignore_si_pas_proprietaire(self):
        # un admin "Tous les droits" (mais pas propriétaire) qui essaie de
        # créer un pair "Tous les droits" : super_admin est ignoré côté
        # serveur (_appliquer_droits, autoriser_super_admin=False) — la
        # requête ne réussit que parce qu'un autre droit précis est aussi fourni
        tous_droits = self._creer_admin("owner8@example.com", super_admin=True)
        token = self._token(tous_droits)

        response = self.client.post(
            '/Registration/admin/adms/creer/',
            data=json.dumps({
                'nom': 'X', 'prenom': 'Y', 'email': 'refus5@example.com',
                'mot_de_passe': 'secret123', 'telephone': '12345678',
                'super_admin': True, 'gestion_categories': True,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        nouvel_admin = Utilisateur.objects.get(email='refus5@example.com')
        self.assertFalse(nouvel_admin.droits_admin.super_admin)

    # ── promouvoirAdmin ──
    def test_promouvoirAdmin_succes(self):
        proprietaire = self._creer_admin("owner9@example.com", est_super_super_admin=True, super_admin=True)
        acheteur = self._creer_utilisateur("promu1@example.com")
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/promouvoir/',
            data=json.dumps({'id': acheteur.id, 'gestion_signalements': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        acheteur.refresh_from_db()
        self.assertEqual(acheteur.profil.role, 'admin')
        self.assertTrue(acheteur.droits_admin.gestion_signalements)

    def test_promouvoirAdmin_refuse_deja_admin(self):
        proprietaire = self._creer_admin("owner10@example.com", est_super_super_admin=True, super_admin=True)
        autre_admin = self._creer_admin("dejaadmin1@example.com", gestion_categories=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/promouvoir/',
            data=json.dumps({'id': autre_admin.id, 'gestion_signalements': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_promouvoirAdmin_refuse_compte_gmail(self):
        proprietaire = self._creer_admin("owner11@example.com", est_super_super_admin=True, super_admin=True)
        acheteur_gmail = self._creer_utilisateur("quelquun@gmail.com")
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/promouvoir/',
            data=json.dumps({'id': acheteur_gmail.id, 'gestion_signalements': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'GMAIL_CANNOT_BECOME_ADMIN')

    def test_promouvoirAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("promreq1@example.com", gestion_utilisateurs=True)
        acheteur = self._creer_utilisateur("promu2@example.com")
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/adms/promouvoir/',
            data=json.dumps({'id': acheteur.id, 'gestion_signalements': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    # ── modifierDroitsAdmin ──
    def test_modifierDroitsAdmin_succes(self):
        proprietaire = self._creer_admin("owner12@example.com", est_super_super_admin=True, super_admin=True)
        cible = self._creer_admin("modif_droits1@example.com", gestion_categories=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/modifier-droits/',
            data=json.dumps({'id': cible.id, 'gestion_categories': False, 'gestion_support': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        cible.droits_admin.refresh_from_db()
        self.assertFalse(cible.droits_admin.gestion_categories)
        self.assertTrue(cible.droits_admin.gestion_support)

    def test_modifierDroitsAdmin_refuse_sur_soi_meme(self):
        proprietaire = self._creer_admin("owner13@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/modifier-droits/',
            data=json.dumps({'id': proprietaire.id, 'gestion_categories': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'CANNOT_MODIFY_OWN_RIGHTS')

    def test_modifierDroitsAdmin_refuse_cible_non_admin(self):
        proprietaire = self._creer_admin("owner14@example.com", est_super_super_admin=True, super_admin=True)
        acheteur = self._creer_utilisateur("pasadmin1@example.com")
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/modifier-droits/',
            data=json.dumps({'id': acheteur.id, 'gestion_categories': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'NOT_ADMIN')

    def test_modifierDroitsAdmin_refuse_entre_deux_tous_droits(self):
        a = self._creer_admin("tousdroitsA_m@example.com", super_admin=True)
        b = self._creer_admin("tousdroitsB_m@example.com", super_admin=True)
        token = self._token(a)

        response = self.client.put(
            '/Registration/admin/adms/modifier-droits/',
            data=json.dumps({'id': b.id, 'gestion_categories': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'CANNOT_ACT_ON_ADMIN')

    def test_modifierDroitsAdmin_refuse_retrait_de_tous_les_droits(self):
        proprietaire = self._creer_admin("owner15@example.com", est_super_super_admin=True, super_admin=True)
        cible = self._creer_admin("modif_droits2@example.com", gestion_categories=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/modifier-droits/',
            data=json.dumps({'id': cible.id, 'gestion_categories': False}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'DROIT_MANQUANT')

    # ── revoquerAdmin ──
    def test_revoquerAdmin_succes(self):
        proprietaire = self._creer_admin("owner16@example.com", est_super_super_admin=True, super_admin=True)
        cible = self._creer_admin("revoque1@example.com", gestion_categories=True)
        Token.objects.create(utilisateur=cible, cle=secrets.token_hex(32))
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/revoquer/',
            data=json.dumps({'id': cible.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        cible.refresh_from_db()
        self.assertEqual(cible.profil.role, 'acheteur')
        self.assertFalse(DroitsAdmin.objects.filter(utilisateur=cible).exists())
        self.assertEqual(Token.objects.filter(utilisateur=cible).count(), 0)

    def test_revoquerAdmin_refuse_sur_soi_meme(self):
        proprietaire = self._creer_admin("owner17@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/revoquer/',
            data=json.dumps({'id': proprietaire.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_revoquerAdmin_refuse_cible_non_admin(self):
        proprietaire = self._creer_admin("owner18@example.com", est_super_super_admin=True, super_admin=True)
        acheteur = self._creer_utilisateur("pasadmin2@example.com")
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/revoquer/',
            data=json.dumps({'id': acheteur.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    # ── modifierInfosAdmin ──
    def test_modifierInfosAdmin_succes(self):
        proprietaire = self._creer_admin("owner19@example.com", est_super_super_admin=True, super_admin=True)
        cible = self._creer_admin("infos1@example.com", gestion_categories=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/modifier-infos/',
            data=json.dumps({'id': cible.id, 'nom': 'NouveauNom', 'telephone': '87654321'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        cible.refresh_from_db()
        self.assertEqual(cible.nom, 'NouveauNom')
        self.assertEqual(cible.telephone, '87654321')

    def test_modifierInfosAdmin_refuse_sur_soi_meme(self):
        proprietaire = self._creer_admin("owner20@example.com", est_super_super_admin=True, super_admin=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/modifier-infos/',
            data=json.dumps({'id': proprietaire.id, 'nom': 'X'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'USE_OWN_PROFILE')

    def test_modifierInfosAdmin_refuse_nom_invalide(self):
        proprietaire = self._creer_admin("owner21@example.com", est_super_super_admin=True, super_admin=True)
        cible = self._creer_admin("infos2@example.com", gestion_categories=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/modifier-infos/',
            data=json.dumps({'id': cible.id, 'nom': 'X1'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'INVALID_NAME')

    # ── reinitialiserMotDePasseAdmin ──
    def test_reinitialiserMotDePasseAdmin_succes(self):
        proprietaire = self._creer_admin("owner22@example.com", est_super_super_admin=True, super_admin=True)
        cible = self._creer_admin("resetmdp1@example.com", gestion_categories=True)
        token = self._token(proprietaire)

        response = self.client.put(
            '/Registration/admin/adms/reinitialiser-mdp/',
            data=json.dumps({'id': cible.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        cible.refresh_from_db()
        self.assertTrue(cible.doit_changer_mot_de_passe)

    def test_reinitialiserMotDePasseAdmin_refuse_sur_soi_meme(self):
        admin = self._creer_admin("resetmdp_self@example.com", gestion_mots_de_passe=True)
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/adms/reinitialiser-mdp/',
            data=json.dumps({'id': admin.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'CANNOT_RESET_OWN_PASSWORD')

    def test_reinitialiserMotDePasseAdmin_refuse_entre_pairs_avec_meme_droit(self):
        a = self._creer_admin("resetmdpA_m@example.com", gestion_mots_de_passe=True)
        b = self._creer_admin("resetmdpB_m@example.com", gestion_mots_de_passe=True)
        token = self._token(a)

        response = self.client.put(
            '/Registration/admin/adms/reinitialiser-mdp/',
            data=json.dumps({'id': b.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_reinitialiserMotDePasseAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("resetmdp_sansdroit@example.com", gestion_categories=True)
        cible = self._creer_admin("resetmdp_cible@example.com", gestion_categories=True)
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/adms/reinitialiser-mdp/',
            data=json.dumps({'id': cible.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)


# ── DEMANDES ADMINISTRATIVES (créer/lister/approuver/rejeter) ────────────────
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestDemandesAdministratives(TestCase):

    def _creer_admin(self, email, **droits):
        utilisateur = Utilisateur.objects.create(
            nom="Admin", prenom="Test", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000080",
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

    def _creer_utilisateur(self, email):
        utilisateur = Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000081",
        )
        utilisateur.profil.role = 'acheteur'
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    # ── créer / mes demandes ──
    def test_creerDemandeAdministrative_succes(self):
        utilisateur = self._creer_utilisateur("demandeur1@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/demandes-administratives/',
            data=json.dumps({'objet': 'Contestation', 'description': 'Je conteste ceci.'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['demande']['statut'], 'en_attente')
        self.assertEqual(data['demande']['utilisateur_email'], utilisateur.email)

    def test_creerDemandeAdministrative_refuse_sans_objet(self):
        utilisateur = self._creer_utilisateur("demandeur2@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/demandes-administratives/',
            data=json.dumps({'description': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_creerDemandeAdministrative_refuse_sans_token(self):
        response = self.client.post(
            '/Registration/demandes-administratives/',
            data=json.dumps({'objet': 'x', 'description': 'y'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_mesDemandesAdministratives_scope_a_lutilisateur(self):
        u1 = self._creer_utilisateur("demandeur3@example.com")
        u2 = self._creer_utilisateur("demandeur4@example.com")
        DemandeAdministrative.objects.create(
            utilisateur=u1, objet="A", description="d", nom_contact="A A", email_contact=u1.email,
        )
        DemandeAdministrative.objects.create(
            utilisateur=u2, objet="B", description="d", nom_contact="B B", email_contact=u2.email,
        )
        token = self._token(u1)

        response = self.client.get('/Registration/demandes-administratives/mes-demandes/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['demandes']), 1)
        self.assertEqual(data['demandes'][0]['objet'], 'A')

    # ── lister (admin) ──
    def test_listerDemandesAdministrativesAdmin_ne_montre_que_en_attente(self):
        admin = self._creer_admin("admin_dem1@example.com", gestion_utilisateurs=True)
        utilisateur = self._creer_utilisateur("demandeur5@example.com")
        DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="En attente", description="d",
            nom_contact="X", email_contact=utilisateur.email,
        )
        DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="Déjà traitée", description="d",
            nom_contact="X", email_contact=utilisateur.email, statut='approuvee',
        )
        token = self._token(admin)

        response = self.client.get('/Registration/admin/demandes-administratives/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['demandes']), 1)
        self.assertEqual(data['demandes'][0]['objet'], 'En attente')

    def test_listerDemandesAdministrativesAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_dem2@example.com")
        token = self._token(admin)
        response = self.client.get('/Registration/admin/demandes-administratives/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    # ── approuver ──
    def test_approuverDemandeAdministrative_succes_et_debloque_le_compte(self):
        admin = self._creer_admin("admin_dem3@example.com", gestion_utilisateurs=True)
        utilisateur = self._creer_utilisateur("demandeur6@example.com")
        utilisateur.bloquer("Suspicion de fraude")
        demande = DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="Contestation de blocage", description="d",
            nom_contact="X", email_contact=utilisateur.email,
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/demandes-administratives/approuver/',
            data=json.dumps({'id': demande.id, 'reponse': 'Vérifié, tout est en ordre.'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['demande']['statut'], 'approuvee')
        utilisateur.refresh_from_db()
        self.assertFalse(utilisateur.est_bloquer)

    def test_approuverDemandeAdministrative_refuse_deja_traitee(self):
        admin = self._creer_admin("admin_dem4@example.com", gestion_utilisateurs=True)
        utilisateur = self._creer_utilisateur("demandeur7@example.com")
        demande = DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="X", description="d",
            nom_contact="X", email_contact=utilisateur.email, statut='rejetee',
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/demandes-administratives/approuver/',
            data=json.dumps({'id': demande.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 409)

    def test_approuverDemandeAdministrative_introuvable(self):
        admin = self._creer_admin("admin_dem5@example.com", gestion_utilisateurs=True)
        token = self._token(admin)
        response = self.client.put(
            '/Registration/admin/demandes-administratives/approuver/',
            data=json.dumps({'id': 999999}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_approuverDemandeAdministrative_refuse_sans_droit(self):
        admin = self._creer_admin("admin_dem6@example.com")
        utilisateur = self._creer_utilisateur("demandeur8@example.com")
        demande = DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="X", description="d",
            nom_contact="X", email_contact=utilisateur.email,
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/demandes-administratives/approuver/',
            data=json.dumps({'id': demande.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_approuverDemandeAdministrative_compte_supprime_ne_plante_pas(self):
        # demande créée pour un compte depuis supprimé (utilisateur_id nul,
        # voir docstring DemandeAdministrative, Registration/models.py) —
        # l'approbation doit rester un simple accusé de réception, sans
        # tenter de débloquer un compte qui n'existe plus
        admin = self._creer_admin("admin_dem7@example.com", gestion_utilisateurs=True)
        demande = DemandeAdministrative.objects.create(
            utilisateur=None, objet="Contestation de suppression de compte", description="d",
            nom_contact="Ancien Compte", email_contact="ancien@example.com",
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/demandes-administratives/approuver/',
            data=json.dumps({'id': demande.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, 'approuvee')

    # ── rejeter ──
    def test_rejeterDemandeAdministrative_succes(self):
        admin = self._creer_admin("admin_dem8@example.com", gestion_utilisateurs=True)
        utilisateur = self._creer_utilisateur("demandeur9@example.com")
        demande = DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="X", description="d",
            nom_contact="X", email_contact=utilisateur.email,
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/demandes-administratives/rejeter/',
            data=json.dumps({'id': demande.id, 'motif': 'Preuves insuffisantes'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['demande']['statut'], 'rejetee')
        self.assertEqual(data['demande']['reponse_admin'], 'Preuves insuffisantes')

    def test_rejeterDemandeAdministrative_refuse_sans_motif(self):
        admin = self._creer_admin("admin_dem9@example.com", gestion_utilisateurs=True)
        utilisateur = self._creer_utilisateur("demandeur10@example.com")
        demande = DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="X", description="d",
            nom_contact="X", email_contact=utilisateur.email,
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/demandes-administratives/rejeter/',
            data=json.dumps({'id': demande.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_rejeterDemandeAdministrative_refuse_deja_traitee(self):
        admin = self._creer_admin("admin_dem10@example.com", gestion_utilisateurs=True)
        utilisateur = self._creer_utilisateur("demandeur11@example.com")
        demande = DemandeAdministrative.objects.create(
            utilisateur=utilisateur, objet="X", description="d",
            nom_contact="X", email_contact=utilisateur.email, statut='approuvee',
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/demandes-administratives/rejeter/',
            data=json.dumps({'id': demande.id, 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 409)


# ── PIPELINE KYC (soumettre_verification/statut_verification/previsualiser_contrat) ──
# _lancer_pipeline_ocr et _lancer_verification_faciale sont mockées dans TOUTE
# cette classe : elles appellent respectivement PaddleOCR et un subprocess
# DeepFace dans un venv séparé (voir Registration/services/{ocr,face}_service.py)
# — ni disponible ni souhaitable dans la suite de tests (lent, non
# déterministe). soumettre_verification les traite comme une boîte noire
# (elle ne fait qu'appeler ces deux fonctions et gérer leurs exceptions) :
# les mocker ici teste exactement la responsabilité de la vue elle-même —
# validation des champs, unicité de la pièce, mise à jour de la localisation,
# gestion d'erreur — sans dupliquer les tests dédiés à l'OCR/au facial.
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
@patch('Registration.views._lancer_verification_faciale', lambda demande: None)
@patch('Registration.views._lancer_pipeline_ocr', lambda demande: None)
class TestSoumettreVerification(TestCase):

    def _creer_vendeur_potentiel(self, email, role='acheteur'):
        utilisateur = Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000090",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _creer_entreprise(self, email):
        return Entreprise.objects.create(
            nom="E", prenom="", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000091",
            nom_Entreprise=f"Entreprise {email}",
        )

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def test_soumettre_verification_individuel_succes(self):
        utilisateur = self._creer_vendeur_potentiel("kyc1@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={
                'numero_piece_saisi': 'AB123456',
                'type_document': 'cin',
                'document_recto': _image_upload("recto.png"),
                'selfie': _image_upload("selfie.png"),
                'departement': 'Ouest', 'commune': 'Port-au-Prince',
            },
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        demande = DemandeVerification.objects.get(utilisateur=utilisateur)
        self.assertEqual(demande.type_demandeur, 'individuel')
        self.assertEqual(demande.numero_piece_saisi, 'AB123456')
        utilisateur.profil.refresh_from_db()
        self.assertEqual(utilisateur.profil.commune, 'Port-au-Prince')

    def test_soumettre_verification_refuse_pour_un_admin(self):
        admin = self._creer_vendeur_potentiel("kyc2@example.com", role='admin')
        token = self._token(admin)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'numero_piece_saisi': 'X', 'type_document': 'cin',
                  'document_recto': _image_upload(), 'selfie': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'ADMIN_CANNOT_BECOME_SELLER')

    def test_soumettre_verification_refuse_sans_numero_piece(self):
        utilisateur = self._creer_vendeur_potentiel("kyc3@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'type_document': 'cin', 'document_recto': _image_upload(), 'selfie': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_soumettre_verification_refuse_type_document_invalide(self):
        utilisateur = self._creer_vendeur_potentiel("kyc4@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'numero_piece_saisi': 'X', 'type_document': 'exotique',
                  'document_recto': _image_upload(), 'selfie': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_soumettre_verification_refuse_sans_selfie(self):
        utilisateur = self._creer_vendeur_potentiel("kyc5@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'numero_piece_saisi': 'X', 'type_document': 'cin', 'document_recto': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_soumettre_verification_refuse_fichier_non_image(self):
        utilisateur = self._creer_vendeur_potentiel("kyc6@example.com")
        token = self._token(utilisateur)
        faux_fichier = SimpleUploadedFile("x.png", b"pas une image", content_type='image/png')

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'numero_piece_saisi': 'X', 'type_document': 'cin',
                  'document_recto': faux_fichier, 'selfie': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_soumettre_verification_refuse_numero_piece_deja_utilise(self):
        premier = self._creer_vendeur_potentiel("kyc7@example.com")
        DemandeVerification.objects.create(
            utilisateur=premier, type_demandeur='individuel', numero_piece_saisi='DEJA-PRIS',
        )
        second = self._creer_vendeur_potentiel("kyc8@example.com")
        token = self._token(second)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'numero_piece_saisi': 'deja-pris', 'type_document': 'cin',  # comparaison normalisée (casse/espaces)
                  'document_recto': _image_upload(), 'selfie': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 409)

    def test_soumettre_verification_numero_piece_reutilisable_si_echoue(self):
        # une demande 'echoue' ne réserve pas indéfiniment le numéro (voir
        # docstring de soumettre_verification, Registration/views.py)
        premier = self._creer_vendeur_potentiel("kyc9@example.com")
        DemandeVerification.objects.create(
            utilisateur=premier, type_demandeur='individuel',
            numero_piece_saisi='REUTILISABLE', statut='echoue',
        )
        second = self._creer_vendeur_potentiel("kyc10@example.com")
        token = self._token(second)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'numero_piece_saisi': 'REUTILISABLE', 'type_document': 'cin',
                  'document_recto': _image_upload(), 'selfie': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)

    def test_soumettre_verification_erreur_pipeline_marque_echoue(self):
        utilisateur = self._creer_vendeur_potentiel("kyc11@example.com")
        token = self._token(utilisateur)

        with patch('Registration.views._lancer_pipeline_ocr', side_effect=RuntimeError("OCR indisponible")):
            response = self.client.post(
                '/Registration/verification/soumettre/',
                data={'numero_piece_saisi': 'X', 'type_document': 'cin',
                      'document_recto': _image_upload(), 'selfie': _image_upload()},
                HTTP_AUTHORIZATION=f'Token {token}',
            )
        # la soumission elle-même réussit toujours (erreur pipeline non
        # bloquante pour la requête, voir docstring de la vue) — seul le
        # statut de la demande reflète l'échec
        self.assertEqual(response.status_code, 201)
        demande = DemandeVerification.objects.get(utilisateur=utilisateur)
        self.assertEqual(demande.statut, 'echoue')
        self.assertIn('OCR indisponible', demande.motif_echec)

    def test_soumettre_verification_entreprise_succes(self):
        entreprise = self._creer_entreprise("kyc_ent1@example.com")
        token = self._token(entreprise)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={
                'numero_piece_saisi': 'PATENTE-001',
                'certificat_patente': _image_upload("patente.png"),
            },
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        demande = DemandeVerification.objects.get(utilisateur=entreprise)
        self.assertEqual(demande.type_demandeur, 'entreprise')

    def test_soumettre_verification_entreprise_refuse_sans_certificat(self):
        entreprise = self._creer_entreprise("kyc_ent2@example.com")
        token = self._token(entreprise)

        response = self.client.post(
            '/Registration/verification/soumettre/',
            data={'numero_piece_saisi': 'PATENTE-002'},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_soumettre_verification_refuse_sans_token(self):
        response = self.client.post('/Registration/verification/soumettre/', data={'numero_piece_saisi': 'X'})
        self.assertEqual(response.status_code, 401)


class TestStatutEtPrevisualisationVerification(TestCase):

    def _creer_utilisateur(self, email):
        utilisateur = Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000092",
        )
        utilisateur.profil.role = 'acheteur'
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def test_statut_verification_404_sans_demande(self):
        utilisateur = self._creer_utilisateur("statut1@example.com")
        token = self._token(utilisateur)
        response = self.client.get('/Registration/verification/statut/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 404)

    def test_statut_verification_retourne_le_statut_courant(self):
        utilisateur = self._creer_utilisateur("statut2@example.com")
        DemandeVerification.objects.create(
            utilisateur=utilisateur, type_demandeur='individuel',
            statut='en_attente_manuelle', motif_revue_manuelle="Score de correspondance faible",
        )
        token = self._token(utilisateur)

        response = self.client.get('/Registration/verification/statut/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['statut'], 'en_attente_manuelle')

    def test_statut_verification_refuse_sans_token(self):
        response = self.client.get('/Registration/verification/statut/')
        self.assertEqual(response.status_code, 401)

    def test_previsualiser_contrat_individuel_retourne_un_pdf(self):
        utilisateur = self._creer_utilisateur("apercu1@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/verification/previsualiser/',
            data={
                'numero_piece_saisi': 'AB123', 'type_document': 'cin',
                'document_recto': _image_upload(), 'selfie': _image_upload(),
            },
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        # aucune DemandeVerification créée — pure prévisualisation (voir docstring de la vue)
        self.assertFalse(DemandeVerification.objects.filter(utilisateur=utilisateur).exists())

    def test_previsualiser_contrat_entreprise_retourne_un_pdf(self):
        entreprise = Entreprise.objects.create(
            nom="E", prenom="", email="apercu_ent1@example.com",
            mot_de_passe=haser_password("secret123"), telephone="00000093",
            nom_Entreprise="Entreprise Apercu",
        )
        token = self._token(entreprise)

        response = self.client.post(
            '/Registration/verification/previsualiser/',
            data={'numero_piece_saisi': 'PATENTE-APERCU', 'certificat_patente': _image_upload()},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_previsualiser_contrat_refuse_sans_token(self):
        response = self.client.post('/Registration/verification/previsualiser/', data={})
        self.assertEqual(response.status_code, 401)


# ── REVUE MANUELLE (lister_demandes_admin/lister_demandes_revue_manuelle/traiter_demande_revue_manuelle) ──
# EMAIL_BACKEND=locmem : marquer_verifie()/marquer_echoue() (Registration/
# models.py) envoient chacune un email (contrat PDF joint pour la première) —
# non bloquant en cas d'échec (try/except dans le modèle), mais on évite un
# vrai essai SMTP réseau. marquer_verifie génère un vrai PDF via reportlab
# (Registration/services/contrat_service.py) — fonctionne même sans fichiers
# réels attachés à la demande (_lire_fichier tolère None).
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestRevueManuelle(TestCase):

    def _creer_admin(self, email, **droits):
        utilisateur = Utilisateur.objects.create(
            nom="Admin", prenom="Test", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000095",
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

    def _creer_utilisateur(self, email, role='acheteur'):
        utilisateur = Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000096",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _creer_entreprise(self, email):
        return Entreprise.objects.create(
            nom="E", prenom="", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000097",
            nom_Entreprise=f"Entreprise {email}",
        )

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    # ── lister_demandes_admin (entreprise, en_attente/en_attente_manuelle uniquement) ──
    def test_lister_demandes_admin_ne_montre_que_entreprise(self):
        super_admin = self._creer_admin("super_rev1@example.com", super_admin=True)
        entreprise = self._creer_entreprise("rev_ent1@example.com")
        DemandeVerification.objects.create(
            utilisateur=entreprise, type_demandeur='entreprise', statut='en_attente',
            numero_patente_extrait='PAT-123',
        )
        individuel = self._creer_utilisateur("rev_ind1@example.com")
        DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente',
        )
        token = self._token(super_admin)

        response = self.client.get('/Registration/admin/verifications-entreprise/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['demandes']), 1)
        self.assertEqual(data['demandes'][0]['numero_patente_extrait'], 'PAT-123')

    def test_lister_demandes_admin_exclut_les_demandes_deja_traitees(self):
        super_admin = self._creer_admin("super_rev2@example.com", super_admin=True)
        entreprise = self._creer_entreprise("rev_ent2@example.com")
        DemandeVerification.objects.create(
            utilisateur=entreprise, type_demandeur='entreprise', statut='verifie',
        )
        token = self._token(super_admin)

        response = self.client.get('/Registration/admin/verifications-entreprise/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        self.assertEqual(data['demandes'], [])

    def test_lister_demandes_admin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_rev1@example.com", gestion_utilisateurs=True)
        token = self._token(admin)
        response = self.client.get('/Registration/admin/verifications-entreprise/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    # ── lister_demandes_revue_manuelle (individuel + entreprise) ──
    def test_lister_demandes_revue_manuelle_inclut_individuel_et_entreprise(self):
        super_admin = self._creer_admin("super_rev3@example.com", super_admin=True)
        individuel = self._creer_utilisateur("rev_ind2@example.com")
        entreprise = self._creer_entreprise("rev_ent3@example.com")
        DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente_manuelle',
            motif_revue_manuelle="Score facial faible",
        )
        DemandeVerification.objects.create(
            utilisateur=entreprise, type_demandeur='entreprise', statut='en_attente_manuelle',
        )
        token = self._token(super_admin)

        response = self.client.get('/Registration/admin/verifications-revue-manuelle/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['demandes']), 2)

    def test_lister_demandes_revue_manuelle_exclut_en_attente_simple(self):
        super_admin = self._creer_admin("super_rev4@example.com", super_admin=True)
        individuel = self._creer_utilisateur("rev_ind3@example.com")
        DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente',
        )
        token = self._token(super_admin)

        response = self.client.get('/Registration/admin/verifications-revue-manuelle/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        self.assertEqual(data['demandes'], [])

    def test_lister_demandes_revue_manuelle_refuse_sans_droit(self):
        admin = self._creer_admin("admin_rev2@example.com", gestion_utilisateurs=True)
        token = self._token(admin)
        response = self.client.get('/Registration/admin/verifications-revue-manuelle/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    # ── traiter_demande_revue_manuelle ──
    def test_traiter_demande_revue_manuelle_approuver_promeut_vendeur(self):
        super_admin = self._creer_admin("super_rev5@example.com", super_admin=True)
        individuel = self._creer_utilisateur("rev_ind4@example.com")
        demande = DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente_manuelle',
            numero_piece_saisi='ABC123',
        )
        token = self._token(super_admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': demande.id, 'decision': 'approuver'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['demande']['statut'], 'verifie')
        individuel.refresh_from_db()
        self.assertEqual(individuel.profil.role, 'vendeur')
        demande.refresh_from_db()
        self.assertTrue(demande.contrat_pdf)

    def test_traiter_demande_revue_manuelle_rejeter_avec_motif(self):
        super_admin = self._creer_admin("super_rev6@example.com", super_admin=True)
        individuel = self._creer_utilisateur("rev_ind5@example.com")
        demande = DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente_manuelle',
        )
        token = self._token(super_admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': demande.id, 'decision': 'rejeter', 'motif': 'Photo illisible'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, 'echoue')
        self.assertEqual(demande.motif_echec, 'Photo illisible')
        individuel.refresh_from_db()
        self.assertEqual(individuel.profil.role, 'acheteur')

    def test_traiter_demande_revue_manuelle_rejeter_motif_par_defaut(self):
        super_admin = self._creer_admin("super_rev7@example.com", super_admin=True)
        individuel = self._creer_utilisateur("rev_ind6@example.com")
        demande = DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente_manuelle',
        )
        token = self._token(super_admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': demande.id, 'decision': 'rejeter'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        demande.refresh_from_db()
        self.assertTrue(demande.motif_echec)

    def test_traiter_demande_revue_manuelle_refuse_deja_traitee(self):
        super_admin = self._creer_admin("super_rev8@example.com", super_admin=True)
        individuel = self._creer_utilisateur("rev_ind7@example.com")
        demande = DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='verifie',
        )
        token = self._token(super_admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': demande.id, 'decision': 'approuver'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)['error_code'], 'ALREADY_PROCESSED')

    def test_traiter_demande_revue_manuelle_refuse_decision_invalide(self):
        super_admin = self._creer_admin("super_rev9@example.com", super_admin=True)
        individuel = self._creer_utilisateur("rev_ind8@example.com")
        demande = DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente_manuelle',
        )
        token = self._token(super_admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': demande.id, 'decision': 'peut-etre'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_traiter_demande_revue_manuelle_introuvable(self):
        super_admin = self._creer_admin("super_rev10@example.com", super_admin=True)
        token = self._token(super_admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': 999999, 'decision': 'approuver'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_traiter_demande_revue_manuelle_refuse_sans_droit(self):
        admin = self._creer_admin("admin_rev3@example.com", gestion_utilisateurs=True)
        individuel = self._creer_utilisateur("rev_ind9@example.com")
        demande = DemandeVerification.objects.create(
            utilisateur=individuel, type_demandeur='individuel', statut='en_attente_manuelle',
        )
        token = self._token(admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': demande.id, 'decision': 'approuver'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_traiter_demande_revue_manuelle_approuver_entreprise(self):
        super_admin = self._creer_admin("super_rev11@example.com", super_admin=True)
        entreprise = self._creer_entreprise("rev_ent4@example.com")
        demande = DemandeVerification.objects.create(
            utilisateur=entreprise, type_demandeur='entreprise', statut='en_attente_manuelle',
            numero_piece_saisi='PATENTE-999',
        )
        token = self._token(super_admin)

        response = self.client.put(
            '/Registration/admin/verifications-revue-manuelle/traiter/',
            data=json.dumps({'id': demande.id, 'decision': 'approuver'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        entreprise.profil.refresh_from_db()
        self.assertEqual(entreprise.profil.role, 'vendeur')


# ── RAPPORT D'AUDIT PDF (genererRapportAudit) ─────────────────────────────────
class TestRapportAudit(TestCase):

    def _creer_admin(self, email, **droits):
        utilisateur = Utilisateur.objects.create(
            nom="Admin", prenom="Test", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000098",
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

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    # date_fin ne peut jamais être future (voir genererRapportAudit,
    # Registration/views.py) — aujourd'hui sert de borne haute par défaut
    # dans les tests "succès" ci-dessous.
    AUJOURDHUI = timezone.localdate().isoformat()

    def test_genererRapportAudit_succes(self):
        super_admin = self._creer_admin("audit1@example.com", super_admin=True)
        enregistrer_audit(super_admin, 'utilisateur.bloquer', "A bloqué un compte de test")
        token = self._token(super_admin)

        response = self.client.get(
            f'/Registration/admin/adms/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_genererRapportAudit_succes_sans_aucune_entree(self):
        # période sans la moindre action journalisée — le PDF doit tout de
        # même se générer (tableau vide), jamais planter
        super_admin = self._creer_admin("audit2@example.com", super_admin=True)
        token = self._token(super_admin)

        response = self.client.get(
            '/Registration/admin/adms/rapport-audit/?date_debut=2000-01-01&date_fin=2000-01-02',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_genererRapportAudit_filtre_par_admin(self):
        super_admin = self._creer_admin("audit3@example.com", super_admin=True)
        autre_admin = self._creer_admin("audit4@example.com", gestion_categories=True)
        enregistrer_audit(autre_admin, 'admin.creer', "A créé un compte")
        token = self._token(super_admin)

        response = self.client.get(
            f'/Registration/admin/adms/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}&admin_id={autre_admin.id}',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_genererRapportAudit_refuse_admin_filtre_introuvable(self):
        super_admin = self._creer_admin("audit5@example.com", super_admin=True)
        token = self._token(super_admin)

        response = self.client.get(
            f'/Registration/admin/adms/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}&admin_id=999999',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_genererRapportAudit_refuse_sans_dates(self):
        super_admin = self._creer_admin("audit6@example.com", super_admin=True)
        token = self._token(super_admin)

        response = self.client.get('/Registration/admin/adms/rapport-audit/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 400)

    def test_genererRapportAudit_refuse_dates_incoherentes(self):
        super_admin = self._creer_admin("audit7@example.com", super_admin=True)
        token = self._token(super_admin)

        response = self.client.get(
            '/Registration/admin/adms/rapport-audit/?date_debut=2020-01-10&date_fin=2020-01-01',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_genererRapportAudit_refuse_date_future(self):
        super_admin = self._creer_admin("audit8@example.com", super_admin=True)
        token = self._token(super_admin)

        response = self.client.get(
            '/Registration/admin/adms/rapport-audit/?date_debut=2099-01-01&date_fin=2099-01-02',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_genererRapportAudit_refuse_format_de_date_invalide(self):
        super_admin = self._creer_admin("audit9@example.com", super_admin=True)
        token = self._token(super_admin)

        response = self.client.get(
            '/Registration/admin/adms/rapport-audit/?date_debut=01-01-2020&date_fin=2020-01-02',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_genererRapportAudit_refuse_sans_droit(self):
        admin = self._creer_admin("audit10@example.com", gestion_categories=True)
        token = self._token(admin)

        response = self.client.get(
            f'/Registration/admin/adms/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_genererRapportAudit_refuse_sans_token(self):
        response = self.client.get(f'/Registration/admin/adms/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}')
        self.assertEqual(response.status_code, 401)


# ── CLÉS DE CHIFFREMENT E2E (cleChiffrement/clePubliqueUtilisateur/clesPubliquesAdmins) ──
class TestClesChiffrementE2E(TestCase):

    def _creer_utilisateur(self, email, role='acheteur'):
        utilisateur = Utilisateur.objects.create(
            nom="Doe", prenom="Jane", email=email,
            mot_de_passe=haser_password("secret123"), telephone="00000099",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    # ── GET ──
    def test_cleChiffrement_get_404_si_non_configuree(self):
        utilisateur = self._creer_utilisateur("e2e1@example.com")
        token = self._token(utilisateur)
        response = self.client.get('/Registration/cle-chiffrement/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content)['error_code'], 'CLE_CHIFFREMENT_INTROUVABLE')

    def test_cleChiffrement_get_succes(self):
        utilisateur = self._creer_utilisateur("e2e2@example.com")
        CleChiffrementUtilisateur.objects.create(utilisateur=utilisateur, cle_publique="cle-pub-abc")
        token = self._token(utilisateur)

        response = self.client.get('/Registration/cle-chiffrement/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['cle_publique'], 'cle-pub-abc')

    def test_cleChiffrement_get_refuse_sans_token(self):
        response = self.client.get('/Registration/cle-chiffrement/')
        self.assertEqual(response.status_code, 401)

    # ── POST ──
    def test_cleChiffrement_post_succes(self):
        utilisateur = self._creer_utilisateur("e2e3@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/cle-chiffrement/',
            data=json.dumps({
                'cle_publique': 'cle-pub-1', 'cle_privee_chiffree': 'chiffree-1',
                'iv_cle_privee': 'iv-1', 'sel_kdf': 'sel-1', 'iterations_kdf': 100000,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        cle = CleChiffrementUtilisateur.objects.get(utilisateur=utilisateur)
        self.assertEqual(cle.cle_publique, 'cle-pub-1')
        self.assertEqual(cle.iterations_kdf, 100000)

    def test_cleChiffrement_post_refuse_sans_cle_publique(self):
        utilisateur = self._creer_utilisateur("e2e4@example.com")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/cle-chiffrement/', data=json.dumps({}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'CHAMPS_CLE_MANQUANTS')

    def test_cleChiffrement_post_refuse_si_deja_existante(self):
        utilisateur = self._creer_utilisateur("e2e5@example.com")
        CleChiffrementUtilisateur.objects.create(utilisateur=utilisateur, cle_publique="deja-la")
        token = self._token(utilisateur)

        response = self.client.post(
            '/Registration/cle-chiffrement/',
            data=json.dumps({'cle_publique': 'nouvelle'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)['error_code'], 'CLE_CHIFFREMENT_EXISTE_DEJA')

    # ── PUT ──
    def test_cleChiffrement_put_remplace_la_cle(self):
        utilisateur = self._creer_utilisateur("e2e6@example.com")
        CleChiffrementUtilisateur.objects.create(utilisateur=utilisateur, cle_publique="ancienne")
        token = self._token(utilisateur)

        response = self.client.put(
            '/Registration/cle-chiffrement/',
            data=json.dumps({'cle_publique': 'nouvelle-cle', 'sel_kdf': 'nouveau-sel'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        cle = CleChiffrementUtilisateur.objects.get(utilisateur=utilisateur)
        self.assertEqual(cle.cle_publique, 'nouvelle-cle')
        self.assertEqual(cle.sel_kdf, 'nouveau-sel')

    def test_cleChiffrement_put_404_si_non_configuree(self):
        utilisateur = self._creer_utilisateur("e2e7@example.com")
        token = self._token(utilisateur)

        response = self.client.put(
            '/Registration/cle-chiffrement/',
            data=json.dumps({'cle_publique': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_cleChiffrement_refuse_methode_delete(self):
        utilisateur = self._creer_utilisateur("e2e8@example.com")
        token = self._token(utilisateur)
        response = self.client.delete('/Registration/cle-chiffrement/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 405)

    # ── clePubliqueUtilisateur ──
    def test_clePubliqueUtilisateur_succes(self):
        proprietaire = self._creer_utilisateur("e2e9@example.com")
        CleChiffrementUtilisateur.objects.create(utilisateur=proprietaire, cle_publique="cle-publique-visible")
        demandeur = self._creer_utilisateur("e2e10@example.com")
        token = self._token(demandeur)

        response = self.client.get(
            f'/Registration/cle-chiffrement/publique/?utilisateur_id={proprietaire.id}',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['cle_publique'], 'cle-publique-visible')

    def test_clePubliqueUtilisateur_404_si_non_configuree(self):
        proprietaire = self._creer_utilisateur("e2e11@example.com")
        demandeur = self._creer_utilisateur("e2e12@example.com")
        token = self._token(demandeur)

        response = self.client.get(
            f'/Registration/cle-chiffrement/publique/?utilisateur_id={proprietaire.id}',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_clePubliqueUtilisateur_refuse_sans_utilisateur_id(self):
        demandeur = self._creer_utilisateur("e2e13@example.com")
        token = self._token(demandeur)
        response = self.client.get('/Registration/cle-chiffrement/publique/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 400)

    def test_clePubliqueUtilisateur_refuse_sans_token(self):
        response = self.client.get('/Registration/cle-chiffrement/publique/?utilisateur_id=1')
        self.assertEqual(response.status_code, 401)

    # ── clesPubliquesAdmins ──
    def test_clesPubliquesAdmins_ne_retourne_que_les_admins(self):
        admin = self._creer_utilisateur("e2e14@example.com", role='admin')
        CleChiffrementUtilisateur.objects.create(utilisateur=admin, cle_publique="cle-admin")
        acheteur = self._creer_utilisateur("e2e15@example.com")
        CleChiffrementUtilisateur.objects.create(utilisateur=acheteur, cle_publique="cle-acheteur")
        token = self._token(acheteur)

        response = self.client.get('/Registration/cle-chiffrement/admins/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['admins']), 1)
        self.assertEqual(data['admins'][0]['utilisateur_id'], admin.id)

    def test_clesPubliquesAdmins_liste_vide_si_aucun_admin_configure(self):
        acheteur = self._creer_utilisateur("e2e16@example.com")
        token = self._token(acheteur)
        response = self.client.get('/Registration/cle-chiffrement/admins/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['admins'], [])

    def test_clesPubliquesAdmins_refuse_sans_token(self):
        response = self.client.get('/Registration/cle-chiffrement/admins/')
        self.assertEqual(response.status_code, 401)



