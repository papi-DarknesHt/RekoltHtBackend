import json
import secrets
from django.test import TestCase
from .models import Utilisateur, Profil, haser_password, verifier_password
from . import views

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


class TestRegistrationViews(TestCase):

    def setUp(self):
        views.TOKENS.clear()

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
        views.TOKENS[token] = utilisateur.id
        return token

    def test_sinscrire_creates_user_and_returns_token(self):
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
        self.assertEqual(data['message'], 'Utilisateur inscrit avec succès')
        self.assertIn('token', data)
        self.assertIn(data['token'], views.TOKENS)
        self.assertEqual(data['utilisateur']['email'], payload['email'])
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
        self.assertIn(data['token'], views.TOKENS)

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
        self.assertEqual(data['error'], 'Email ou mot de passe incorrect')

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
        self.assertNotIn(token, views.TOKENS)

        utilisateur.refresh_from_db()
        self.assertFalse(utilisateur.est_actif)

    def test_profilAfficher_get_and_put(self):
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

        put_payload = {
            'nom': 'Updated',
            'prenom': 'User',
            'bio': 'Nouvelle bio',
            'ville': 'Cap-Haïtien'
        }
        put_response = self.client.put(
            '/Registration/profil/',
            data=json.dumps(put_payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )
        self.assertEqual(put_response.status_code, 200)
        put_data = json.loads(put_response.content)
        self.assertEqual(put_data['utilisateur']['nom'], 'Updated')
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



