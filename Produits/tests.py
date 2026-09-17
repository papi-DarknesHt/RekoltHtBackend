import json
from io import BytesIO

from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from Registration.models import Utilisateur, DroitsAdmin, VueProfilVendeur, haser_password
from .models import (
    Categories, sousCategories, Produits, SignalementProduit, SignalementVendeur,
    AvisProduit, SignalementAvis, photo_produits, VueProduit, ContactProduit,
)


def _image_upload(nom="photo.png"):
    """Construit un fichier multipart contenant une VRAIE image PNG décodable
    (1x1 pixel) — valider_fichier_upload_django (Registration/services/
    upload_validation_service.py) rejette tout contenu qui n'est pas une
    image Pillow-décodable, un simple b"contenu" ne suffit donc pas ici."""
    tampon = BytesIO()
    Image.new('RGB', (1, 1), color='red').save(tampon, format='PNG')
    tampon.seek(0)
    return SimpleUploadedFile(nom, tampon.read(), content_type='image/png')


class ProduitsTestCase(TestCase):
    """Base commune : crée un admin (droit gestion_categories) et un vendeur
    prêt à publier (rôle vendeur + catégorie déjà choisie), utilisés par la
    plupart des tests ci-dessous. Voir Registration/tests.py::
    TestDroitsAdminPermissions pour le rappel important sur le bootstrap du
    tout premier compte (devient admin/super_super_admin automatiquement) —
    même prudence appliquée ici (role et droits toujours forcés explicitement)."""

    def _creer_admin(self, email="admin@example.com", **droits):
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
        from Registration.models import Token
        import secrets
        cle = secrets.token_hex(32)
        Token.objects.create(utilisateur=utilisateur, cle=cle)
        return cle

    def _creer_categorie(self, nom="Produits Agricoles"):
        return Categories.objects.create(nom=nom, nom_ht="", nom_en="", description="")

    def _creer_vendeur_pret(self, email="vendeur@example.com", categorie=None):
        """Vendeur avec le rôle 'vendeur' et sa catégorie déjà choisie
        (condition requise par creerProduit, voir Produits/views/produitsViews.py)."""
        utilisateur = Utilisateur.objects.create(
            nom="Vend", prenom="Eur", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000002",
        )
        utilisateur.profil.role = 'vendeur'
        utilisateur.profil.departement = 'Ouest'
        utilisateur.profil.commune = 'Port-au-Prince'
        utilisateur.profil.save()
        if categorie is None:
            categorie = self._creer_categorie()
        utilisateur.profil.categories_produits.set([categorie])
        return utilisateur, categorie


# ── CATÉGORIES ────────────────────────────────────────────────────────────────
class TestCategoriesViews(ProduitsTestCase):

    def test_listerCategories_public_sans_authentification(self):
        self._creer_categorie("Produits Agricoles")
        response = self.client.get('/produits/categories/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['categories']), 1)
        self.assertEqual(data['categories'][0]['nom'], "Produits Agricoles")

    def test_creerCategorie_refuse_sans_token(self):
        response = self.client.post(
            '/produits/categories/creer/',
            data=json.dumps({'nom': 'Nouvelle'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_creerCategorie_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sans_droit@example.com")  # aucun droit
        token = self._creer_token(admin)
        response = self.client.post(
            '/produits/categories/creer/',
            data=json.dumps({'nom': 'Nouvelle'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_creerCategorie_avec_droit_gestion_categories(self):
        admin = self._creer_admin("admin_categories@example.com", gestion_categories=True)
        token = self._creer_token(admin)
        response = self.client.post(
            '/produits/categories/creer/',
            data=json.dumps({'nom': 'Électronique', 'nom_ht': 'Elektwonik', 'nom_en': 'Electronics'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['categorie']['nom'], 'Électronique')
        self.assertEqual(data['categorie']['nom_ht'], 'Elektwonik')
        self.assertTrue(Categories.objects.filter(nom='Électronique').exists())

    def test_modifierCategorie_met_a_jour_les_traductions(self):
        admin = self._creer_admin("admin_mod_cat@example.com", gestion_categories=True)
        token = self._creer_token(admin)
        categorie = self._creer_categorie("Ancien nom")

        response = self.client.put(
            '/produits/categories/modifier/',
            data=json.dumps({'id': categorie.id, 'nom_en': 'New name'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        categorie.refresh_from_db()
        self.assertEqual(categorie.nom_en, 'New name')
        self.assertEqual(categorie.nom, 'Ancien nom')  # champ non fourni : inchangé

    def test_supprimerCategorie(self):
        admin = self._creer_admin("admin_del_cat@example.com", gestion_categories=True)
        token = self._creer_token(admin)
        categorie = self._creer_categorie("À supprimer")

        response = self.client.delete(
            '/produits/categories/supprimer/',
            data=json.dumps({'id': categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Categories.objects.filter(id=categorie.id).exists())

    def test_choisirCategoriesVendeur(self):
        utilisateur, categorie = self._creer_vendeur_pret()
        # repartir de zéro pour tester l'endpoint choisir/ lui-même
        utilisateur.profil.categories_produits.clear()
        self.assertFalse(utilisateur.profil.a_choisi_categories())
        token = self._creer_token(utilisateur)

        response = self.client.post(
            '/produits/categories/choisir/',
            data=json.dumps({'categorie_ids': [categorie.id]}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(utilisateur.profil.a_choisi_categories())


# ── SOUS-CATÉGORIES ───────────────────────────────────────────────────────────
class TestSousCategoriesViews(ProduitsTestCase):

    def test_listerSousCategories_filtre_par_categorie(self):
        cat1 = self._creer_categorie("Cat 1")
        cat2 = self._creer_categorie("Cat 2")
        sousCategories.objects.create(categorie=cat1, nom="Sous 1")
        sousCategories.objects.create(categorie=cat2, nom="Sous 2")

        response = self.client.get(f'/produits/sous-categories/?categorie_id={cat1.id}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['sous_categories']), 1)
        self.assertEqual(data['sous_categories'][0]['nom'], "Sous 1")

    def test_creerSousCategorie_refuse_categorie_introuvable(self):
        admin = self._creer_admin("admin_sc@example.com", gestion_categories=True)
        token = self._creer_token(admin)
        response = self.client.post(
            '/produits/sous-categories/creer/',
            data=json.dumps({'nom': 'Sous X', 'categorie_id': 99999}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_listerSousCategories_sans_filtre_retourne_tout(self):
        cat1 = self._creer_categorie("Cat A")
        cat2 = self._creer_categorie("Cat B")
        sousCategories.objects.create(categorie=cat1, nom="Sous A")
        sousCategories.objects.create(categorie=cat2, nom="Sous B")

        response = self.client.get('/produits/sous-categories/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)['sous_categories']), 2)

    def test_listerSousCategories_refuse_methode_post(self):
        response = self.client.post('/produits/sous-categories/')
        self.assertEqual(response.status_code, 405)

    def test_creerSousCategorie_succes(self):
        admin = self._creer_admin("admin_sc2@example.com", gestion_categories=True)
        categorie = self._creer_categorie("Cat C")
        token = self._creer_token(admin)

        response = self.client.post(
            '/produits/sous-categories/creer/',
            data=json.dumps({'nom': 'Sous C', 'categorie_id': categorie.id, 'nom_ht': 'Su C', 'nom_en': 'Sub C'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['sous_categorie']['nom_ht'], 'Su C')
        self.assertTrue(sousCategories.objects.filter(nom='Sous C', categorie=categorie).exists())

    def test_creerSousCategorie_refuse_sans_nom(self):
        admin = self._creer_admin("admin_sc3@example.com", gestion_categories=True)
        categorie = self._creer_categorie("Cat D")
        token = self._creer_token(admin)

        response = self.client.post(
            '/produits/sous-categories/creer/',
            data=json.dumps({'categorie_id': categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_creerSousCategorie_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sc4@example.com")  # aucun droit
        categorie = self._creer_categorie("Cat E")
        token = self._creer_token(admin)

        response = self.client.post(
            '/produits/sous-categories/creer/',
            data=json.dumps({'nom': 'Sous E', 'categorie_id': categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_creerSousCategorie_refuse_sans_token(self):
        response = self.client.post(
            '/produits/sous-categories/creer/', data=json.dumps({'nom': 'X', 'categorie_id': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_modifierSousCategorie_succes(self):
        admin = self._creer_admin("admin_sc5@example.com", gestion_categories=True)
        categorie = self._creer_categorie("Cat F")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Ancien nom")
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/sous-categories/modifier/',
            data=json.dumps({'id': sous_categorie.id, 'nom': 'Nouveau nom'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        sous_categorie.refresh_from_db()
        self.assertEqual(sous_categorie.nom, 'Nouveau nom')

    def test_modifierSousCategorie_change_de_categorie_parente(self):
        admin = self._creer_admin("admin_sc6@example.com", gestion_categories=True)
        categorie_origine = self._creer_categorie("Cat G")
        categorie_cible = self._creer_categorie("Cat H")
        sous_categorie = sousCategories.objects.create(categorie=categorie_origine, nom="Sous G")
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/sous-categories/modifier/',
            data=json.dumps({'id': sous_categorie.id, 'categorie_id': categorie_cible.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        sous_categorie.refresh_from_db()
        self.assertEqual(sous_categorie.categorie_id, categorie_cible.id)

    def test_modifierSousCategorie_refuse_categorie_cible_introuvable(self):
        admin = self._creer_admin("admin_sc7@example.com", gestion_categories=True)
        categorie = self._creer_categorie("Cat I")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Sous I")
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/sous-categories/modifier/',
            data=json.dumps({'id': sous_categorie.id, 'categorie_id': 999999}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_modifierSousCategorie_introuvable(self):
        admin = self._creer_admin("admin_sc8@example.com", gestion_categories=True)
        token = self._creer_token(admin)
        response = self.client.put(
            '/produits/sous-categories/modifier/',
            data=json.dumps({'id': 999999, 'nom': 'X'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_modifierSousCategorie_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sc9@example.com")
        categorie = self._creer_categorie("Cat J")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Sous J")
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/sous-categories/modifier/',
            data=json.dumps({'id': sous_categorie.id, 'nom': 'X'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_supprimerSousCategorie_succes(self):
        admin = self._creer_admin("admin_sc10@example.com", gestion_categories=True)
        categorie = self._creer_categorie("Cat K")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Sous K")
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/sous-categories/supprimer/',
            data=json.dumps({'id': sous_categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(sousCategories.objects.filter(id=sous_categorie.id).exists())

    def test_supprimerSousCategorie_introuvable(self):
        admin = self._creer_admin("admin_sc11@example.com", gestion_categories=True)
        token = self._creer_token(admin)
        response = self.client.delete(
            '/produits/sous-categories/supprimer/',
            data=json.dumps({'id': 999999}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_supprimerSousCategorie_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sc12@example.com")
        categorie = self._creer_categorie("Cat L")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Sous L")
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/sous-categories/supprimer/',
            data=json.dumps({'id': sous_categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(sousCategories.objects.filter(id=sous_categorie.id).exists())

    def test_supprimerSousCategorie_refuse_sans_token(self):
        response = self.client.delete(
            '/produits/sous-categories/supprimer/', data=json.dumps({'id': 1}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)


# ── PRODUITS ───────────────────────────────────────────────────────────────────
class TestCreerProduit(ProduitsTestCase):

    def test_creerProduit_refuse_pour_un_acheteur(self):
        acheteur = Utilisateur.objects.create(
            nom="A", prenom="B", email="acheteur@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000003",
        )
        acheteur.profil.role = 'acheteur'
        acheteur.profil.save()
        token = self._creer_token(acheteur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({'nom': 'Tomate', 'categorie_id': 1, 'sous_categorie_id': 1}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_creerProduit_refuse_sans_categorie_choisie(self):
        vendeur = Utilisateur.objects.create(
            nom="V", prenom="D", email="vendeur_sans_cat@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000004",
        )
        vendeur.profil.role = 'vendeur'
        vendeur.profil.save()
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({'nom': 'Tomate', 'categorie_id': 1, 'sous_categorie_id': 1}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_creerProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret()
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Légumes")
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({
                'nom': 'Tomate',
                'categorie_id': categorie.id,
                'sous_categorie_id': sous_categorie.id,
                'prix': 50,
                'unitePrix': 'HTG',
                'unite_De_Mesure': 'kg',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(data['produit']['nom'], 'Tomate')
        self.assertEqual(data['produit']['categorie']['id'], categorie.id)
        self.assertTrue(Produits.objects.filter(nom='Tomate', vendeur=vendeur).exists())

    def test_creerProduit_refuse_sous_categorie_incoherente(self):
        vendeur, categorie = self._creer_vendeur_pret()
        autre_categorie = self._creer_categorie("Autre")
        sous_categorie_autre = sousCategories.objects.create(categorie=autre_categorie, nom="Sous autre")
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({
                'nom': 'Tomate',
                'categorie_id': categorie.id,
                'sous_categorie_id': sous_categorie_autre.id,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_creerProduit_refuse_compte_bloque(self):
        vendeur, categorie = self._creer_vendeur_pret(email="vendeur_bloque@example.com")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Légumes")
        vendeur.est_bloquer = True
        vendeur.save()
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({
                'nom': 'Tomate', 'categorie_id': categorie.id, 'sous_categorie_id': sous_categorie.id,
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)
        data = json.loads(response.content)
        self.assertEqual(data['error_code'], 'COMPTE_BLOQUE')


class TestListerProduits(ProduitsTestCase):

    def test_listerProduits_public_et_filtre_categorie(self):
        vendeur, categorie = self._creer_vendeur_pret()
        autre_categorie = self._creer_categorie("Autre cat")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Légumes")

        Produits.objects.create(
            vendeur=vendeur, categorie=categorie, sous_categorie=sous_categorie,
            nom="Tomate", region="Ouest",
        )
        Produits.objects.create(
            vendeur=vendeur, categorie=autre_categorie,
            nom="Autre produit", region="Ouest",
        )

        response = self.client.get('/produits/lister/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['produits']), 2)

        response_filtre = self.client.get(f'/produits/lister/?categorie_id={categorie.id}')
        data_filtre = json.loads(response_filtre.content)
        self.assertEqual(len(data_filtre['produits']), 1)
        self.assertEqual(data_filtre['produits'][0]['nom'], "Tomate")


# ── SIGNALEMENTS DE PRODUITS ─────────────────────────────────────────────────
class TestSignalementProduit(ProduitsTestCase):

    def _creer_produit(self, vendeur=None, categorie=None):
        if vendeur is None:
            vendeur, categorie = self._creer_vendeur_pret()
        elif categorie is None:
            categorie = self._creer_categorie()
        return Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Tomate", region="Ouest")

    def _acheteur(self, email):
        u = Utilisateur.objects.create(
            nom="A", prenom="B", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000005",
        )
        u.profil.role = 'acheteur'
        u.profil.save()
        return u

    def test_signalerProduit_succes(self):
        produit = self._creer_produit()
        acheteur = self._acheteur("signaleur1@example.com")
        token = self._creer_token(acheteur)

        response = self.client.post(
            '/produits/signaler/',
            data=json.dumps({
                'produit_id': produit.id, 'type_probleme': 'produit_obsolete', 'motif': 'Plus en stock',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SignalementProduit.objects.filter(produit=produit).count(), 1)

    def test_signalerProduit_refuse_son_propre_produit(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/signaler/',
            data=json.dumps({'produit_id': produit.id, 'type_probleme': 'autre', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_signalerProduit_desactive_automatiquement_au_5e(self):
        produit = self._creer_produit()
        produit.est_disponible = True
        produit.save(update_fields=['est_disponible'])

        for i in range(5):
            acheteur = self._acheteur(f"signaleur_auto_{i}@example.com")
            token = self._creer_token(acheteur)
            response = self.client.post(
                '/produits/signaler/',
                data=json.dumps({'produit_id': produit.id, 'type_probleme': 'autre', 'motif': f'motif {i}'}),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Token {token}',
            )
            self.assertEqual(response.status_code, 201)

        produit.refresh_from_db()
        self.assertTrue(produit.desactive_par_signalements)
        self.assertFalse(produit.est_disponible)
        # les 4 premiers signalements sont résolus automatiquement, seul le
        # 5e (celui qui a déclenché le seuil) reste visible dans la file admin
        self.assertEqual(
            SignalementProduit.objects.filter(produit=produit, resolu_automatiquement=True).count(), 4
        )
        self.assertEqual(
            SignalementProduit.objects.filter(produit=produit, resolu_automatiquement=False).count(), 1
        )

    def test_listerSignalementsAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_sig1@example.com")
        token = self._creer_token(admin)
        response = self.client.get('/produits/signalements/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_traiterSignalement_succes_et_deja_traite(self):
        produit = self._creer_produit()
        acheteur = self._acheteur("signaleur2@example.com")
        token_ach = self._creer_token(acheteur)
        envoi = self.client.post(
            '/produits/signaler/',
            data=json.dumps({'produit_id': produit.id, 'type_probleme': 'autre', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_ach}',
        )
        signalement_id = json.loads(envoi.content)['signalement']['id']

        admin = self._creer_admin("admin_sig2@example.com", gestion_signalements=True)
        token_admin = self._creer_token(admin)

        traiter = self.client.post(
            '/produits/signalements/traiter/',
            data=json.dumps({'ids': [signalement_id], 'explication': 'Vérifié, tout va bien'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_admin}',
        )
        self.assertEqual(traiter.status_code, 200)

        # retraiter le même signalement : plus rien à mettre à jour -> 409
        retraiter = self.client.post(
            '/produits/signalements/traiter/',
            data=json.dumps({'ids': [signalement_id], 'explication': 'Encore'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token_admin}',
        )
        self.assertEqual(retraiter.status_code, 409)


# ── SIGNALEMENTS DE VENDEUR ───────────────────────────────────────────────────
class TestSignalementVendeur(ProduitsTestCase):

    def _acheteur(self, email):
        u = Utilisateur.objects.create(
            nom="A", prenom="B", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000006",
        )
        u.profil.role = 'acheteur'
        u.profil.save()
        return u

    def test_signalerVendeur_refuse_soi_meme(self):
        vendeur, _ = self._creer_vendeur_pret()
        token = self._creer_token(vendeur)
        response = self.client.post(
            '/produits/signaler-vendeur/',
            data=json.dumps({'vendeur_id': vendeur.id, 'type_probleme': 'autre', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_signalerVendeur_suspend_au_6e_meme_motif(self):
        # seuil STRICTEMENT supérieur à SEUIL_SIGNALEMENTS_VENDEUR (5), donc le
        # 6e signalement du MÊME motif déclenche la suspension — voir
        # signalerVendeur, Produits/views/signalementsViews.py
        vendeur, categorie = self._creer_vendeur_pret(email="vendeur_suspendu@example.com")
        produit = Produits.objects.create(
            vendeur=vendeur, categorie=categorie, nom="Produit à bannir",
            region="Ouest", est_disponible=True,
        )

        for i in range(6):
            acheteur = self._acheteur(f"signaleur_vendeur_{i}@example.com")
            token = self._creer_token(acheteur)
            response = self.client.post(
                '/produits/signaler-vendeur/',
                data=json.dumps({
                    'vendeur_id': vendeur.id, 'type_probleme': 'arnaque_fraude', 'motif': f'motif {i}',
                }),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Token {token}',
            )
            self.assertEqual(response.status_code, 201)

        vendeur.refresh_from_db()
        self.assertTrue(vendeur.desactive_par_signalements)
        produit.refresh_from_db()
        self.assertFalse(produit.est_disponible)

    def test_signalerVendeur_pas_suspendu_avant_le_seuil(self):
        vendeur, _ = self._creer_vendeur_pret(email="vendeur_ok@example.com")
        for i in range(5):  # exactement 5, seuil pas encore dépassé (> 5 requis)
            acheteur = self._acheteur(f"signaleur_ok_{i}@example.com")
            token = self._creer_token(acheteur)
            self.client.post(
                '/produits/signaler-vendeur/',
                data=json.dumps({'vendeur_id': vendeur.id, 'type_probleme': 'non_reponse', 'motif': 'x'}),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Token {token}',
            )
        vendeur.refresh_from_db()
        self.assertFalse(vendeur.desactive_par_signalements)


# ── AVIS PRODUIT ───────────────────────────────────────────────────────────────
class TestAvisProduit(ProduitsTestCase):

    def _acheteur(self, email):
        u = Utilisateur.objects.create(
            nom="A", prenom="B", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000007",
        )
        u.profil.role = 'acheteur'
        u.profil.save()
        return u

    def test_creerModifierAvis_cree_puis_met_a_jour(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Mangue", region="Ouest")
        acheteur = self._acheteur("noteur1@example.com")
        token = self._creer_token(acheteur)

        creation = self.client.post(
            '/produits/avis/creer/',
            data=json.dumps({'produit_id': produit.id, 'note': 4, 'commentaire': 'Très bon'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(creation.status_code, 201)
        self.assertEqual(AvisProduit.objects.filter(produit=produit, auteur=acheteur).count(), 1)

        # même auteur, même produit -> met à jour, ne duplique pas
        maj = self.client.post(
            '/produits/avis/creer/',
            data=json.dumps({'produit_id': produit.id, 'note': 2, 'commentaire': 'Finalement moins bon'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(maj.status_code, 200)
        self.assertEqual(AvisProduit.objects.filter(produit=produit, auteur=acheteur).count(), 1)
        avis = AvisProduit.objects.get(produit=produit, auteur=acheteur)
        self.assertEqual(avis.note, 2)

        produit.refresh_from_db()
        self.assertEqual(produit.nombre_avis, 1)
        self.assertEqual(produit.note_moyenne, 2)

    def test_creerModifierAvis_refuse_son_propre_produit(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Café", region="Ouest")
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/avis/creer/',
            data=json.dumps({'produit_id': produit.id, 'note': 5}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_creerModifierAvis_refuse_note_hors_bornes(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Riz", region="Ouest")
        acheteur = self._acheteur("noteur2@example.com")
        token = self._creer_token(acheteur)

        response = self.client.post(
            '/produits/avis/creer/',
            data=json.dumps({'produit_id': produit.id, 'note': 8}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_listerAvisProduit_public(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Haricot", region="Ouest")
        AvisProduit.objects.create(produit=produit, auteur=self._acheteur("noteur3@example.com"), note=5)

        response = self.client.get(f'/produits/avis/lister/?produit_id={produit.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)['avis']), 1)

    def test_signalerAvis_refuse_son_propre_avis(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Mais", region="Ouest")
        acheteur = self._acheteur("noteur4@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=acheteur, note=3, commentaire="Moyen")
        token = self._creer_token(acheteur)

        response = self.client.post(
            '/produits/avis/signaler/',
            data=json.dumps({'avis_id': avis.id, 'type_probleme': 'autre', 'motif': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_signalerAvis_succes_capture_un_instantane(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Igname", region="Ouest")
        auteur_avis = self._acheteur("noteur5@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur_avis, note=1, commentaire="Terrible")
        signaleur = self._acheteur("signaleur_avis@example.com")
        token = self._creer_token(signaleur)

        response = self.client.post(
            '/produits/avis/signaler/',
            data=json.dumps({'avis_id': avis.id, 'type_probleme': 'faux_avis', 'motif': 'Semble faux'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        signalement = SignalementAvis.objects.get(avis=avis)
        self.assertEqual(signalement.avis_commentaire_snapshot, "Terrible")

    def test_listerAvisRecusVendeur_tous_produits_confondus(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_recus1@example.com")
        produit1 = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Banane", region="Ouest")
        produit2 = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Melon", region="Ouest")
        AvisProduit.objects.create(produit=produit1, auteur=self._acheteur("recus_a1@example.com"), note=5)
        AvisProduit.objects.create(produit=produit2, auteur=self._acheteur("recus_a2@example.com"), note=4)
        token = self._creer_token(vendeur)

        response = self.client.get('/produits/avis/recus/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['avis']), 2)
        self.assertIn('produit_nom', data['avis'][0])

    def test_listerAvisRecusVendeur_vide_pour_un_acheteur(self):
        acheteur = self._acheteur("recus_acheteur1@example.com")
        token = self._creer_token(acheteur)
        response = self.client.get('/produits/avis/recus/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['avis'], [])

    def test_listerAvisRecusVendeur_refuse_sans_token(self):
        response = self.client.get('/produits/avis/recus/')
        self.assertEqual(response.status_code, 401)

    def test_supprimerAvis_par_son_auteur_sans_raison(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_sup_avis1@example.com")
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Pastèque", region="Ouest")
        auteur = self._acheteur("sup_avis_auteur1@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur, note=3, commentaire="x")
        token = self._creer_token(auteur)

        response = self.client.delete(
            '/produits/avis/supprimer/', data=json.dumps({'id': avis.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AvisProduit.objects.filter(id=avis.id).exists())
        auteur.refresh_from_db()
        # l'auteur supprime lui-même son avis : jamais un avertissement
        # (réservé à une suppression décidée par un admin, voir docstring de la vue)
        self.assertEqual(auteur.nombre_avertissements, 0)

    def test_supprimerAvis_refuse_avis_dun_autre_sans_droit(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_sup_avis2@example.com")
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Ananas", region="Ouest")
        auteur = self._acheteur("sup_avis_auteur2@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur, note=3, commentaire="x")
        autre = self._acheteur("sup_avis_autre1@example.com")
        token = self._creer_token(autre)

        response = self.client.delete(
            '/produits/avis/supprimer/', data=json.dumps({'id': avis.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(AvisProduit.objects.filter(id=avis.id).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_supprimerAvis_admin_modere_avis_dautrui_avertit_lauteur(self):
        admin = self._creer_admin("admin_sup_avis1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_sup_avis3@example.com")
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Goyave", region="Ouest")
        auteur = self._acheteur("sup_avis_auteur3@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur, note=1, commentaire="Faux avis")
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/avis/supprimer/',
            data=json.dumps({'id': avis.id, 'raison': 'Avis frauduleux confirmé'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AvisProduit.objects.filter(id=avis.id).exists())
        auteur.refresh_from_db()
        self.assertEqual(auteur.nombre_avertissements, 1)

        from Messagerie.models import Message
        self.assertTrue(Message.objects.filter(expediteur=admin, conversation__participant_a=auteur).exists()
                         or Message.objects.filter(expediteur=admin, conversation__participant_b=auteur).exists())

    def test_supprimerAvis_admin_refuse_sans_raison_pour_autrui(self):
        admin = self._creer_admin("admin_sup_avis2@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_sup_avis4@example.com")
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Papaye", region="Ouest")
        auteur = self._acheteur("sup_avis_auteur4@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur, note=1, commentaire="x")
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/avis/supprimer/', data=json.dumps({'id': avis.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        # aucun avertissement/suppression tant que la raison n'est pas fournie
        self.assertTrue(AvisProduit.objects.filter(id=avis.id).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_supprimerAvis_bloque_le_compte_au_seuil_davertissements(self):
        admin = self._creer_admin("admin_sup_avis3@example.com", gestion_signalements=True)
        auteur = self._acheteur("sup_avis_auteur5@example.com")
        auteur.nombre_avertissements = 9  # SEUIL_AVERTISSEMENTS - 1
        auteur.save()
        token = self._creer_token(admin)

        vendeur, categorie = self._creer_vendeur_pret(email="v_sup_avis5@example.com")
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Corossol", region="Ouest")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur, note=1, commentaire="x")

        response = self.client.delete(
            '/produits/avis/supprimer/',
            data=json.dumps({'id': avis.id, 'raison': 'Encore un faux avis'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        auteur.refresh_from_db()
        self.assertEqual(auteur.nombre_avertissements, 10)
        self.assertTrue(auteur.est_bloquer)

    def test_supprimerAvis_admin_sans_droit_ne_peut_supprimer_que_le_sien(self):
        admin_sans_droit = self._creer_admin("admin_sup_avis4@example.com")  # aucun droit
        vendeur, categorie = self._creer_vendeur_pret(email="v_sup_avis6@example.com")
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Chadèque", region="Ouest")
        autre_auteur = self._acheteur("sup_avis_auteur6@example.com")
        avis_dautrui = AvisProduit.objects.create(produit=produit, auteur=autre_auteur, note=2, commentaire="x")
        token = self._creer_token(admin_sans_droit)

        response = self.client.delete(
            '/produits/avis/supprimer/', data=json.dumps({'id': avis_dautrui.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_supprimerAvis_refuse_sans_token(self):
        response = self.client.delete('/produits/avis/supprimer/', data=json.dumps({'id': 1}), content_type='application/json')
        self.assertEqual(response.status_code, 401)

    def test_supprimerAvis_introuvable(self):
        acheteur = self._acheteur("sup_avis_introuvable@example.com")
        token = self._creer_token(acheteur)
        response = self.client.delete(
            '/produits/avis/supprimer/', data=json.dumps({'id': 999999}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)


# ── PHOTOS DE PRODUIT (Produits/views/photoProduits.py) ───────────────────────
class TestPhotosProduit(ProduitsTestCase):

    def _creer_produit(self, vendeur=None, categorie=None):
        if vendeur is None:
            vendeur, categorie = self._creer_vendeur_pret()
        elif categorie is None:
            categorie = self._creer_categorie()
        return Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Tomate", region="Ouest")

    def test_ajouterPhotosProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/photos/ajouter/',
            data={'produit_id': produit.id, 'photos': [_image_upload("a.png"), _image_upload("b.png")]},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        self.assertEqual(len(data['photos']), 2)
        self.assertEqual(photo_produits.objects.filter(produits=produit).count(), 2)
        # numérotées à la suite (ordre_depart = 1, pas 0), voir commentaire de la vue
        ordres = sorted(p.ordre for p in photo_produits.objects.filter(produits=produit))
        self.assertEqual(ordres, [1, 2])

    def test_ajouterPhotosProduit_continue_apres_des_photos_existantes(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        photo_produits.objects.create(produits=produit, url_photo=_image_upload("premiere.png"), ordre=5)
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/photos/ajouter/',
            data={'produit_id': produit.id, 'photos': [_image_upload("c.png")]},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        nouvelle = photo_produits.objects.exclude(ordre=5).get(produits=produit)
        self.assertEqual(nouvelle.ordre, 6)

    def test_ajouterPhotosProduit_refuse_produit_dun_autre_vendeur(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        autre_vendeur, _ = self._creer_vendeur_pret(email="autre_vendeur@example.com")
        token = self._creer_token(autre_vendeur)

        response = self.client.post(
            '/produits/photos/ajouter/',
            data={'produit_id': produit.id, 'photos': [_image_upload()]},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_ajouterPhotosProduit_refuse_sans_fichier(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/photos/ajouter/',
            data={'produit_id': produit.id},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_ajouterPhotosProduit_refuse_fichier_non_image(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(vendeur)
        faux_fichier = SimpleUploadedFile("x.png", b"pas une image", content_type='image/png')

        response = self.client.post(
            '/produits/photos/ajouter/',
            data={'produit_id': produit.id, 'photos': [faux_fichier]},
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(photo_produits.objects.filter(produits=produit).count(), 0)

    def test_ajouterPhotosProduit_refuse_sans_token(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        response = self.client.post(
            '/produits/photos/ajouter/', data={'produit_id': produit.id, 'photos': [_image_upload()]},
        )
        self.assertEqual(response.status_code, 401)

    def test_listerPhotosProduit_public(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        photo_produits.objects.create(produits=produit, url_photo=_image_upload(), ordre=0)

        response = self.client.get(f'/produits/photos/lister/?produit_id={produit.id}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['photos']), 1)

    def test_listerPhotosProduit_refuse_sans_produit_id(self):
        response = self.client.get('/produits/photos/lister/')
        self.assertEqual(response.status_code, 400)

    def test_reordonnerPhotosProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        p1 = photo_produits.objects.create(produits=produit, url_photo=_image_upload("1.png"), ordre=0)
        p2 = photo_produits.objects.create(produits=produit, url_photo=_image_upload("2.png"), ordre=1)
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/photos/reordonner/',
            data=json.dumps({'produit_id': produit.id, 'ordre': [p2.id, p1.id]}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p2.ordre, 0)
        self.assertEqual(p1.ordre, 1)

    def test_reordonnerPhotosProduit_refuse_liste_incoherente(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        p1 = photo_produits.objects.create(produits=produit, url_photo=_image_upload(), ordre=0)
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/photos/reordonner/',
            data=json.dumps({'produit_id': produit.id, 'ordre': [p1.id, 99999]}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_reordonnerPhotosProduit_refuse_champs_manquants(self):
        vendeur, categorie = self._creer_vendeur_pret()
        token = self._creer_token(vendeur)
        response = self.client.put(
            '/produits/photos/reordonner/',
            data=json.dumps({'produit_id': None, 'ordre': []}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_supprimerPhotoProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        photo = photo_produits.objects.create(produits=produit, url_photo=_image_upload(), ordre=0)
        token = self._creer_token(vendeur)

        response = self.client.delete(
            '/produits/photos/supprimer/',
            data=json.dumps({'id': photo.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(photo_produits.objects.filter(id=photo.id).exists())

    def test_supprimerPhotoProduit_refuse_photo_dun_autre_vendeur(self):
        vendeur, categorie = self._creer_vendeur_pret()
        produit = self._creer_produit(vendeur, categorie)
        photo = photo_produits.objects.create(produits=produit, url_photo=_image_upload(), ordre=0)
        autre_vendeur, _ = self._creer_vendeur_pret(email="autre_vendeur2@example.com")
        token = self._creer_token(autre_vendeur)

        response = self.client.delete(
            '/produits/photos/supprimer/',
            data=json.dumps({'id': photo.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(photo_produits.objects.filter(id=photo.id).exists())

    def test_supprimerPhotoProduit_refuse_champ_manquant(self):
        vendeur, _ = self._creer_vendeur_pret()
        token = self._creer_token(vendeur)
        response = self.client.delete(
            '/produits/photos/supprimer/', data=json.dumps({}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_ajouterPhotosProduit_refuse_methode_get(self):
        response = self.client.get('/produits/photos/ajouter/')
        self.assertEqual(response.status_code, 405)


# ── STATISTIQUES DE VUES (Produits/views/vuesViews.py) ────────────────────────
class TestStatistiquesVues(ProduitsTestCase):

    def _creer_produit(self, vendeur, categorie):
        return Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Mangue", region="Ouest")

    def test_statistiquesVuesVendeur_refuse_sans_token(self):
        response = self.client.get('/produits/vendeur/statistiques-vues/')
        self.assertEqual(response.status_code, 401)

    def test_statistiquesVuesVendeur_refuse_pour_un_acheteur(self):
        acheteur = Utilisateur.objects.create(
            nom="A", prenom="B", email="acheteur_vues@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000006",
        )
        acheteur.profil.role = 'acheteur'
        acheteur.profil.save()
        token = self._creer_token(acheteur)

        response = self.client.get('/produits/vendeur/statistiques-vues/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_statistiquesVuesVendeur_periode_par_defaut(self):
        vendeur, categorie = self._creer_vendeur_pret(email="vendeur_vues@example.com")
        produit = self._creer_produit(vendeur, categorie)
        visiteur = Utilisateur.objects.create(
            nom="Vis", prenom="Iteur", email="visiteur@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000007",
        )
        VueProduit.objects.create(produit=produit, visiteur=visiteur)
        VueProfilVendeur.objects.create(vendeur=vendeur, visiteur=visiteur)
        token = self._creer_token(vendeur)

        response = self.client.get('/produits/vendeur/statistiques-vues/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['profil']['total'], 1)
        self.assertEqual(len(data['profil']['visiteurs']), 1)
        self.assertEqual(data['produits_plus_consultes'][0]['label'], 'Mangue')
        # 7 jours par défaut (aujourd'hui inclus)
        self.assertEqual(len(data['profil']['serie_temporelle']), 7)

    def test_statistiquesVuesVendeur_refuse_dates_incoherentes(self):
        vendeur, _ = self._creer_vendeur_pret(email="vendeur_vues2@example.com")
        token = self._creer_token(vendeur)

        response = self.client.get(
            '/produits/vendeur/statistiques-vues/?date_debut=2026-01-10&date_fin=2026-01-01',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)['error_code'], 'INVALID_DATE')

    def test_statistiquesVuesVendeur_refuse_une_seule_date(self):
        vendeur, _ = self._creer_vendeur_pret(email="vendeur_vues3@example.com")
        token = self._creer_token(vendeur)
        response = self.client.get(
            '/produits/vendeur/statistiques-vues/?date_debut=2026-01-01',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_statistiquesVuesVendeur_refuse_date_future(self):
        vendeur, _ = self._creer_vendeur_pret(email="vendeur_vues4@example.com")
        token = self._creer_token(vendeur)
        response = self.client.get(
            '/produits/vendeur/statistiques-vues/?date_debut=2099-01-01&date_fin=2099-01-02',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_statistiquesVuesVendeur_refuse_format_de_date_invalide(self):
        vendeur, _ = self._creer_vendeur_pret(email="vendeur_vues5@example.com")
        token = self._creer_token(vendeur)
        response = self.client.get(
            '/produits/vendeur/statistiques-vues/?date_debut=01-01-2026&date_fin=2026-01-02',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_statistiquesVuesAdmin_refuse_pour_un_vendeur(self):
        vendeur, _ = self._creer_vendeur_pret(email="vendeur_vues6@example.com")
        token = self._creer_token(vendeur)
        response = self.client.get('/Registration/admin/statistiques-vues/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_statistiquesVuesAdmin_succes_vue_globale(self):
        admin = self._creer_admin("admin_vues@example.com")
        vendeur, categorie = self._creer_vendeur_pret(email="vendeur_vues7@example.com")
        produit = self._creer_produit(vendeur, categorie)
        VueProduit.objects.create(produit=produit, visiteur=None)
        VueProfilVendeur.objects.create(vendeur=vendeur, visiteur=None)
        token = self._creer_token(admin)

        response = self.client.get('/Registration/admin/statistiques-vues/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['profil']['total'], 1)
        self.assertIn('top_vendeurs', data['profil'])
        self.assertNotIn('visiteurs', data['profil'])
        self.assertEqual(data['categories_plus_consultees'][0]['label'], categorie.nom)


# ── PRODUITS — reste de produitsViews.py (lister/détail/modifier/vendeurs) ────
from django.test import override_settings  # noqa: E402  (import groupé ici, utilisé seulement par cette section)


class TestListerDetailMesProduits(ProduitsTestCase):

    def _creer_produit(self, vendeur, categorie, **kwargs):
        valeurs = dict(vendeur=vendeur, categorie=categorie, nom="Banane", region="Ouest", est_disponible=True)
        valeurs.update(kwargs)
        return Produits.objects.create(**valeurs)

    def test_listerProduits_filtre_par_categorie_et_disponibilite(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_lister1@example.com")
        autre_categorie = self._creer_categorie("Autre")
        p1 = self._creer_produit(vendeur, categorie, est_disponible=True)
        self._creer_produit(vendeur, autre_categorie, est_disponible=False)

        response = self.client.get(f'/produits/lister/?categorie_id={categorie.id}&disponible=true')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual([p['id'] for p in data['produits']], [p1.id])

    def test_listerProduits_filtre_exclure_id(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_lister2@example.com")
        p1 = self._creer_produit(vendeur, categorie)
        p2 = self._creer_produit(vendeur, categorie)

        response = self.client.get(f'/produits/lister/?exclure_id={p1.id}')
        data = json.loads(response.content)
        ids = [p['id'] for p in data['produits']]
        self.assertNotIn(p1.id, ids)
        self.assertIn(p2.id, ids)

    def test_listerProduits_refuse_methode_post(self):
        response = self.client.post('/produits/lister/')
        self.assertEqual(response.status_code, 405)

    def test_detailProduit_incremente_les_vues_et_journalise(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_detail1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        self.assertEqual(produit.nombre_vues, 0)

        response = self.client.get(f'/produits/detail/?id={produit.id}')
        self.assertEqual(response.status_code, 200)
        produit.refresh_from_db()
        self.assertEqual(produit.nombre_vues, 1)
        self.assertEqual(VueProduit.objects.filter(produit=produit).count(), 1)

    def test_detailProduit_refuse_sans_id(self):
        response = self.client.get('/produits/detail/')
        self.assertEqual(response.status_code, 400)

    def test_detailProduit_introuvable(self):
        response = self.client.get('/produits/detail/?id=999999')
        self.assertEqual(response.status_code, 404)

    def test_detailProduit_refuse_pour_visiteur_bloque(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_detail2@example.com")
        produit = self._creer_produit(vendeur, categorie)
        bloque = Utilisateur.objects.create(
            nom="B", prenom="Q", email="bloque_detail@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000010",
        )
        bloque.est_bloquer = True
        bloque.save()
        token = self._creer_token(bloque)

        response = self.client.get(f'/produits/detail/?id={produit.id}', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(json.loads(response.content)['error_code'], 'COMPTE_BLOQUE')

    def test_mesProduits_scope_au_vendeur_connecte(self):
        vendeur1, categorie = self._creer_vendeur_pret(email="v_mes1@example.com")
        vendeur2, _ = self._creer_vendeur_pret(email="v_mes2@example.com", categorie=categorie)
        p1 = self._creer_produit(vendeur1, categorie)
        self._creer_produit(vendeur2, categorie)
        token = self._creer_token(vendeur1)

        response = self.client.get('/produits/mes-produits/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual([p['id'] for p in data['produits']], [p1.id])

    def test_mesProduits_refuse_sans_token(self):
        response = self.client.get('/produits/mes-produits/')
        self.assertEqual(response.status_code, 401)


class TestCreerProduitLocalisationEtSuspensions(ProduitsTestCase):

    def test_creerProduit_reprend_la_localisation_du_profil(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_local1@example.com")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Fruits")
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({'nom': 'Banane', 'categorie_id': categorie.id, 'sous_categorie_id': sous_categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content)
        # départment/commune du profil vendeur (voir _creer_vendeur_pret) repris tels quels
        self.assertEqual(data['produit']['departement'], 'Ouest')
        self.assertEqual(data['produit']['commune'], 'Port-au-Prince')

    def test_creerProduit_refuse_categorie_non_choisie(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_local2@example.com")
        autre_categorie = self._creer_categorie("Non choisie")
        sous_categorie = sousCategories.objects.create(categorie=autre_categorie, nom="X")
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({'nom': 'Riz', 'categorie_id': autre_categorie.id, 'sous_categorie_id': sous_categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_creerProduit_refuse_unitePrix_invalide(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_local3@example.com")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Y")
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({
                'nom': 'Riz', 'categorie_id': categorie.id, 'sous_categorie_id': sous_categorie.id,
                'unitePrix': 'EUR',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_creerProduit_refuse_suspendu_par_signalements(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_local4@example.com")
        sous_categorie = sousCategories.objects.create(categorie=categorie, nom="Z")
        vendeur.desactive_par_signalements = True
        vendeur.save()
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({'nom': 'Riz', 'categorie_id': categorie.id, 'sous_categorie_id': sous_categorie.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_creerProduit_refuse_sans_categorie_choisie_du_tout(self):
        vendeur = Utilisateur.objects.create(
            nom="V", prenom="Sans", email="v_sans_choix@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000011",
        )
        vendeur.profil.role = 'vendeur'
        vendeur.profil.save()
        token = self._creer_token(vendeur)

        response = self.client.post(
            '/produits/creer/',
            data=json.dumps({'nom': 'Riz', 'categorie_id': 1, 'sous_categorie_id': 1}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)


class TestModifierToggleSupprimerProduit(ProduitsTestCase):

    def _creer_produit(self, vendeur, categorie, **kwargs):
        valeurs = dict(vendeur=vendeur, categorie=categorie, nom="Igname", region="Ouest")
        valeurs.update(kwargs)
        return Produits.objects.create(**valeurs)

    def test_modifierProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_mod1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/modifier/',
            data=json.dumps({'id': produit.id, 'nom': 'Igname modifiée', 'prix': 75}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['produit']['nom'], 'Igname modifiée')

    def test_modifierProduit_refuse_produit_dun_autre_vendeur(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_mod2@example.com")
        produit = self._creer_produit(vendeur, categorie)
        autre, _ = self._creer_vendeur_pret(email="v_mod3@example.com")
        token = self._creer_token(autre)

        response = self.client.put(
            '/produits/modifier/',
            data=json.dumps({'id': produit.id, 'nom': 'Piraté'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_modifierProduit_refuse_sous_categorie_incoherente(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_mod4@example.com")
        produit = self._creer_produit(vendeur, categorie)
        autre_categorie = self._creer_categorie("Autre mod")
        sous_categorie_autre = sousCategories.objects.create(categorie=autre_categorie, nom="Sous")
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/modifier/',
            data=json.dumps({'id': produit.id, 'sous_categorie_id': sous_categorie_autre.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_modifierProduit_refuse_reactivation_si_desactive_par_signalements(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_mod5@example.com")
        produit = self._creer_produit(vendeur, categorie, desactive_par_signalements=True, est_disponible=False)
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/modifier/',
            data=json.dumps({'id': produit.id, 'est_disponible': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_modifierProduit_refuse_reactivation_si_compte_suspendu(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_mod6@example.com")
        produit = self._creer_produit(vendeur, categorie, est_disponible=False)
        vendeur.desactive_par_signalements = True
        vendeur.save()
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/modifier/',
            data=json.dumps({'id': produit.id, 'est_disponible': True}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_toggleDisponibiliteProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_tog1@example.com")
        produit = self._creer_produit(vendeur, categorie, est_disponible=False)
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/toggle-disponibilite/',
            data=json.dumps({'id': produit.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        produit.refresh_from_db()
        self.assertTrue(produit.est_disponible)

    def test_toggleDisponibiliteProduit_refuse_si_desactive_par_signalements(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_tog2@example.com")
        produit = self._creer_produit(vendeur, categorie, desactive_par_signalements=True, est_disponible=False)
        token = self._creer_token(vendeur)

        response = self.client.put(
            '/produits/toggle-disponibilite/',
            data=json.dumps({'id': produit.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_supprimerProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_sup1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        photo_produits.objects.create(produits=produit, url_photo=_image_upload(), ordre=0)
        token = self._creer_token(vendeur)

        response = self.client.delete(
            '/produits/supprimer/', data=json.dumps({'id': produit.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Produits.objects.filter(id=produit.id).exists())

    def test_supprimerProduit_refuse_produit_dun_autre_vendeur(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_sup2@example.com")
        produit = self._creer_produit(vendeur, categorie)
        autre, _ = self._creer_vendeur_pret(email="v_sup3@example.com")
        token = self._creer_token(autre)

        response = self.client.delete(
            '/produits/supprimer/', data=json.dumps({'id': produit.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Produits.objects.filter(id=produit.id).exists())


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TestAdminActionsProduit(ProduitsTestCase):
    """reactiverProduitAdmin / desactiverProduitAdmin / supprimerProduitAdmin —
    même précaution EMAIL_BACKEND=locmem que Registration/tests.py (ces trois
    vues envoient un email de notification au vendeur, voir
    notification_service.envoyer_email_decision)."""

    def _creer_produit(self, vendeur, categorie, **kwargs):
        valeurs = dict(vendeur=vendeur, categorie=categorie, nom="Mil", region="Ouest")
        valeurs.update(kwargs)
        return Produits.objects.create(**valeurs)

    def test_reactiverProduitAdmin_succes(self):
        admin = self._creer_admin("admin_react1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_admin1@example.com")
        produit = self._creer_produit(vendeur, categorie, desactive_par_signalements=True, est_disponible=False)
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/admin/reactiver/', data=json.dumps({'id': produit.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        produit.refresh_from_db()
        self.assertTrue(produit.est_disponible)
        self.assertFalse(produit.desactive_par_signalements)

    def test_reactiverProduitAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_react2@example.com")
        vendeur, categorie = self._creer_vendeur_pret(email="v_admin2@example.com")
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/admin/reactiver/', data=json.dumps({'id': produit.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_reactiverProduitAdmin_refuse_vendeur_bloque(self):
        admin = self._creer_admin("admin_react3@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_admin3@example.com")
        vendeur.est_bloquer = True
        vendeur.save()
        produit = self._creer_produit(vendeur, categorie, desactive_par_signalements=True, est_disponible=False)
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/admin/reactiver/', data=json.dumps({'id': produit.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 409)

    def test_desactiverProduitAdmin_succes(self):
        admin = self._creer_admin("admin_desac1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_admin4@example.com")
        produit = self._creer_produit(vendeur, categorie, est_disponible=True)
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/admin/desactiver/',
            data=json.dumps({'id': produit.id, 'raison': 'Produit non conforme'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        produit.refresh_from_db()
        self.assertFalse(produit.est_disponible)

    def test_desactiverProduitAdmin_refuse_sans_raison(self):
        admin = self._creer_admin("admin_desac2@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_admin5@example.com")
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(admin)

        response = self.client.put(
            '/produits/admin/desactiver/', data=json.dumps({'id': produit.id, 'raison': '  '}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_supprimerProduitAdmin_succes(self):
        admin = self._creer_admin("admin_del1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_admin6@example.com")
        produit = self._creer_produit(vendeur, categorie)
        photo_produits.objects.create(produits=produit, url_photo=_image_upload(), ordre=0)
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/admin/supprimer/',
            data=json.dumps({'id': produit.id, 'raison': 'Contenu frauduleux'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Produits.objects.filter(id=produit.id).exists())

    def test_supprimerProduitAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_del2@example.com")
        vendeur, categorie = self._creer_vendeur_pret(email="v_admin7@example.com")
        produit = self._creer_produit(vendeur, categorie)
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/admin/supprimer/',
            data=json.dumps({'id': produit.id, 'raison': 'x'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)


class TestContactsEtVendeursPublics(ProduitsTestCase):

    def _creer_produit(self, vendeur, categorie, **kwargs):
        valeurs = dict(vendeur=vendeur, categorie=categorie, nom="Cafe", region="Ouest", est_disponible=True)
        valeurs.update(kwargs)
        return Produits.objects.create(**valeurs)

    def _acheteur(self, email):
        u = Utilisateur.objects.create(
            nom="A", prenom="B", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000012",
        )
        u.profil.role = 'acheteur'
        u.profil.save()
        return u

    def test_contacterProduit_succes(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_contact1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        acheteur = self._acheteur("acheteur_contact1@example.com")
        token = self._creer_token(acheteur)

        response = self.client.post(
            '/produits/contacter/', data=json.dumps({'id': produit.id}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['nombre_contacts'], 1)

    def test_contacterProduit_refuse_sans_token(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_contact2@example.com")
        produit = self._creer_produit(vendeur, categorie)
        response = self.client.post(
            '/produits/contacter/', data=json.dumps({'id': produit.id}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_historiqueContactsVendeur_liste_les_contacts_recus(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_contact3@example.com")
        produit = self._creer_produit(vendeur, categorie)
        acheteur = self._acheteur("acheteur_contact2@example.com")
        ContactProduit.objects.create(produit=produit, acheteur=acheteur)
        token = self._creer_token(vendeur)

        response = self.client.get('/produits/contacts/historique/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['contacts']), 1)
        self.assertEqual(data['contacts'][0]['acheteur_id'], acheteur.id)

    def test_infoVendeur_succes_et_incremente_les_vues(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_info1@example.com")
        self._creer_produit(vendeur, categorie)
        visiteur = self._acheteur("visiteur_info@example.com")
        token = self._creer_token(visiteur)

        response = self.client.get(f'/produits/vendeur/?vendeur_id={vendeur.id}', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['vendeur']['nombre_produits'], 1)
        self.assertFalse(data['vendeur']['est_entreprise'])
        vendeur.refresh_from_db()
        self.assertEqual(vendeur.nombre_vues_profil, 1)
        self.assertEqual(VueProfilVendeur.objects.filter(vendeur=vendeur).count(), 1)

    def test_infoVendeur_ne_compte_pas_sa_propre_consultation(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_info2@example.com")
        token = self._creer_token(vendeur)

        response = self.client.get(f'/produits/vendeur/?vendeur_id={vendeur.id}', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        vendeur.refresh_from_db()
        self.assertEqual(vendeur.nombre_vues_profil, 0)

    def test_infoVendeur_refuse_sans_token(self):
        vendeur, _ = self._creer_vendeur_pret(email="v_info3@example.com")
        response = self.client.get(f'/produits/vendeur/?vendeur_id={vendeur.id}')
        self.assertEqual(response.status_code, 401)

    def test_infoVendeur_introuvable(self):
        vendeur, _ = self._creer_vendeur_pret(email="v_info4@example.com")
        token = self._creer_token(vendeur)
        response = self.client.get('/produits/vendeur/?vendeur_id=999999', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 404)

    def test_listerVendeursCarte_ignore_les_vendeurs_sans_position(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_carte1@example.com")
        self._creer_produit(vendeur, categorie, est_disponible=True)
        # profil sans latitude/longitude (valeur par défaut de _creer_vendeur_pret) -> ignoré

        response = self.client.get('/produits/vendeurs-carte/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['vendeurs'], [])

    def test_listerVendeursCarte_inclut_un_vendeur_positionne(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_carte2@example.com")
        vendeur.profil.latitude = 18.5392
        vendeur.profil.longitude = -72.3364
        vendeur.profil.save()
        self._creer_produit(vendeur, categorie, est_disponible=True)

        response = self.client.get('/produits/vendeurs-carte/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data['vendeurs']), 1)
        self.assertEqual(data['vendeurs'][0]['vendeur_id'], vendeur.id)

    def test_listerVendeursPublics_inclut_tous_les_vendeurs(self):
        vendeur1, categorie = self._creer_vendeur_pret(email="v_pub1@example.com")
        vendeur2, _ = self._creer_vendeur_pret(email="v_pub2@example.com", categorie=categorie)
        # aucun produit nécessaire, contrairement à listerVendeursCarte

        response = self.client.get('/produits/vendeurs-publics/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        ids = {v['vendeur_id'] for v in data['vendeurs']}
        self.assertEqual(ids, {vendeur1.id, vendeur2.id})


class TestRapportStatistiquePdf(ProduitsTestCase):

    def test_statistiquesVendeurPdf_succes(self):
        vendeur, categorie = self._creer_vendeur_pret(email="v_pdf1@example.com")
        Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="The", region="Ouest")
        token = self._creer_token(vendeur)

        response = self.client.get('/produits/statistiques/rapport-pdf/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_statistiquesVendeurPdf_refuse_pour_un_acheteur(self):
        acheteur = Utilisateur.objects.create(
            nom="A", prenom="B", email="acheteur_pdf@example.com",
            mot_de_passe=haser_password("secret123"), telephone="0000000013",
        )
        acheteur.profil.role = 'acheteur'
        acheteur.profil.save()
        token = self._creer_token(acheteur)

        response = self.client.get('/produits/statistiques/rapport-pdf/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_statistiquesVendeurPdf_refuse_sans_token(self):
        response = self.client.get('/produits/statistiques/rapport-pdf/')
        self.assertEqual(response.status_code, 401)


# ── LISTER / HISTORIQUE / RAPPORT DES SIGNALEMENTS (produits/vendeurs/avis) ───
class TestListerEtHistoriqueSignalements(ProduitsTestCase):

    def _acheteur(self, email):
        u = Utilisateur.objects.create(
            nom="A", prenom="B", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000014",
        )
        u.profil.role = 'acheteur'
        u.profil.save()
        return u

    def _creer_produit(self, vendeur, categorie):
        return Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Igname", region="Ouest")

    # ── signalements de produits ──
    def test_listerSignalementsAdmin_succes(self):
        admin = self._creer_admin("admin_lsp1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_lsp1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        signaleur = self._acheteur("sig_lsp1@example.com")
        SignalementProduit.objects.create(
            produit=produit, signaleur=signaleur, type_probleme='autre', motif='x',
        )
        token = self._creer_token(admin)

        response = self.client.get('/produits/signalements/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)['signalements']), 1)

    def test_listerSignalementsTraites_moi_seul_par_defaut(self):
        admin1 = self._creer_admin("admin_lst1@example.com", gestion_signalements=True)
        admin2 = self._creer_admin("admin_lst2@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_lst1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        signaleur = self._acheteur("sig_lst1@example.com")
        SignalementProduit.objects.create(
            produit=produit, signaleur=signaleur, type_probleme='autre', motif='x',
            admin_traitant=admin1, date_traitement=timezone.now(), explication_decision='ok',
        )
        SignalementProduit.objects.create(
            produit=produit, signaleur=signaleur, type_probleme='autre', motif='y',
            admin_traitant=admin2, date_traitement=timezone.now(), explication_decision='ok',
        )
        token = self._creer_token(admin1)

        response = self.client.get('/produits/signalements/traites/', HTTP_AUTHORIZATION=f'Token {token}')
        data = json.loads(response.content)
        self.assertEqual(len(data['signalements']), 1)

    def test_listerSignalementsTraites_super_admin_voit_tout(self):
        super_admin = self._creer_admin("admin_lst3@example.com", super_admin=True)
        autre_admin = self._creer_admin("admin_lst4@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_lst2@example.com")
        produit = self._creer_produit(vendeur, categorie)
        signaleur = self._acheteur("sig_lst2@example.com")
        SignalementProduit.objects.create(
            produit=produit, signaleur=signaleur, type_probleme='autre', motif='x',
            admin_traitant=autre_admin, date_traitement=timezone.now(), explication_decision='ok',
        )
        token = self._creer_token(super_admin)

        response = self.client.get('/produits/signalements/traites/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(len(json.loads(response.content)['signalements']), 1)

    def test_supprimerHistoriqueSignalements_masque_sans_supprimer(self):
        admin = self._creer_admin("admin_shs1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_shs1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        signaleur = self._acheteur("sig_shs1@example.com")
        signalement = SignalementProduit.objects.create(
            produit=produit, signaleur=signaleur, type_probleme='autre', motif='x',
            admin_traitant=admin, date_traitement=timezone.now(), explication_decision='ok',
        )
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/signalements/traites/supprimer/',
            data=json.dumps({'ids': [signalement.id]}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['nombre_supprime'], 1)
        self.assertTrue(SignalementProduit.objects.filter(id=signalement.id).exists())

    def test_supprimerHistoriqueSignalements_refuse_ids_manquants(self):
        admin = self._creer_admin("admin_shs2@example.com", gestion_signalements=True)
        token = self._creer_token(admin)
        response = self.client.delete(
            '/produits/signalements/traites/supprimer/', data=json.dumps({}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    # ── signalements de vendeurs ──
    def test_listerSignalementsVendeursAdmin_succes(self):
        admin = self._creer_admin("admin_lsv1@example.com", gestion_signalements=True)
        vendeur, _ = self._creer_vendeur_pret(email="v_lsv1@example.com")
        signaleur = self._acheteur("sig_lsv1@example.com")
        SignalementVendeur.objects.create(vendeur=vendeur, signaleur=signaleur, type_probleme='autre', motif='x')
        token = self._creer_token(admin)

        response = self.client.get('/produits/signalements-vendeurs/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)['signalements']), 1)

    def test_listerSignalementsVendeursAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_lsv2@example.com")
        token = self._creer_token(admin)
        response = self.client.get('/produits/signalements-vendeurs/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_listerSignalementsVendeursTraites_moi_seul_par_defaut(self):
        admin1 = self._creer_admin("admin_lsv3@example.com", gestion_signalements=True)
        admin2 = self._creer_admin("admin_lsv4@example.com", gestion_signalements=True)
        vendeur, _ = self._creer_vendeur_pret(email="v_lsv2@example.com")
        signaleur = self._acheteur("sig_lsv2@example.com")
        SignalementVendeur.objects.create(
            vendeur=vendeur, signaleur=signaleur, type_probleme='autre', motif='x',
            admin_traitant=admin2, date_traitement=timezone.now(), explication_decision='ok',
        )
        token = self._creer_token(admin1)

        response = self.client.get('/produits/signalements-vendeurs/traites/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(json.loads(response.content)['signalements'], [])

    def test_supprimerHistoriqueSignalementsVendeurs_masque_sans_supprimer(self):
        admin = self._creer_admin("admin_shsv1@example.com", gestion_signalements=True)
        vendeur, _ = self._creer_vendeur_pret(email="v_shsv1@example.com")
        signaleur = self._acheteur("sig_shsv1@example.com")
        signalement = SignalementVendeur.objects.create(
            vendeur=vendeur, signaleur=signaleur, type_probleme='autre', motif='x',
            admin_traitant=admin, date_traitement=timezone.now(), explication_decision='ok',
        )
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/signalements-vendeurs/traites/supprimer/',
            data=json.dumps({'ids': [signalement.id]}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SignalementVendeur.objects.filter(id=signalement.id).exists())

    # ── signalements d'avis ──
    def test_listerSignalementsAvisAdmin_succes(self):
        admin = self._creer_admin("admin_lsa1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_lsa1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        auteur_avis = self._acheteur("auteur_lsa1@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur_avis, note=1, commentaire="Mauvais")
        signaleur = self._acheteur("sig_lsa1@example.com")
        SignalementAvis.objects.create(
            avis=avis, signaleur=signaleur, type_probleme='faux_avis', motif='x',
            avis_commentaire_snapshot=avis.commentaire, avis_note_snapshot=avis.note,
            produit_nom_snapshot=produit.nom,
        )
        token = self._creer_token(admin)

        response = self.client.get('/produits/avis/signalements/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)['signalements']), 1)

    def test_listerSignalementsAvisAdmin_refuse_sans_droit(self):
        admin = self._creer_admin("admin_lsa2@example.com")
        token = self._creer_token(admin)
        response = self.client.get('/produits/avis/signalements/en-attente/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 403)

    def test_listerSignalementsAvisTraites_super_admin_voit_tout(self):
        super_admin = self._creer_admin("admin_lsa3@example.com", super_admin=True)
        autre_admin = self._creer_admin("admin_lsa4@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_lsa2@example.com")
        produit = self._creer_produit(vendeur, categorie)
        auteur_avis = self._acheteur("auteur_lsa2@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur_avis, note=2, commentaire="Bof")
        signaleur = self._acheteur("sig_lsa2@example.com")
        SignalementAvis.objects.create(
            avis=avis, signaleur=signaleur, type_probleme='autre', motif='x',
            avis_commentaire_snapshot=avis.commentaire, avis_note_snapshot=avis.note,
            produit_nom_snapshot=produit.nom,
            admin_traitant=autre_admin, date_traitement=timezone.now(), explication_decision='ok',
        )
        token = self._creer_token(super_admin)

        response = self.client.get('/produits/avis/signalements/traites/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(len(json.loads(response.content)['signalements']), 1)

    def test_supprimerHistoriqueSignalementsAvis_masque_sans_supprimer(self):
        admin = self._creer_admin("admin_shsa1@example.com", gestion_signalements=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_shsa1@example.com")
        produit = self._creer_produit(vendeur, categorie)
        auteur_avis = self._acheteur("auteur_shsa1@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur_avis, note=3, commentaire="Moyen")
        signaleur = self._acheteur("sig_shsa1@example.com")
        signalement = SignalementAvis.objects.create(
            avis=avis, signaleur=signaleur, type_probleme='autre', motif='x',
            avis_commentaire_snapshot=avis.commentaire, avis_note_snapshot=avis.note,
            produit_nom_snapshot=produit.nom,
            admin_traitant=admin, date_traitement=timezone.now(), explication_decision='ok',
        )
        token = self._creer_token(admin)

        response = self.client.delete(
            '/produits/avis/signalements/traites/supprimer/',
            data=json.dumps({'ids': [signalement.id]}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SignalementAvis.objects.filter(id=signalement.id).exists())


# ── RAPPORT PDF D'AUDIT DES SIGNALEMENTS (4 types confondus) ──────────────────
class TestRapportSignalements(ProduitsTestCase):

    AUJOURDHUI = timezone.localdate().isoformat()

    def _acheteur(self, email):
        u = Utilisateur.objects.create(
            nom="A", prenom="B", email=email,
            mot_de_passe=haser_password("secret123"), telephone="0000000015",
        )
        u.profil.role = 'acheteur'
        u.profil.save()
        return u

    def test_genererRapportSignalements_succes_avec_les_4_types(self):
        from Messagerie.models import Conversation, SignalementMessage

        super_admin = self._creer_admin("admin_rapsig1@example.com", super_admin=True)
        vendeur, categorie = self._creer_vendeur_pret(email="v_rapsig1@example.com")
        produit = Produits.objects.create(vendeur=vendeur, categorie=categorie, nom="Cafe", region="Ouest")
        signaleur = self._acheteur("sig_rapsig1@example.com")
        auteur_avis = self._acheteur("auteur_rapsig1@example.com")
        avis = AvisProduit.objects.create(produit=produit, auteur=auteur_avis, note=1, commentaire="Nul")

        maintenant = timezone.now()
        SignalementProduit.objects.create(
            produit=produit, signaleur=signaleur, type_probleme='autre', motif='x',
            admin_traitant=super_admin, date_traitement=maintenant, explication_decision='ok',
        )
        SignalementVendeur.objects.create(
            vendeur=vendeur, signaleur=signaleur, type_probleme='autre', motif='x',
            admin_traitant=super_admin, date_traitement=maintenant, explication_decision='ok',
        )
        SignalementAvis.objects.create(
            avis=avis, signaleur=signaleur, type_probleme='autre', motif='x',
            avis_commentaire_snapshot=avis.commentaire, avis_note_snapshot=avis.note,
            produit_nom_snapshot=produit.nom,
            admin_traitant=super_admin, date_traitement=maintenant, explication_decision='ok',
        )
        conversation = Conversation.obtenir_ou_creer(signaleur, vendeur)
        message = conversation.messages.create(expediteur=signaleur, contenu="msg", chiffre=False)
        SignalementMessage.objects.create(
            message=message, signaleur=vendeur, type_probleme='spam', motif='x',
            contenu_signale='msg',
            admin_traitant=super_admin, date_traitement=maintenant, explication_decision='ok',
        )

        token = self._creer_token(super_admin)
        response = self.client.get(
            f'/produits/signalements/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_genererRapportSignalements_succes_sans_aucune_entree(self):
        super_admin = self._creer_admin("admin_rapsig2@example.com", super_admin=True)
        token = self._creer_token(super_admin)
        response = self.client.get(
            '/produits/signalements/rapport-audit/?date_debut=2000-01-01&date_fin=2000-01-02',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_genererRapportSignalements_filtre_par_admin_introuvable(self):
        super_admin = self._creer_admin("admin_rapsig3@example.com", super_admin=True)
        token = self._creer_token(super_admin)
        response = self.client.get(
            f'/produits/signalements/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}&admin_id=999999',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 404)

    def test_genererRapportSignalements_refuse_sans_droit(self):
        admin = self._creer_admin("admin_rapsig4@example.com", gestion_signalements=True)
        token = self._creer_token(admin)
        response = self.client.get(
            f'/produits/signalements/rapport-audit/?date_debut=2020-01-01&date_fin={self.AUJOURDHUI}',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 403)

    def test_genererRapportSignalements_refuse_dates_incoherentes(self):
        super_admin = self._creer_admin("admin_rapsig5@example.com", super_admin=True)
        token = self._creer_token(super_admin)
        response = self.client.get(
            '/produits/signalements/rapport-audit/?date_debut=2020-01-10&date_fin=2020-01-01',
            HTTP_AUTHORIZATION=f'Token {token}',
        )
        self.assertEqual(response.status_code, 400)

    def test_genererRapportSignalements_refuse_sans_dates(self):
        super_admin = self._creer_admin("admin_rapsig6@example.com", super_admin=True)
        token = self._creer_token(super_admin)
        response = self.client.get('/produits/signalements/rapport-audit/', HTTP_AUTHORIZATION=f'Token {token}')
        self.assertEqual(response.status_code, 400)
