# ── IMPORTS ───────────────────────────────────────────────────────────────────
import re                    # extraction des dates/numéros dans le texte OCRisé
import unicodedata           # normalisation des accents pour la comparaison de labels
from datetime import date    # conversion de la date de naissance extraite

# NOTE : paddleocr/paddlepaddle sont importés à l'intérieur de _obtenir_ocr(),
# pas ici au niveau module. Ce sont des dépendances lourdes (deep learning) :
# un import au chargement du module rendrait tout le projet Django incapable
# de démarrer si elles ne sont pas installées/compatibles avec l'environnement,
# alors que seule la vérification KYC (étape 02) en a besoin.

# score de confiance PaddleOCR minimum pour accepter un champ (sinon document
# jugé illisible, voir marquer_echoue dans Registration/views.py)
SEUIL_CONFIANCE_MINIMUM = 0.70

# singleton paresseux : le modèle PaddleOCR est volumineux à charger (poids du
# réseau de neurones), il est donc initialisé une seule fois et réutilisé —
# critique pour rester sous les 5 minutes cumulées du traitement synchrone
_OCR = None

_RE_DATE   = re.compile(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})')
# un numéro de pièce peut contenir des tirets (NIF haïtien : 008-390-493-6) —
# pas seulement des caractères alphanumériques contigus
_RE_NUMERO = re.compile(r'\b([A-Z0-9][A-Z0-9\-]{4,18}[A-Z0-9])\b')

# labels par type de document — voir _chercher_valeur_liee : la mise en page
# réelle des pièces haïtiennes (constatée sur des documents réels) place la
# VALEUR sous l'étiquette (ou parfois à sa droite), jamais collée dessus, et
# l'ordre de lecture brut de PaddleOCR mélange les colonnes d'un tableau —
# d'où la recherche par position (coordonnées des boîtes) plutôt que par
# simple ordre séquentiel du texte.
_LABELS_NOM    = {'NOM', 'SIYATI'}
_LABELS_PRENOM = {'PRENOM', 'NON'}
_LABELS_NUMERO_PAR_TYPE = {
    'passeport': {'PASSEPORT', 'PASPO'},            # "Paspò nimewo / N° Passeport" (variantes OCR FR/Kreyòl)
    'cin':       {'CARTE', 'KAT', 'CIN'},           # "Numéro de carte / Nimewo kat la"
    'permis':    {'NIF'},                           # identifiant retenu pour le permis (voir consigne produit)
}
_LABELS_NUMERO_PATENTE = {'PATENTE'}                # "Numéro de Patente"
_LABELS_ENTREPRISE     = {'DELIVREA', 'DELIVRE'}    # "Délivré à" (nom de l'entreprise sur le certificat)


def _obtenir_ocr():
    """Initialise PaddleOCR une seule fois (coûteux) et réutilise l'instance."""
    global _OCR
    if _OCR is None:
        from paddleocr import PaddleOCR   # import différé, voir NOTE en tête de fichier
        _OCR = PaddleOCR(lang='fr', use_angle_cls=True)
    return _OCR


def _sans_accents(texte):
    """Retire les accents (é→e, à→a...) pour comparer des labels de façon fiable."""
    return ''.join(c for c in unicodedata.normalize('NFD', texte) if unicodedata.category(c) != 'Mn')


def _normalise(texte):
    """Sans accents, en majuscules — base de toutes les comparaisons de libellés."""
    return _sans_accents(texte).upper()


def _label_present(texte, labels):
    """
    Vrai si un des libellés apparaît en SOUS-CHAÎNE du texte normalisé —
    délibérément plus tolérant qu'une correspondance de token exact, car
    l'OCR fusionne parfois deux mots adjacents (ex: "Siyati/Nom" lu comme
    "SiyatilNom", le "/" confondu avec un "l" : aucun séparateur ne subsiste
    pour isoler "NOM" comme token), ou ajoute un pluriel ("Prénoms") — les
    deux cas constatés sur de vraies pièces. Une correspondance de sous-
    chaîne retrouve "NOM"/"SIYATI"/"PRENOM" dans ces deux cas sans effort
    supplémentaire.
    """
    normalise = _normalise(texte)
    return any(label in normalise for label in labels)


def _est_un_label(ligne):
    """Vrai si la ligne correspond à une étiquette connue — sert à ne jamais
    retourner un autre label comme si c'était une valeur (voir _chercher_valeur_liee)."""
    tous_labels = _LABELS_NOM | _LABELS_PRENOM | _LABELS_NUMERO_PATENTE | _LABELS_ENTREPRISE
    for labels in _LABELS_NUMERO_PAR_TYPE.values():
        tous_labels = tous_labels | labels
    return _label_present(ligne, tous_labels)


def _centre_boite(boite):
    """Coordonnées (x, y) du centre d'une boîte OCR (4 points [x,y])."""
    xs = [p[0] for p in boite]
    ys = [p[1] for p in boite]
    return sum(xs) / 4, sum(ys) / 4


def _valeur_dans_meme_boite(texte, labels):
    """
    Certains documents n'ont aucun espace entre le libellé et sa valeur, et
    PaddleOCR fusionne alors les deux dans une seule boîte détectée (constaté
    en conditions réelles sur un certificat de patente : une seule détection
    "Numero de Patente590712200" au lieu de deux boîtes séparées) — chercher
    une boîte voisine dans ce cas renvoie une valeur sans rapport (le bloc de
    texte suivant). On tente donc d'abord d'extraire ce qui suit le libellé
    DANS le même texte, avant de chercher ailleurs.
    """
    normalise = _normalise(texte)
    for label in labels:
        idx = normalise.find(label)
        if idx == -1:
            continue
        fin = idx + len(label)
        # le libellé doit être un mot complet, pas le préfixe d'un mot plus
        # long (ex: "PRENOM" ne doit pas matcher dans "PRENOMS" et renvoyer
        # juste le "S" final comme si c'était une valeur, constaté en
        # conditions réelles) — sauf s'il est immédiatement suivi d'un
        # chiffre, cas réel d'une valeur fusionnée ("Patente590712200")
        if fin < len(normalise) and normalise[fin].isalpha():
            continue
        # collé sans espace au libellé (valeur vraiment fusionnée, ex.
        # "Patente590712200") — sinon, un espace suivi de simples mots du
        # même libellé (ex. "Nimewo kat la", "la" étant l'article créole, pas
        # une valeur) ne doit être accepté que s'il ressemble à une vraie
        # valeur (au moins un chiffre) — constaté en conditions réelles
        colle = fin >= len(normalise) or normalise[fin] not in ' \t'
        reste = texte[fin:].strip(" :./-")
        if not reste:
            continue
        if not colle and not any(c.isdigit() for c in reste):
            continue
        return reste
    return None


def _chercher_valeur_liee(detections, labels):
    """
    Cherche une détection dont le texte contient un des tokens de `labels`,
    puis retourne la valeur associée — la valeur associée est presque
    toujours juste en dessous (mise en page en tableau) ou juste à droite
    (mise en page en ligne) de son étiquette, ou parfois fusionnée dans la
    même boîte (voir _valeur_dans_meme_boite).

    Priorité 1 sur TOUTES les boîtes portant le libellé (pas seulement la
    première) : si l'une contient la valeur fusionnée dans le même texte, on
    la prend — sinon un fragment de titre contenant aussi le mot du libellé
    (ex: "DE PATENTE" dans un en-tête, avant la vraie boîte "Numero de
    Patente590712200" plus bas) gagnerait à tort la recherche par simple
    ordre de détection, et sa recherche de boîte voisine (priorité 2)
    renverrait une valeur sans rapport — constaté en conditions réelles sur
    un certificat de patente.
    """
    candidats = [d for d in detections if _label_present(d['texte'], labels)]

    # cas particulier : "NOM" est une sous-chaîne de "PRENOM" (ex: "Prenom/Non"
    # matcherait à tort la recherche du NOM de famille et gagnerait la boîte
    # correspondant en fait au prénom, avant la vraie boîte "Nom/Siyati" —
    # constaté en conditions réelles) — une boîte qui porte aussi le libellé
    # PRENOM n'est jamais une boîte NOM légitime sur les pièces observées.
    if labels is _LABELS_NOM:
        candidats = [d for d in candidats if not _label_present(d['texte'], _LABELS_PRENOM)]

    for detection in candidats:
        meme_boite = _valeur_dans_meme_boite(detection['texte'], labels)
        if meme_boite and not _est_un_label(meme_boite):
            return meme_boite.strip(" :.-")

    # priorité 2 : repli sur la boîte voisine la plus proche du premier
    # libellé trouvé (mise en page en tableau/ligne)
    for detection in candidats:
        lx, ly = detection['cx'], detection['cy']
        meilleur, meilleure_distance = None, None
        for autre in detections:
            if autre is detection or _est_un_label(autre['texte']):
                continue
            dx, dy = autre['cx'] - lx, autre['cy'] - ly
            # la valeur est en dessous (dy > 0) ou sur la même ligne à droite
            # (dy proche de 0, dx > 0) — jamais au-dessus ni loin à gauche
            if dy < -10 or (abs(dy) < 15 and dx < -10):
                continue
            distance = (dy if dy > 15 else abs(dy) * 0.3) + abs(dx) * 0.15
            if meilleure_distance is None or distance < meilleure_distance:
                meilleur, meilleure_distance = autre['texte'], distance
        if meilleur:
            return meilleur.strip(" :.-")
    return None


def _chercher_date_naissance(lignes):
    """Retourne la première date au format JJ/MM/AAAA (ou -/.) trouvée dans le texte."""
    for ligne in lignes:
        trouve = _RE_DATE.search(ligne)
        if trouve:
            return trouve.group(0)
    return None


def _nettoyer_numero(valeur):
    """
    Extrait le motif ressemblant à un numéro de pièce dans une valeur OCRisée
    brute. Préfère un motif contenant au moins un chiffre — un vrai numéro en
    a toujours un, alors qu'un fragment de libellé purement alphabétique
    laissé dans la valeur (ex: "NUMERO" avant le vrai numéro dans "Numero de
    Patentc:8707001535", le libellé "Patente" ayant lui-même été mal lu par
    l'OCR) matcherait sinon en premier et masquerait le vrai numéro plus loin
    dans la même chaîne — constaté en conditions réelles.
    """
    if not valeur:
        return None
    candidats = _RE_NUMERO.findall(valeur.upper())
    avec_chiffre = [c for c in candidats if any(ch.isdigit() for ch in c)]
    if avec_chiffre:
        return avec_chiffre[0]
    return candidats[0] if candidats else valeur.strip() or None


def extraire_infos_piece(chemin_image, type_document):
    """
    Lance l'OCR sur une image de pièce d'identité ou de document d'entreprise
    et tente d'en extraire les champs structurés (nom, prénom, numéro de pièce,
    nom d'entreprise, date de naissance) en s'appuyant sur la POSITION des
    boîtes détectées, pas seulement l'ordre séquentiel du texte (voir
    _chercher_valeur_liee) — nécessaire car les pièces réelles sont mises en
    page en tableau/colonnes, ce qui mélange l'ordre de lecture brut de
    PaddleOCR entre étiquettes et valeurs.

    type_document : 'passeport' | 'permis' | 'cin' pour une pièce individuelle,
    ou None pour le mode générique (certificat de patente — voir
    soumettre_verification, étape 02).

    Retourne {nom, prenom, numero_piece, nom_entreprise, date_naissance,
    texte_brut, confiance}. date_naissance est une chaîne "JJ/MM/AAAA" (pas un
    objet date : ce dict est destiné à être stocké tel quel dans
    donnees_ocr_brutes, un JSONField — voir parser_date_naissance() pour la
    conversion en objet date). Imparfait par nature (OCR + mise en page) — le
    texte brut et le score de confiance sont toujours renvoyés pour permettre
    une relecture manuelle si un champ manque.
    """
    resultat = _obtenir_ocr().ocr(chemin_image, cls=True)

    detections, confiances = [], []
    for page in resultat or []:
        for boite, (texte, confiance) in (page or []):
            cx, cy = _centre_boite(boite)
            detections.append({'texte': texte, 'cx': cx, 'cy': cy})
            confiances.append(float(confiance))   # float natif : évite un numpy.float32 non JSON-sérialisable

    lignes = [d['texte'] for d in detections]

    labels_numero = _LABELS_NUMERO_PAR_TYPE.get(type_document, _LABELS_NUMERO_PATENTE)
    numero_brut   = _chercher_valeur_liee(detections, labels_numero)

    nom_entreprise = None
    if type_document is None:   # mode générique (patente) uniquement
        nom_entreprise = _chercher_valeur_liee(detections, _LABELS_ENTREPRISE)

    return {
        'nom':            _chercher_valeur_liee(detections, _LABELS_NOM),
        'prenom':         _chercher_valeur_liee(detections, _LABELS_PRENOM),
        'numero_piece':   _nettoyer_numero(numero_brut),
        'nom_entreprise': nom_entreprise,
        'date_naissance': _chercher_date_naissance(lignes),
        'texte_brut':     "\n".join(lignes),
        'confiance':      sum(confiances) / len(confiances) if confiances else 0.0,
    }


def parser_date_naissance(date_extraite):
    """Convertit une date extraite ('JJ/MM/AAAA' ou variantes -/.) en objet date, ou None si invalide."""
    if not date_extraite:
        return None
    trouve = _RE_DATE.search(date_extraite)
    if not trouve:
        return None
    jour, mois, annee = (int(x) for x in trouve.groups())
    try:
        return date(annee, mois, jour)
    except ValueError:
        return None
