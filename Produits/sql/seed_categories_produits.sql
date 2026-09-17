-- Peuple les tables categories / sous_categories à partir du référentiel
-- src/assets/Produits/categorieProduits.json (frontend) : 1 catégorie
-- "Produits Agricoles" + ses sous-catégories standard.
-- Schéma exact (voir Produits/models/categoriesModels.py et sousCategoriesModel.py) :
--   categories (id, nom, description)
--   sous_categories (id, categorie_id FK -> categories.id, nom)
--
-- Compatible SQLite (dev) et PostgreSQL (prod). À exécuter une seule fois sur
-- une base où "Produits Agricoles" n'existe pas encore (sinon la catégorie
-- serait dupliquée — pas de contrainte UNIQUE sur categories.nom).

INSERT INTO categories (nom, description)
VALUES ('Produits Agricoles', NULL);

INSERT INTO sous_categories (categorie_id, nom)
VALUES
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Animaux vivants'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Viandes et abats'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Poissons et produits aquatiques'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Produits laitiers, oeufs et miel'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Plantes vivantes et floriculture'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Legumes, racines et tubercules'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Fruits et fruits a coque'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Cafe, the et epices'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Cereales'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Graines oleagineuses et fourrages'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Matieres premieres vegetales brutes'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Tabac'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Produits de minoterie'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Huiles et graisses'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Sucres'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Cacao et derives'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Preparations alimentaires'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Boissons'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Residus et alimentation animale'),
    ((SELECT id FROM categories WHERE nom = 'Produits Agricoles'), 'Produits forestiers');
