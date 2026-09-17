"""
Peuple la base de développement avec des données de démonstration : 10
comptes vendeurs individuels déjà vérifiés (role='vendeur' directement, sans
passer par le pipeline KYC — voir Profil.convertir_en_vendeur) et 30 produits
répartis entre eux (3 par vendeur), tous disponibles immédiatement.

Usage :
    python manage.py seed_demo_donnees
    python manage.py seed_demo_donnees --supprimer   # retire les données déjà semées, puis ressème

Ré-exécutable sans risque : les comptes sont identifiés par leur email
(domaine @demo.rekoltht.ht) et retrouvés via get_or_create plutôt que
recréés — relancer la commande n'aboutit jamais à des doublons.
"""
import random

from django.core.management.base import BaseCommand
from django.db import transaction

from Registration.models import Utilisateur, haser_password
from Produits.models import Categories, Produits, sousCategories

DOMAINE_DEMO = "demo.rekoltht.ht"
NOMBRE_VENDEURS = 10
PRODUITS_PAR_VENDEUR = 3
MOT_DE_PASSE_DEMO = "Demo1234!"

# (prénom, nom) — vendeurs individuels fictifs
IDENTITES_VENDEURS = [
    ("Jean",     "Baptiste"),
    ("Marie",    "Joseph"),
    ("Pierre",   "Louis"),
    ("Rose",     "Charles"),
    ("Jacques",  "Fleurant"),
    ("Nadège",   "Pierre-Louis"),
    ("Wilner",   "Dorsainvil"),
    ("Guerline", "Saint-Fleur"),
    ("Fritz",    "Alexandre"),
    ("Yolette",  "Casimir"),
]

# (département, commune) — un par vendeur, régions agricoles réelles d'Haïti
LOCALISATIONS = [
    ("Ouest",       "Kenscoff"),
    ("Artibonite",  "Saint-Marc"),
    ("Nord",        "Cap-Haïtien"),
    ("Sud",         "Les Cayes"),
    ("Nippes",      "Miragoâne"),
    ("Centre",      "Hinche"),
    ("Grand'Anse",  "Jérémie"),
    ("Sud-Est",     "Jacmel"),
    ("Nord-Est",    "Fort-Liberté"),
    ("Nord-Ouest",  "Port-de-Paix"),
]

# noms de produits réalistes par sous-catégorie (voir Categories/sousCategories
# déjà en base, une seule catégorie "Produits Agricoles" au moment de l'écriture
# de cette commande) — utilisés en cycle, pas besoin d'un par sous-catégorie exacte
PRODUITS_DEMO = [
    ("Mangues Francisque", "Fruits et fruits a coque"),
    ("Bananes plantain", "Fruits et fruits a coque"),
    ("Avocats", "Fruits et fruits a coque"),
    ("Maïs moulu", "Cereales"),
    ("Riz local", "Cereales"),
    ("Petit mil", "Cereales"),
    ("Choux", "Legumes/racines et tubercules"),
    ("Ignames", "Legumes/racines et tubercules"),
    ("Patates douces", "Legumes/racines et tubercules"),
    ("Carottes", "Legumes/racines et tubercules"),
    ("Miel local", "Produits laitiers/oeufs et miel"),
    ("Œufs de ferme", "Produits laitiers/oeufs et miel"),
    ("Fromage de campagne", "Produits laitiers/oeufs et miel"),
    ("Poules pondeuses", "Animaux vivants"),
    ("Cabris", "Animaux vivants"),
    ("Poissons frais", "Poissons et produits aquatiques"),
    ("Lambis", "Poissons et produits aquatiques"),
    ("Café en grain", "Matieres premieres vegetales brutes"),
    ("Cacao sec", "Matieres premieres vegetales brutes"),
    ("Semis de plantules", "Plantes vivantes et floriculture"),
]


class Command(BaseCommand):
    help = "Crée 10 comptes vendeurs de démonstration et 30 produits répartis entre eux."

    def add_arguments(self, parser):
        parser.add_argument(
            '--supprimer', action='store_true',
            help="Supprime d'abord les comptes/produits de démonstration déjà semés (email @%s) avant de ressemer." % DOMAINE_DEMO,
        )

    def handle(self, *args, **options):
        if options['supprimer']:
            self._supprimer_donnees_existantes()

        categorie = Categories.objects.filter(nom="Produits Agricoles").first()
        if not categorie:
            self.stderr.write(self.style.ERROR(
                "Catégorie 'Produits Agricoles' introuvable en base — impossible de créer des produits sans catégorie."
            ))
            return

        sous_categories_par_nom = {sc.nom: sc for sc in categorie.sous_categories.all()}

        with transaction.atomic():
            vendeurs = [self._creer_vendeur(i) for i in range(NOMBRE_VENDEURS)]
            nombre_produits_crees = self._creer_produits(vendeurs, categorie, sous_categories_par_nom)

        self.stdout.write(self.style.SUCCESS(
            f"{len(vendeurs)} vendeurs prêts, {nombre_produits_crees} produits créés."
        ))

    def _supprimer_donnees_existantes(self):
        utilisateurs_demo = Utilisateur.objects.filter(email__endswith=f"@{DOMAINE_DEMO}")
        nb_produits = Produits.objects.filter(vendeur__in=utilisateurs_demo).count()
        nb_utilisateurs = utilisateurs_demo.count()
        utilisateurs_demo.delete()  # CASCADE supprime aussi les produits liés (voir Produits.vendeur, on_delete=CASCADE)
        self.stdout.write(self.style.WARNING(
            f"Supprimé : {nb_utilisateurs} comptes de démonstration et {nb_produits} produits associés."
        ))

    def _creer_vendeur(self, index):
        prenom, nom = IDENTITES_VENDEURS[index]
        email = f"vendeur{index + 1}@{DOMAINE_DEMO}"
        departement, commune = LOCALISATIONS[index]

        utilisateur, cree = Utilisateur.objects.get_or_create(
            email=email,
            defaults={
                'nom': nom,
                'prenom': prenom,
                'mot_de_passe': haser_password(MOT_DE_PASSE_DEMO),
                'telephone': f"+509{10000000 + index}",
                'est_actif': True,
            },
        )
        # role='vendeur' directement, sans simuler tout le pipeline KYC — voir
        # Profil.convertir_en_vendeur (Registration/models.py) ; profil déjà
        # créé automatiquement par le signal post_save (Registration/signals.py)
        if utilisateur.profil.role != 'vendeur':
            utilisateur.profil.convertir_en_vendeur()
        if not utilisateur.profil.departement:
            utilisateur.profil.departement = departement
            utilisateur.profil.commune = commune
            utilisateur.profil.pays = "Haiti"
            utilisateur.profil.save()

        etat = "créé" if cree else "déjà existant"
        self.stdout.write(f"  vendeur {index + 1}/{NOMBRE_VENDEURS} — {email} ({etat})")
        return utilisateur

    def _creer_produits(self, vendeurs, categorie, sous_categories_par_nom):
        compteur = 0
        for index_vendeur, vendeur in enumerate(vendeurs):
            departement, commune = LOCALISATIONS[index_vendeur]
            for i in range(PRODUITS_PAR_VENDEUR):
                nom_produit, nom_sous_categorie = PRODUITS_DEMO[(index_vendeur * PRODUITS_PAR_VENDEUR + i) % len(PRODUITS_DEMO)]
                # évite les doublons si la commande est relancée sans --supprimer
                if Produits.objects.filter(vendeur=vendeur, nom=nom_produit).exists():
                    continue
                Produits.objects.create(
                    vendeur=vendeur,
                    categorie=categorie,
                    sous_categorie=sous_categories_par_nom.get(nom_sous_categorie),
                    nom=nom_produit,
                    description=f"{nom_produit} de qualité, vendu directement par le producteur à {commune}.",
                    prix=round(random.uniform(50, 800), 2),
                    unitePrix="HTG",
                    unite_De_Mesure=random.choice(["kg", "marmite", "unité", "douzaine"]),
                    est_disponible=True,
                    departement=departement,
                    commune=commune,
                    region=departement,
                )
                compteur += 1
        return compteur
