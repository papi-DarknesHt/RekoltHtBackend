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

# les passeports haïtiens affichent les dates en jour + nom(s) de mois abrégé
# bilingue créole/français + année sur 2 chiffres, souvent collés sans espace
# par l'OCR (ex: "01Me/Mai02" pour le 01/05/2002, "03Jan/Jan24" pour le
# 03/01/2024) — constaté en conditions réelles, voir _chercher_date_naissance
_RE_DATE_MOIS_LETTRES = re.compile(r'(\d{1,2})\s*([A-Za-zÀ-ÿ]{2,10}(?:/[A-Za-zÀ-ÿ]{2,10})?)\s*(\d{2})\b')
_MOIS_ABREGES = {
    'JAN': 1, 'FEV': 2, 'FÉV': 2, 'MAS': 3, 'MAR': 3, 'AVR': 4,
    'ME': 5, 'MAI': 5, 'JEN': 6, 'JUN': 6, 'JIY': 7, 'JUL': 7,
    'OUT': 8, 'AOU': 8, 'SEP': 9, 'OKT': 10, 'OCT': 10,
    'NOV': 11, 'DES': 12, 'DEC': 12, 'DÉC': 12,
}

# labels par type de document — voir _chercher_valeur_liee : la mise en page
# réelle des pièces haïtiennes (constatée sur des documents réels) place la
# VALEUR sous l'étiquette (ou parfois à sa droite), jamais collée dessus, et
# l'ordre de lecture brut de PaddleOCR mélange les colonnes d'un tableau —
# d'où la recherche par position (coordonnées des boîtes) plutôt que par
# simple ordre séquentiel du texte.
_LABELS_NOM    = {'NOM', 'SIYATI'}
_LABELS_PRENOM = {'PRENOM', 'NON'}
# "SIYATI" (créole) désigne à la fois le libellé du nom ("Siyati/Nom") ET la
# ligne de signature plus bas sur le passeport ("Siyati [...] a / Signature du
# titulaire") — sans cette exclusion, la ligne de signature est retenue comme
# valeur du nom, car elle a du texte fusionné dans la même boîte qui gagne la
# recherche avant même d'essayer la vraie boîte "Siyati/Nom" (priorité 1 de
# _chercher_valeur_liee) — constaté en conditions réelles
_LABELS_EXCLUS_NOM = {'SIGNATURE', 'TITULAIRE'}
_LABELS_NUMERO_PAR_TYPE = {
    'passeport': {'PASSEPORT'},                    # "Paspò nimewo / N° Passeport"
    'cin':       {'CARTE', 'KAT'},                  # "Numéro de carte / Nimewo kat la"
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
        reste = texte[fin:].strip(" :./-")
        if reste:
            return reste
    return None


def _ressemble_a_un_numero(texte):
    """
    Vrai si le texte contient un motif plausible de numéro de pièce (voir
    _RE_NUMERO) ET au moins un chiffre — sert à départager plusieurs boîtes
    voisines candidates de la recherche par position (priorité 2 de
    _chercher_valeur_liee). Exemple réel : sur un passeport, la ligne sous
    "N°Passeport" est découpée en trois colonnes proches ("P", "HTI",
    "R12009379") ; sans ce filtre, la boîte la plus proche géométriquement
    ("P", le code type de document) gagne à tort au lieu de la vraie colonne
    du numéro. Le chiffre est obligatoire car _RE_NUMERO seul accepte aussi
    une suite de lettres majuscules (ex: "NAPOLEON", 8 caractères) — sans
    cette exigence, un nom voisin peut être pris à tort pour le numéro de
    pièce (constaté en conditions réelles).
    """
    return bool(_RE_NUMERO.search(texte.upper())) and any(c.isdigit() for c in texte)


def _chercher_valeur_liee(detections, labels, exclure=None, filtre=None):
    """
    Cherche une détection dont le texte contient un des tokens de `labels`,
    puis retourne la valeur associée — la valeur associée est presque
    toujours juste en dessous (mise en page en tableau) ou juste à droite
    (mise en page en ligne) de son étiquette, ou parfois fusionnée dans la
    même boîte (voir _valeur_dans_meme_boite).

    `exclure` : labels supplémentaires qui, s'ils apparaissent dans la même
    boîte qu'un des `labels` recherchés, disqualifient cette boîte (ex: une
    boîte "SIYATI" qui est en réalité la ligne de signature, pas le nom —
    voir _LABELS_EXCLUS_NOM).

    `filtre` : si fourni, la recherche par position (priorité 2) préfère,
    parmi les boîtes voisines candidates, la première qui satisfait ce
    prédicat plutôt que la plus proche géométriquement (voir
    _ressemble_a_un_numero) ; si aucune ne le satisfait, on retombe sur la
    plus proche comme avant.

    Priorité 1 sur TOUTES les boîtes portant le libellé (pas seulement la
    première) : si l'une contient la valeur fusionnée dans le même texte, on
    la prend — sinon un fragment de titre contenant aussi le mot du libellé
    (ex: "DE PATENTE" dans un en-tête, avant la vraie boîte "Numero de
    Patente590712200" plus bas) gagnerait à tort la recherche par simple
    ordre de détection, et sa recherche de boîte voisine (priorité 2)
    renverrait une valeur sans rapport — constaté en conditions réelles sur
    un certificat de patente.
    """
    candidats = [
        d for d in detections
        if _label_present(d['texte'], labels)
        and not (exclure and _label_present(d['texte'], exclure))
    ]

    for detection in candidats:
        meme_boite = _valeur_dans_meme_boite(detection['texte'], labels)
        if meme_boite and not _est_un_label(meme_boite):
            return meme_boite.strip(" :.-")

    # priorité 2 : repli sur la boîte voisine la plus proche du premier
    # libellé trouvé (mise en page en tableau/ligne)
    for detection in candidats:
        lx, ly = detection['cx'], detection['cy']
        proches = []
        for autre in detections:
            if autre is detection or _est_un_label(autre['texte']):
                continue
            dx, dy = autre['cx'] - lx, autre['cy'] - ly
            # la valeur est en dessous (dy > 0) ou sur la même ligne à droite
            # (dy proche de 0, dx > 0) — jamais au-dessus ni loin à gauche
            if dy < -10 or (abs(dy) < 15 and dx < -10):
                continue
            distance = (dy if dy > 15 else abs(dy) * 0.3) + abs(dx) * 0.15
            proches.append((distance, autre['texte']))
        if not proches:
            continue
        proches.sort(key=lambda p: p[0])
        if filtre:
            correspond = next((texte for _, texte in proches if filtre(texte)), None)
            if correspond:
                return correspond.strip(" :.-")
        return proches[0][1].strip(" :.-")
    return None


def _mois_depuis_texte(texte):
    """Fait correspondre un nom de mois abrégé (créole et/ou français, ex:
    'Me/Mai') à son numéro, via _MOIS_ABREGES — None si aucun ne correspond."""
    normalise = _sans_accents(texte).upper()
    for partie in re.split(r'[/\s]+', normalise):
        for abrege, numero in _MOIS_ABREGES.items():
            if partie.startswith(abrege):
                return numero
    return None


def _annee_sur_quatre_chiffres(annee_deux_chiffres):
    """Convertit une année sur 2 chiffres en 4 chiffres. Pivot à 30 : une
    pièce d'identité affiche aussi bien une date de naissance ancienne
    (19xx) qu'une date d'émission/expiration récente (20xx) — 30 couvre les
    naissances jusqu'en 2030 en 20xx, au-delà on suppose 19xx."""
    annee = int(annee_deux_chiffres)
    return 2000 + annee if annee <= 30 else 1900 + annee


def _chercher_date_naissance(lignes):
    """
    Retourne la première date trouvée, au format JJ/MM/AAAA.

    Essaie d'abord le format numérique standard (JJ/MM/AAAA, voir _RE_DATE),
    puis, si aucune ligne ne correspond, le format bilingue créole/français
    des passeports haïtiens : jour + nom(s) de mois abrégé(s) + année sur 2
    chiffres, souvent collés sans espace par l'OCR (ex: "01Me/Mai02" pour le
    01/05/2002 — voir _RE_DATE_MOIS_LETTRES et _MOIS_ABREGES). Plusieurs
    correspondances peuvent apparaître sur une même ligne (faux positifs,
    ex. un fragment du numéro de passeport lu comme "94HTI02") : on ignore
    silencieusement celles dont le texte ne correspond à aucun mois connu et
    on essaie la suivante, plutôt que d'abandonner la ligne entière.
    """
    for ligne in lignes:
        trouve = _RE_DATE.search(ligne)
        if trouve:
            return trouve.group(0)
    for ligne in lignes:
        for trouve in _RE_DATE_MOIS_LETTRES.finditer(ligne):
            jour, mois_texte, annee_deux_chiffres = trouve.groups()
            mois = _mois_depuis_texte(mois_texte)
            if mois:
                return f"{int(jour):02d}/{mois:02d}/{_annee_sur_quatre_chiffres(annee_deux_chiffres)}"
    return None


def _nettoyer_numero(valeur):
    """Extrait le motif ressemblant à un numéro de pièce dans une valeur OCRisée brute."""
    if not valeur:
        return None
    trouve = _RE_NUMERO.search(valeur.upper())
    return trouve.group(1) if trouve else valeur.strip() or None


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
    numero_brut   = _chercher_valeur_liee(detections, labels_numero, filtre=_ressemble_a_un_numero)

    nom_entreprise = None
    if type_document is None:   # mode générique (patente) uniquement
        nom_entreprise = _chercher_valeur_liee(detections, _LABELS_ENTREPRISE)

    return {
        'nom':            _chercher_valeur_liee(detections, _LABELS_NOM, exclure=_LABELS_EXCLUS_NOM),
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
