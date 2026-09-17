import json
import secrets
from django.test import TestCase, override_settings

from Registration.models import Utilisateur, Token, DroitsAdmin, haser_password
from .models import Conversation, Message, SignalementMessage, MessageSupport


class MessagerieTestCase(TestCase):
    """Base commune : deux utilisateurs (acheteur/vendeur, rôle sans
    importance ici) prêts à échanger des messages privés. Voir Registration/
    tests.py::TestDroitsAdminPermissions pour le rappel sur le bootstrap du
    tout premier compte — sans incidence ici (aucun test ne vérifie le rôle
    admin), mais le role est quand même forcé par prudence/lisibilité."""

    def _creer_utilisateur(self, email, role='acheteur'):
        utilisateur = Utilisateur.objects.create(
            nom="Nom", prenom="Prenom", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000000",
        )
        utilisateur.profil.role = role
        utilisateur.profil.save()
        return utilisateur

    def _creer_token(self, utilisateur):
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def _creer_admin(self, email, **droits):
        # voir Registration/tests.py::TestDroitsAdminPermissions pour le
        # rappel sur le bootstrap du tout premier compte de la base — même
        # prudence appliquée ici (update_or_create + valeurs explicites)
        utilisateur = self._creer_utilisateur(email, role='admin')
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


class TestConversations(MessagerieTestCase):

    def test_demarrerConversation_cree_puis_reutilise_la_meme(self):
        alice = self._creer_utilisateur("alice@example.com")
        bob = self._creer_utilisateur("bob@example.com")
        token = self._creer_token(alice)

        response1 = self.client.post(
            '/messagerie/conversations/demarrer/',
            data=json.dumps({'destinataire_id': bob.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response1.status_code, 200)
        conv_id_1 = json.loads(response1.content)['conversation']['id']

        # même paire redemandée (dans l'autre sens : bob -> alice) doit
        # retourner la MÊME conversation (voir Conversation.obtenir_ou_creer,
        # normalisation par id croissant)
        token_bob = self._creer_token(bob)
        response2 = self.client.post(
            '/messagerie/conversations/demarrer/',
            data=json.dumps({'destinataire_id': alice.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_bob}',
        )
        conv_id_2 = json.loads(response2.content)['conversation']['id']
        self.assertEqual(conv_id_1, conv_id_2)
        self.assertEqual(Conversation.objects.count(), 1)

    def test_demarrerConversation_refuse_avec_soi_meme(self):
        alice = self._creer_utilisateur("alice2@example.com")
        token = self._creer_token(alice)
        response = self.client.post(
            '/messagerie/conversations/demarrer/',
            data=json.dumps({'destinataire_id': alice.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_demarrerConversation_refuse_pour_compte_bloque_sauf_admin(self):
        alice = self._creer_utilisateur("alice3@example.com")
        alice.est_bloquer = True
        alice.save()
        bob = self._creer_utilisateur("bob3@example.com")
        admin = self._creer_utilisateur("admin3@example.com", role='admin')
        token = self._creer_token(alice)

        refuse = self.client.post(
            '/messagerie/conversations/demarrer/',
            data=json.dumps({'destinataire_id': bob.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(refuse.status_code, 403)
        self.assertEqual(json.loads(refuse.content)['error_code'], 'COMPTE_BLOQUE')

        # mais peut toujours contacter un admin
        autorise = self.client.post(
            '/messagerie/conversations/demarrer/',
            data=json.dumps({'destinataire_id': admin.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(autorise.status_code, 200)

    def test_mesConversations_exclut_celles_supprimees_pour_moi(self):
        alice = self._creer_utilisateur("alice4@example.com")
        bob = self._creer_utilisateur("bob4@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token = self._creer_token(alice)

        avant = self.client.get('/messagerie/conversations/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(len(json.loads(avant.content)['conversations']), 1)

        suppr = self.client.delete(
            '/messagerie/conversations/supprimer-pour-moi/',
            data=json.dumps({'id': conversation.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(suppr.status_code, 200)

        apres = self.client.get('/messagerie/conversations/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(len(json.loads(apres.content)['conversations']), 0)

        # l'autre participant continue de la voir normalement
        token_bob = self._creer_token(bob)
        vue_bob = self.client.get('/messagerie/conversations/', HTTP_AUTHORIZATION=f'Token {token_bob}')
        self.assertEqual(len(json.loads(vue_bob.content)['conversations']), 1)


class TestMessages(MessagerieTestCase):

    def test_envoyerMessage_puis_le_relire_dechiffre(self):
        alice = self._creer_utilisateur("alice5@example.com")
        bob = self._creer_utilisateur("bob5@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token = self._creer_token(alice)

        envoi = self.client.post(
            '/messagerie/messages/envoyer/',
            data=json.dumps({'conversation_id': conversation.id, 'contenu': 'Bonjour Bob !'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(envoi.status_code, 201)
        data = json.loads(envoi.content)
        # le contenu renvoyé est déjà en clair (déchiffrement serveur) même
        # si le message est stocké chiffré en base (voir Message.chiffre)
        self.assertEqual(data['message']['contenu'], 'Bonjour Bob !')

        message_db = Message.objects.get(id=data['message']['id'])
        self.assertTrue(message_db.chiffre)
        self.assertNotEqual(message_db.contenu, 'Bonjour Bob !')  # bien chiffré en base

        # bob relit la conversation et voit le texte en clair
        token_bob = self._creer_token(bob)
        lecture = self.client.get(
            f'/messagerie/messages/?conversation_id={conversation.id}',
            HTTP_AUTHORIZATION=f'Token {token_bob}',
        )
        self.assertEqual(lecture.status_code, 200)
        messages = json.loads(lecture.content)['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['contenu'], 'Bonjour Bob !')

    def test_envoyerMessage_refuse_conversation_etrangere(self):
        alice = self._creer_utilisateur("alice6@example.com")
        bob = self._creer_utilisateur("bob6@example.com")
        charlie = self._creer_utilisateur("charlie6@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token_charlie = self._creer_token(charlie)

        response = self.client.post(
            '/messagerie/messages/envoyer/',
            data=json.dumps({'conversation_id': conversation.id, 'contenu': 'Je m\'incruste'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_charlie}',
        )
        self.assertEqual(response.status_code, 404)

    def test_envoyerMessage_refuse_vide_sans_produit(self):
        alice = self._creer_utilisateur("alice7@example.com")
        bob = self._creer_utilisateur("bob7@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token = self._creer_token(alice)

        response = self.client.post(
            '/messagerie/messages/envoyer/',
            data=json.dumps({'conversation_id': conversation.id, 'contenu': '   '}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_messagesConversation_marque_comme_lu(self):
        alice = self._creer_utilisateur("alice8@example.com")
        bob = self._creer_utilisateur("bob8@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token_alice = self._creer_token(alice)
        self.client.post(
            '/messagerie/messages/envoyer/',
            data=json.dumps({'conversation_id': conversation.id, 'contenu': 'Salut'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_alice}',
        )
        message = conversation.messages.first()
        self.assertFalse(message.lu)

        token_bob = self._creer_token(bob)
        self.client.get(
            f'/messagerie/messages/?conversation_id={conversation.id}',
            HTTP_AUTHORIZATION=f'Token {token_bob}',
        )
        message.refresh_from_db()
        self.assertTrue(message.lu)

    def test_supprimerMessagePourMoi_ne_le_supprime_pas_pour_l_autre(self):
        alice = self._creer_utilisateur("alice9@example.com")
        bob = self._creer_utilisateur("bob9@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token_alice = self._creer_token(alice)
        envoi = self.client.post(
            '/messagerie/messages/envoyer/',
            data=json.dumps({'conversation_id': conversation.id, 'contenu': 'À supprimer'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_alice}',
        )
        message_id = json.loads(envoi.content)['message']['id']

        suppr = self.client.delete(
            '/messagerie/messages/supprimer-pour-moi/',
            data=json.dumps({'id': message_id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_alice}',
        )
        self.assertEqual(suppr.status_code, 200)

        vue_alice = self.client.get(
            f'/messagerie/messages/?conversation_id={conversation.id}',
            HTTP_AUTHORIZATION=f'Token {token_alice}',
        )
        self.assertEqual(len(json.loads(vue_alice.content)['messages']), 0)

        token_bob = self._creer_token(bob)
        vue_bob = self.client.get(
            f'/messagerie/messages/?conversation_id={conversation.id}',
            HTTP_AUTHORIZATION=f'Token {token_bob}',
        )
        self.assertEqual(len(json.loads(vue_bob.content)['messages']), 1)


class TestSignalementMessage(MessagerieTestCase):

    def _envoyer(self, expediteur, conversation, contenu, token):
        return self.client.post(
            '/messagerie/messages/envoyer/',
            data=json.dumps({'conversation_id': conversation.id, 'contenu': contenu}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )

    def test_signalerMessage_succes(self):
        alice = self._creer_utilisateur("alice10@example.com")
        bob = self._creer_utilisateur("bob10@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token_alice = self._creer_token(alice)
        envoi = self._envoyer(alice, conversation, "Message limite", token_alice)
        message_id = json.loads(envoi.content)['message']['id']

        token_bob = self._creer_token(bob)
        response = self.client.post(
            '/messagerie/messages/signaler/',
            data=json.dumps({
                'message_id': message_id,
                'type_probleme': 'harcelement',
                'motif': 'Contenu menaçant',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_bob}',
        )
        self.assertEqual(response.status_code, 201)
        signalement = SignalementMessage.objects.get(message_id=message_id)
        # le contenu signalé est capturé en clair (déchiffré côté serveur),
        # pas le ciphertext stocké en base pour le message lui-même
        self.assertEqual(signalement.contenu_signale, "Message limite")

    def test_signalerMessage_refuse_son_propre_message(self):
        alice = self._creer_utilisateur("alice11@example.com")
        bob = self._creer_utilisateur("bob11@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token_alice = self._creer_token(alice)
        envoi = self._envoyer(alice, conversation, "Mon message", token_alice)
        message_id = json.loads(envoi.content)['message']['id']

        response = self.client.post(
            '/messagerie/messages/signaler/',
            data=json.dumps({'message_id': message_id, 'type_probleme': 'spam', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_alice}',
        )
        self.assertEqual(response.status_code, 400)

    def test_signalerMessage_refuse_non_participant(self):
        alice = self._creer_utilisateur("alice12@example.com")
        bob = self._creer_utilisateur("bob12@example.com")
        charlie = self._creer_utilisateur("charlie12@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token_alice = self._creer_token(alice)
        envoi = self._envoyer(alice, conversation, "Privé", token_alice)
        message_id = json.loads(envoi.content)['message']['id']

        token_charlie = self._creer_token(charlie)
        response = self.client.post(
            '/messagerie/messages/signaler/',
            data=json.dumps({'message_id': message_id, 'type_probleme': 'spam', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_charlie}',
        )
        self.assertEqual(response.status_code, 403)

    def test_traiterSignalementMessage_admin(self):
        alice = self._creer_utilisateur("alice13@example.com")
        bob = self._creer_utilisateur("bob13@example.com")
        conversation = Conversation.obtenir_ou_creer(alice, bob)
        token_alice = self._creer_token(alice)
        envoi = self._envoyer(alice, conversation, "À juger", token_alice)
        message_id = json.loads(envoi.content)['message']['id']

        token_bob = self._creer_token(bob)
        self.client.post(
            '/messagerie/messages/signaler/',
            data=json.dumps({'message_id': message_id, 'type_probleme': 'spam', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_bob}',
        )
        signalement = SignalementMessage.objects.get(message_id=message_id)

        admin = self._creer_admin("admin13@example.com", gestion_signalements=True)
        token_admin = self._creer_token(admin)
        response = self.client.post(
            '/messagerie/messages/signalements/traiter/',
            data=json.dumps({'ids': [signalement.id], 'explication': 'Rien à signaler'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_admin}',
        )
        self.assertEqual(response.status_code, 200)
        signalement.refresh_from_db()
        self.assertEqual(signalement.admin_traitant, admin)
        self.assertEqual(signalement.explication_decision, 'Rien à signaler')

    def test_traiterSignalementMessage_refuse_sans_droit(self):
        admin_sans_droit = self._creer_admin("admin14@example.com")  # aucun droit
        token = self._creer_token(admin_sans_droit)
        response = self.client.post(
            '/messagerie/messages/signalements/traiter/',
            data=json.dumps({'ids': [1], 'explication': 'peu importe'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)


# ── MESSAGES VENDEUR -> ADMINS (contacterAdmin/mesMessagesAdmin/repondre...) ──
# EMAIL_BACKEND=locmem : repondreMessageAdmin envoie un email de notification
# au vendeur (via envoyer_email_decision, déjà tolérant aux échecs) — évite un
# vrai essai SMTP réseau. SUPPORT_MASTER_KEY est déjà configurée en .env.dev
# (voir Messagerie/services/support_chiffrement_service.py), donc
# chiffrer_support/dechiffrer_support fonctionnent tels quels ici, sans mock.
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestMessagesAdmin(MessagerieTestCase):

    def test_contacterAdmin_succes_et_chiffre_le_contenu(self):
        vendeur = self._creer_utilisateur("vendeur_ca1@example.com", role='vendeur')
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/messagerie/admin/contacter/',
            data=json.dumps({'contenu': "J'ai un problème avec mon compte"}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['message_admin']['contenu'], "J'ai un problème avec mon compte")
        message_admin = MessageSupport.objects.get(vendeur=vendeur)
        # stocké chiffré en base, jamais en clair (coffre serveur)
        self.assertNotEqual(message_admin.contenu, "J'ai un problème avec mon compte")
        self.assertEqual(message_admin.format_chiffrement, 'coffre_serveur')

    def test_contacterAdmin_refuse_pour_un_admin(self):
        admin = self._creer_admin("admin_ca1@example.com")
        token = self._creer_token(admin)

        response = self.client.post(
            '/messagerie/admin/contacter/',
            data=json.dumps({'contenu': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'ADMIN_CANNOT_CONTACT_ADMIN')

    def test_contacterAdmin_refuse_message_vide(self):
        vendeur = self._creer_utilisateur("vendeur_ca2@example.com", role='vendeur')
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/messagerie/admin/contacter/',
            data=json.dumps({'contenu': '   '}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_contacterAdmin_refuse_message_trop_long(self):
        vendeur = self._creer_utilisateur("vendeur_ca3@example.com", role='vendeur')
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/messagerie/admin/contacter/',
            data=json.dumps({'contenu': 'x' * 1001}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'MESSAGE_TROP_LONG')

    def test_contacterAdmin_refuse_lien(self):
        vendeur = self._creer_utilisateur("vendeur_ca4@example.com", role='vendeur')
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/messagerie/admin/contacter/',
            data=json.dumps({'contenu': 'Voir https://exemple.com pour plus de details'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'LIEN_INTERDIT')

    def test_mesMessagesAdmin_scope_au_vendeur_connecte(self):
        vendeur1 = self._creer_utilisateur("vendeur_mm1@example.com", role='vendeur')
        vendeur2 = self._creer_utilisateur("vendeur_mm2@example.com", role='vendeur')
        MessageSupport.objects.create(vendeur=vendeur1, contenu="A", chiffre=False)
        MessageSupport.objects.create(vendeur=vendeur2, contenu="B", chiffre=False)
        token = self._creer_token(vendeur1)

        response = self.client.get('/messagerie/admin/mes-messages/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['messages_admin']), 1)
        self.assertEqual(data['messages_admin'][0]['contenu'], 'A')

    def test_listerMessagesAdminEnAttente_exclut_les_repondus(self):
        admin = self._creer_admin("admin_ea1@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_ea1@example.com", role='vendeur')
        MessageSupport.objects.create(vendeur=vendeur, contenu="En attente", chiffre=False)
        MessageSupport.objects.create(
            vendeur=vendeur, contenu="Deja repondu", chiffre=False,
            admin_repondant=admin, reponse="ok", reponse_format_chiffrement='coffre_serveur',
        )
        token = self._creer_token(admin)

        response = self.client.get('/messagerie/admin/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['messages_admin']), 1)
        self.assertEqual(data['messages_admin'][0]['contenu'], 'En attente')

    def test_listerMessagesAdminEnAttente_refuse_sans_droit(self):
        admin = self._creer_admin("admin_ea2@example.com")
        token = self._creer_token(admin)
        response = self.client.get('/messagerie/admin/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_listerMessagesAdminRepondus_limite_a_moi_seul_par_defaut(self):
        admin1 = self._creer_admin("admin_rep1@example.com", gestion_support=True)
        admin2 = self._creer_admin("admin_rep2@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_rep1@example.com", role='vendeur')
        MessageSupport.objects.create(
            vendeur=vendeur, contenu="Pour admin1", chiffre=False,
            admin_repondant=admin1, reponse="ok1", reponse_format_chiffrement='coffre_serveur',
        )
        MessageSupport.objects.create(
            vendeur=vendeur, contenu="Pour admin2", chiffre=False,
            admin_repondant=admin2, reponse="ok2", reponse_format_chiffrement='coffre_serveur',
        )
        token = self._creer_token(admin1)

        response = self.client.get('/messagerie/admin/repondus/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['messages_admin']), 1)
        self.assertEqual(data['messages_admin'][0]['contenu'], 'Pour admin1')

    def test_listerMessagesAdminRepondus_super_admin_voit_tout(self):
        super_admin = self._creer_admin("admin_rep3@example.com", super_admin=True)
        autre_admin = self._creer_admin("admin_rep4@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_rep2@example.com", role='vendeur')
        MessageSupport.objects.create(
            vendeur=vendeur, contenu="X", chiffre=False,
            admin_repondant=autre_admin, reponse="ok", reponse_format_chiffrement='coffre_serveur',
        )
        token = self._creer_token(super_admin)

        response = self.client.get('/messagerie/admin/repondus/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        self.assertEqual(len(data['messages_admin']), 1)

    def test_supprimerHistoriqueMessagesSupport_masque_sans_supprimer(self):
        admin = self._creer_admin("admin_hist1@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_hist1@example.com", role='vendeur')
        message_admin = MessageSupport.objects.create(
            vendeur=vendeur, contenu="X", chiffre=False,
            admin_repondant=admin, reponse="ok", reponse_format_chiffrement='coffre_serveur',
        )
        token = self._creer_token(admin)

        response = self.client.delete(
            '/messagerie/admin/repondus/supprimer/',
            data=json.dumps({'ids': [message_admin.id]}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['nombre_supprime'], 1)
        # la ligne MessageSupport existe toujours (masquée, pas supprimée)
        self.assertTrue(MessageSupport.objects.filter(id=message_admin.id).exists())

        # n'apparaît plus dans MON historique...
        liste = self.client.get('/messagerie/admin/repondus/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(json.loads(liste.content)['messages_admin'], [])

    def test_supprimerHistoriqueMessagesSupport_refuse_ids_manquants(self):
        admin = self._creer_admin("admin_hist2@example.com", gestion_support=True)
        token = self._creer_token(admin)
        response = self.client.delete(
            '/messagerie/admin/repondus/supprimer/', data=json.dumps({}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_repondreMessageAdmin_succes_prend_en_charge(self):
        admin = self._creer_admin("admin_rep5@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_rep3@example.com", role='vendeur')
        message_admin = MessageSupport.objects.create(vendeur=vendeur, contenu="Question", chiffre=False)
        token = self._creer_token(admin)

        response = self.client.post(
            '/messagerie/admin/repondre/',
            data=json.dumps({'id': message_admin.id, 'reponse': 'Voici la reponse'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message_admin']['reponse'], 'Voici la reponse')
        message_admin.refresh_from_db()
        self.assertEqual(message_admin.admin_repondant, admin)

    def test_repondreMessageAdmin_refuse_deja_pris_en_charge(self):
        admin1 = self._creer_admin("admin_rep6@example.com", gestion_support=True)
        admin2 = self._creer_admin("admin_rep7@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_rep4@example.com", role='vendeur')
        message_admin = MessageSupport.objects.create(
            vendeur=vendeur, contenu="Question", chiffre=False,
            admin_repondant=admin1, reponse="deja", reponse_format_chiffrement='coffre_serveur',
        )
        token = self._creer_token(admin2)

        response = self.client.post(
            '/messagerie/admin/repondre/',
            data=json.dumps({'id': message_admin.id, 'reponse': 'Trop tard'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 409)

    def test_repondreMessageAdmin_refuse_reponse_vide(self):
        admin = self._creer_admin("admin_rep8@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_rep5@example.com", role='vendeur')
        message_admin = MessageSupport.objects.create(vendeur=vendeur, contenu="Q", chiffre=False)
        token = self._creer_token(admin)

        response = self.client.post(
            '/messagerie/admin/repondre/',
            data=json.dumps({'id': message_admin.id, 'reponse': '  '}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_repondreMessageAdmin_introuvable(self):
        admin = self._creer_admin("admin_rep9@example.com", gestion_support=True)
        token = self._creer_token(admin)
        response = self.client.post(
            '/messagerie/admin/repondre/',
            data=json.dumps({'id': 999999, 'reponse': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_repondreMessageAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_rep10@example.com")
        vendeur = self._creer_utilisateur("vendeur_rep6@example.com", role='vendeur')
        message_admin = MessageSupport.objects.create(vendeur=vendeur, contenu="Q", chiffre=False)
        token = self._creer_token(admin)

        response = self.client.post(
            '/messagerie/admin/repondre/',
            data=json.dumps({'id': message_admin.id, 'reponse': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_migrerMessageVersCoffre_migre_le_contenu_legacy(self):
        admin = self._creer_admin("admin_mig1@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_mig1@example.com", role='vendeur')
        message_admin = MessageSupport.objects.create(
            vendeur=vendeur, contenu="ciphertext-legacy", chiffre=True, format_chiffrement='e2e_client',
        )
        token = self._creer_token(admin)

        response = self.client.post(
            '/messagerie/admin/migrer-vers-coffre/',
            data=json.dumps({'id': message_admin.id, 'contenu_dechiffre': 'Le vrai contenu'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        message_admin.refresh_from_db()
        self.assertEqual(message_admin.format_chiffrement, 'coffre_serveur')
        self.assertNotEqual(message_admin.contenu, 'Le vrai contenu')  # stocké re-chiffré, pas en clair

    def test_migrerMessageVersCoffre_idempotent_si_deja_coffre(self):
        admin = self._creer_admin("admin_mig2@example.com", gestion_support=True)
        vendeur = self._creer_utilisateur("vendeur_mig2@example.com", role='vendeur')
        message_admin = MessageSupport.objects.create(
            vendeur=vendeur, contenu="deja-coffre", chiffre=True, format_chiffrement='coffre_serveur',
        )
        token = self._creer_token(admin)

        response = self.client.post(
            '/messagerie/admin/migrer-vers-coffre/',
            data=json.dumps({'id': message_admin.id, 'contenu_dechiffre': 'Peu importe'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['message'], 'Rien à migrer')

    def test_genererRapportSupport_succes(self):
        super_admin = self._creer_admin("admin_rap1@example.com", super_admin=True)
        vendeur = self._creer_utilisateur("vendeur_rap1@example.com", role='vendeur')
        from django.utils import timezone
        MessageSupport.objects.create(
            vendeur=vendeur, contenu="Q", chiffre=False,
            admin_repondant=super_admin, reponse="R", reponse_format_chiffrement='coffre_serveur',
            date_reponse=timezone.now(),
        )
        token = self._creer_token(super_admin)

        response = self.client.get(
            '/messagerie/admin/rapport-audit/?date_debut=2020-01-01&date_fin=' + timezone.localdate().isoformat(),
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_genererRapportSupport_refuse_sans_droit(self):
        admin = self._creer_admin("admin_rap2@example.com", gestion_support=True)
        token = self._creer_token(admin)
        response = self.client.get(
            '/messagerie/admin/rapport-audit/?date_debut=2020-01-01&date_fin=2020-01-02',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_genererRapportSupport_refuse_dates_incoherentes(self):
        super_admin = self._creer_admin("admin_rap3@example.com", super_admin=True)
        token = self._creer_token(super_admin)
        response = self.client.get(
            '/messagerie/admin/rapport-audit/?date_debut=2020-01-10&date_fin=2020-01-01',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)


class TestSignalementsMessagesAdmin(MessagerieTestCase):

    def _envoyer(self, conversation, contenu, token):
        return self.client.post(
            '/messagerie/messages/envoyer/',
            data=json.dumps({'conversation_id': conversation.id, 'contenu': contenu}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )

    def _creer_signalement(self, expediteur_email, signaleur_email, contenu="Message a signaler"):
        expediteur = self._creer_utilisateur(expediteur_email)
        signaleur = self._creer_utilisateur(signaleur_email)
        conversation = Conversation.obtenir_ou_creer(expediteur, signaleur)
        token_expediteur = self._creer_token(expediteur)
        envoi = self._envoyer(conversation, contenu, token_expediteur)
        message_id = json.loads(envoi.content)['message']['id']

        token_signaleur = self._creer_token(signaleur)
        self.client.post(
            '/messagerie/messages/signaler/',
            data=json.dumps({'message_id': message_id, 'type_probleme': 'spam', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_signaleur}',
        )
        return SignalementMessage.objects.get(message_id=message_id)

    def test_listerSignalementsMessagesAdmin_ne_montre_que_en_attente(self):
        admin = self._creer_admin("admin_sig1@example.com", gestion_signalements=True)
        signalement = self._creer_signalement("exp1@example.com", "sig1@example.com")
        token = self._creer_token(admin)

        response = self.client.get('/messagerie/messages/signalements/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['signalements']), 1)
        self.assertEqual(data['signalements'][0]['id'], signalement.id)

    def test_listerSignalementsMessagesAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sig2@example.com")
        token = self._creer_token(admin)
        response = self.client.get('/messagerie/messages/signalements/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_listerSignalementsMessagesTraites_limite_a_moi_seul(self):
        admin1 = self._creer_admin("admin_sig3@example.com", gestion_signalements=True)
        admin2 = self._creer_admin("admin_sig4@example.com", gestion_signalements=True)
        signalement1 = self._creer_signalement("exp2@example.com", "sig2@example.com")
        signalement2 = self._creer_signalement("exp3@example.com", "sig3@example.com")
        from django.utils import timezone
        signalement1.admin_traitant = admin1
        signalement1.date_traitement = timezone.now()
        signalement1.explication_decision = "ok"
        signalement1.save()
        signalement2.admin_traitant = admin2
        signalement2.date_traitement = timezone.now()
        signalement2.explication_decision = "ok"
        signalement2.save()
        token = self._creer_token(admin1)

        response = self.client.get('/messagerie/messages/signalements/traites/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        self.assertEqual(len(data['signalements']), 1)
        self.assertEqual(data['signalements'][0]['id'], signalement1.id)

    def test_supprimerHistoriqueSignalementsMessages_masque_sans_supprimer(self):
        admin = self._creer_admin("admin_sig5@example.com", gestion_signalements=True)
        signalement = self._creer_signalement("exp4@example.com", "sig4@example.com")
        from django.utils import timezone
        signalement.admin_traitant = admin
        signalement.date_traitement = timezone.now()
        signalement.explication_decision = "ok"
        signalement.save()
        token = self._creer_token(admin)

        response = self.client.delete(
            '/messagerie/messages/signalements/traites/supprimer/',
            data=json.dumps({'ids': [signalement.id]}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['nombre_supprime'], 1)
        self.assertTrue(SignalementMessage.objects.filter(id=signalement.id).exists())

    def test_supprimerMessageAdmin_succes(self):
        # ce test a mis en évidence un bug réel : supprimerMessageAdmin ne
        # retournait jamais de HttpResponse sur le chemin de succès (Django
        # lève ValueError "The view didn't return an HttpResponse object" —
        # voir la correction dans Messagerie/views.py, un simple `return
        # JsonResponse(...)` manquait en fin de fonction).
        admin = self._creer_admin("admin_sig6@example.com", gestion_signalements=True)
        signalement = self._creer_signalement("exp5@example.com", "sig5@example.com")
        message_id = signalement.message_id
        token = self._creer_token(admin)

        response = self.client.delete(
            '/messagerie/messages/supprimer/',
            data=json.dumps({'id': message_id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Message.objects.filter(id=message_id).exists())
        # CASCADE supprime aussi le signalement lié
        self.assertFalse(SignalementMessage.objects.filter(id=signalement.id).exists())

    def test_supprimerMessageAdmin_introuvable(self):
        admin = self._creer_admin("admin_sig7@example.com", gestion_signalements=True)
        token = self._creer_token(admin)
        response = self.client.delete(
            '/messagerie/messages/supprimer/',
            data=json.dumps({'id': 999999}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_supprimerMessageAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sig8@example.com")
        signalement = self._creer_signalement("exp6@example.com", "sig6@example.com")
        token = self._creer_token(admin)

        response = self.client.delete(
            '/messagerie/messages/supprimer/',
            data=json.dumps({'id': signalement.message_id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
