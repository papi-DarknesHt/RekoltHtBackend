import json
import secrets
import base64
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

    def test_modifierProfil_ignore_role_envoye_par_client(self):
        utilisateur = self._create_utilisateur(email='roletamper@example.com')
        token = self._create_token_for(utilisateur)

        payload = {'bio': 'Nouvelle bio', 'role': 'admin'}
        response = self.client.put(
            '/Registration/modifier-profil/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        utilisateur.refresh_from_db()
        # la bio est bien mise à jour...
        self.assertEqual(utilisateur.profil.bio, 'Nouvelle bio')
        # ...mais le rôle envoyé par le client est ignoré
        self.assertNotEqual(utilisateur.profil.role, 'admin')
        self.assertEqual(utilisateur.profil.role, 'acheteur')

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

    # ---------- Tests pour devenirVendeur ----------
    def _make_base64_file(self, content_bytes, filename, mime):
        encoded = base64.b64encode(content_bytes).decode()
        return {
            'content': f'data:{mime};base64,{encoded}',
            'filename': filename
        }

    def test_devenir_vendeur_individu_success(self):
        utilisateur = self._create_utilisateur(email='individu@example.com')
        token = self._create_token_for(utilisateur)

        response = self.client.post(
            '/Registration/devenir-vendeur/',
            data=json.dumps({'type_vendeur': 'individu'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message'], 'Devenu vendeur (individu) avec succès')
        utilisateur.refresh_from_db()
        self.assertEqual(utilisateur.profil.type_vendeur, 'individu')
        # profil.entreprise may not exist for individu; statut attendu via proxy is 'valide'
        self.assertEqual(utilisateur.profil.statut_verification, 'valide')

    def test_devenir_vendeur_entreprise_missing_piece_first_submission(self):
        utilisateur = self._create_utilisateur(email='entreprise1@example.com')
        token = self._create_token_for(utilisateur)

        payload = {'type_vendeur': 'entreprise', 'nom_entreprise': 'Ferme Test'}
        response = self.client.post(
            '/Registration/devenir-vendeur/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('Une pièce justificative est requise', data['error'])

    def test_devenir_vendeur_entreprise_with_document_success(self):
        utilisateur = self._create_utilisateur(email='entreprise2@example.com')
        token = self._create_token_for(utilisateur)

        # petit fichier PNG simulé
        fichier = self._make_base64_file(b'PNGDATA', 'doc.png', 'image/png')

        payload = {
            'type_vendeur': 'entreprise',
            'nom_entreprise': 'Ferme Lakou Vert',
            'piece_justificative': fichier,
        }

        response = self.client.post(
            '/Registration/devenir-vendeur/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message'], 'Demande vendeur entreprise soumise')
        utilisateur.refresh_from_db()
        self.assertEqual(utilisateur.profil.type_vendeur, 'entreprise')
        # vérifier via la relation Entreprise
        self.assertTrue(hasattr(utilisateur.profil, 'entreprise'))
        self.assertEqual(utilisateur.profil.entreprise.statut_verification, 'en_attente')
        # le fichier doit exister dans le champ Entreprise
        self.assertTrue(bool(utilisateur.profil.entreprise.piece_justificative))

    def test_devenir_vendeur_invalid_type(self):
        utilisateur = self._create_utilisateur(email='invalidtype@example.com')
        token = self._create_token_for(utilisateur)

        response = self.client.post(
            '/Registration/devenir-vendeur/',
            data=json.dumps({'type_vendeur': 'autre'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 400)

    def test_devenir_vendeur_without_token(self):
        response = self.client.post(
            '/Registration/devenir-vendeur/',
            data=json.dumps({'type_vendeur': 'individu'}),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 401)

    def test_devenir_vendeur_rejects_bad_extension(self):
        utilisateur = self._create_utilisateur(email='badext@example.com')
        token = self._create_token_for(utilisateur)

        # .exe non autorisé
        fichier = self._make_base64_file(b'EXEDATA', 'malware.exe', 'application/octet-stream')

        payload = {
            'type_vendeur': 'entreprise',
            'nom_entreprise': 'Ferme Mal',
            'piece_justificative': fichier,
        }

        response = self.client.post(
            '/Registration/devenir-vendeur/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 400)

    def test_devenir_vendeur_rejects_large_file(self):
        utilisateur = self._create_utilisateur(email='toolarge@example.com')
        token = self._create_token_for(utilisateur)

        # générer un fichier de 6 Mo
        big = b'A' * (6 * 1024 * 1024)
        fichier = self._make_base64_file(big, 'big.pdf', 'application/pdf')

        payload = {
            'type_vendeur': 'entreprise',
            'nom_entreprise': 'Ferme Big',
            'piece_justificative': fichier,
        }

        response = self.client.post(
            '/Registration/devenir-vendeur/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}'
        )

        self.assertEqual(response.status_code, 400)

    def test_deposer_nouveau_document_remplace_le_precendent(self):
        utilisateur = self._create_utilisateur(email='replace@example.com')
        token = self._create_token_for(utilisateur)

        # d'abord soumettre une entreprise avec document
        fichier1 = self._make_base64_file(b'FIRST', 'first.png', 'image/png')
        payload1 = {'type_vendeur': 'entreprise', 'nom_entreprise': 'Ferme1', 'piece_justificative': fichier1}
        r1 = self.client.post('/Registration/devenir-vendeur/', data=json.dumps(payload1), content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(r1.status_code, 200)
        utilisateur.refresh_from_db()
        old_path = utilisateur.profil.entreprise.piece_justificative.path
        self.assertTrue(old_path)

        # soumettre un nouveau document
        fichier2 = self._make_base64_file(b'SECOND', 'second.png', 'image/png')
        payload2 = {'type_vendeur': 'entreprise', 'nom_entreprise': 'Ferme1', 'piece_justificative': fichier2}
        r2 = self.client.post('/Registration/devenir-vendeur/', data=json.dumps(payload2), content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(r2.status_code, 200)
        utilisateur.refresh_from_db()
        new_path = utilisateur.profil.entreprise.piece_justificative.path
        self.assertNotEqual(old_path, new_path)
        # l'ancien fichier devrait avoir été supprimé ; vérifier qu'il n'existe plus
        import os
        self.assertFalse(os.path.exists(old_path))

    # ---------- Tests pour l'inscription en tant qu'entreprise ----------
    def test_sinscrire_without_type_vendeur_behaves_normally(self):
        payload = {
            'nom': 'Buyer',
            'prenom': 'Solo',
            'email': 'buyer@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '5555555555'
        }
        response = self.client.post('/Registration/inscription/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Utilisateur.objects.filter(email=payload['email']).exists())
        utilisateur = Utilisateur.objects.get(email=payload['email'])
        self.assertEqual(utilisateur.profil.role, 'acheteur')
        self.assertIsNone(utilisateur.profil.type_vendeur)

    def test_sinscrire_with_type_individu_sets_type(self):
        payload = {
            'nom': 'Ind',
            'prenom': 'Person',
            'email': 'ind@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '5550000000',
            'type_vendeur': 'individu'
        }
        response = self.client.post('/Registration/inscription/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 201)
        utilisateur = Utilisateur.objects.get(email=payload['email'])
        self.assertEqual(utilisateur.profil.type_vendeur, 'individu')

    def test_sinscrire_with_type_entreprise_creates_entreprise(self):
        # préparer un petit fichier valide
        fichier = self._make_base64_file(b'DOC', 'patente.pdf', 'application/pdf')
        payload = {
            'nom': 'Ent',
            'prenom': 'Rep',
            'email': 'ent@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '5551111111',
            'type_vendeur': 'entreprise',
            'nom_entreprise': 'Ferme Lakou Vert',
            'piece_justificative': fichier,
        }
        response = self.client.post('/Registration/inscription/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 201)
        utilisateur = Utilisateur.objects.get(email=payload['email'])
        self.assertEqual(utilisateur.profil.role, 'acheteur')
        self.assertEqual(utilisateur.profil.type_vendeur, 'entreprise')
        # l'objet Entreprise doit être créé
        from .entreprise import Entreprise
        self.assertTrue(Entreprise.objects.filter(profil=utilisateur.profil).exists())
        ent = Entreprise.objects.get(profil=utilisateur.profil)
        self.assertEqual(ent.statut_verification, 'en_attente')

    def test_sinscrire_with_type_entreprise_missing_nom_fails_no_user_created(self):
        fichier = self._make_base64_file(b'DOC', 'patente.pdf', 'application/pdf')
        payload = {
            'nom': 'NoName',
            'prenom': 'Missing',
            'email': 'noname@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '5552222222',
            'type_vendeur': 'entreprise',
            'piece_justificative': fichier,
        }
        response = self.client.post('/Registration/inscription/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Utilisateur.objects.filter(email=payload['email']).exists())

    def test_sinscrire_with_type_entreprise_missing_piece_fails_no_user_created(self):
        payload = {
            'nom': 'NoPiece',
            'prenom': 'Missing',
            'email': 'nopic@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '5553333333',
            'type_vendeur': 'entreprise',
            'nom_entreprise': 'Ferme Sans Piece'
        }
        response = self.client.post('/Registration/inscription/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Utilisateur.objects.filter(email=payload['email']).exists())

    def test_sinscrire_with_type_entreprise_invalid_extension_fails_no_user_created(self):
        fichier = self._make_base64_file(b'EXE', 'bad.exe', 'application/octet-stream')
        payload = {
            'nom': 'BadExt',
            'prenom': 'Fail',
            'email': 'badextsignup@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '5554444444',
            'type_vendeur': 'entreprise',
            'nom_entreprise': 'BadExt Farm',
            'piece_justificative': fichier,
        }
        response = self.client.post('/Registration/inscription/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Utilisateur.objects.filter(email=payload['email']).exists())

    def test_signup_then_devenir_vendeur_reuses_same_entreprise(self):
        # s'inscrire d'abord en tant qu'entreprise
        fichier = self._make_base64_file(b'DOC', 'patente.pdf', 'application/pdf')
        payload = {
            'nom': 'Reuse',
            'prenom': 'Test',
            'email': 'reuse@example.com',
            'mot_de_passe': 'secret123',
            'telephone': '5556666666',
            'type_vendeur': 'entreprise',
            'nom_entreprise': 'Ferme Reuse',
            'piece_justificative': fichier,
        }
        r = self.client.post('/Registration/inscription/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 201)
        utilisateur = Utilisateur.objects.get(email=payload['email'])
        from .entreprise import Entreprise
        self.assertEqual(Entreprise.objects.filter(profil=utilisateur.profil).count(), 1)

        # maintenant appeler devenirVendeur sans nouveau document
        token = secrets.token_hex(32)
        views.TOKENS[token] = utilisateur.id
        r2 = self.client.post('/Registration/devenir-vendeur/', data=json.dumps({'type_vendeur': 'entreprise', 'nom_entreprise': 'Ferme Reuse'}), content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(r2.status_code, 200)
        # le role doit devenir vendeur
        utilisateur.refresh_from_db()
        self.assertEqual(utilisateur.profil.role, 'vendeur')
        # toujours un seul objet Entreprise
        self.assertEqual(Entreprise.objects.filter(profil=utilisateur.profil).count(), 1)

    def test_update_nom_entreprise_seul_ne_change_pas_statut(self):
        utilisateur = self._create_utilisateur(email='update_nom@example.com')
        token = self._create_token_for(utilisateur)

        # créer d'abord entreprise avec document
        fichier = self._make_base64_file(b'DATA', 'doc.png', 'image/png')
        payload1 = {'type_vendeur': 'entreprise', 'nom_entreprise': 'FermeOld', 'piece_justificative': fichier}
        r1 = self.client.post('/Registration/devenir-vendeur/', data=json.dumps(payload1), content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(r1.status_code, 200)
        utilisateur.refresh_from_db()
        statut_before = utilisateur.profil.entreprise.statut_verification

        # mettre à jour juste le nom
        payload2 = {'type_vendeur': 'entreprise', 'nom_entreprise': 'FermeNew'}
        r2 = self.client.post('/Registration/devenir-vendeur/', data=json.dumps(payload2), content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(r2.status_code, 200)
        utilisateur.refresh_from_db()
        self.assertEqual(utilisateur.profil.entreprise.statut_verification, statut_before)

    def test_client_cannot_set_statut_directement(self):
        utilisateur = self._create_utilisateur(email='tamper@example.com')
        token = self._create_token_for(utilisateur)

        payload = {'type_vendeur': 'entreprise', 'nom_entreprise': 'FermeTamper', 'statut_verification': 'valide'}
        # absence de piece justificative => doit échouer même si client envoie statut_verification
        r = self.client.post('/Registration/devenir-vendeur/', data=json.dumps(payload), content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(r.status_code, 400)



