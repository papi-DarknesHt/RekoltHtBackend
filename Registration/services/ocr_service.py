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
# "SIYATI" seul (sans "NOM") : plus fiable comme ancre PRIORITAIRE que le
# jeu complet ci-dessus — voir son usage dans extraire_infos_piece. "NOM" est
# un token de 3 lettres qui se cache dans une foule d'autres mots (à
# commencer par "PRÉNOM" lui-même), alors qu'aucun mot français/créole
# courant sur ces pièces ne contient "SIYATI" par accident.
_LABELS_NOM_PRIORITAIRE = {'SIYATI'}
_LABELS_PRENOM = {'PRENOM', 'NON'}
# "SIYATI" (créole) désigne à la fois le libellé du nom ("Siyati/Nom") ET la
# ligne de signature plus bas sur le passeport ("Siyati [...] a / Signature du
# titulaire") — sans cette exclusion, la ligne de signature est retenue comme
# valeur du nom, car elle a du texte fusionné dans la même boîte qui gagne la
# recherche avant même d'essayer la vraie boîte "Siyati/Nom" (priorité 1 de
# _chercher_valeur_liee) — constaté en conditions réelles.
# "PRENOM" contient littéralement le token "NOM" ("PRE-NOM") : sans cette
# exclusion, la boîte libellée "Prénom/Non" (ex: sur la CIN) est elle-même
# retenue comme candidate pour la recherche du NOM, et comme sur la CIN ce
# libellé précède le vrai "Nom/Siyati" dans l'ordre de détection, sa boîte
# voisine (la vraie valeur du PRÉNOM) est renvoyée à tort comme valeur du NOM
# — constaté en conditions réelles, systématique sur toute CIN (l'ordre des
# champs y est inversé par rapport au passeport). Grâce à la tolérance OCR de
# _label_present, cette exclusion matche aussi les variantes mal lues de
# "PRENOM" (ex: "PRANOM") sans qu'il soit besoin de les répertorier ici.
# Filet de sécurité en plus de _LABELS_NOM_PRIORITAIRE, pas un remplacement :
# celui-ci ne protège que la recherche de repli sur le jeu complet _LABELS_NOM
# (voir extraire_infos_piece), utilisée seulement quand "SIYATI" est
# introuvable (même approximativement).
_LABELS_EXCLUS_NOM = {'SIGNATURE', 'TITULAIRE', 'PRENOM'}
_LABELS_NUMERO_PAR_TYPE = {
    # le titre bilingue du document ("PASPÒ" / "PASSEPORT") est imprimé sur
    # deux lignes séparées, et selon la photo, le numéro de passeport peut se
    # trouver géométriquement plus proche de l'une ou l'autre ligne (parfois
    # même AU-DESSUS de la ligne "PASSEPORT", ce qui l'exclut de sa recherche
    # de voisin — voir la restriction "jamais au-dessus" dans
    # _chercher_valeur_liee) — inclure les deux comme ancres possibles permet
    # de retomber sur celle qui a effectivement le numéro comme voisin
    # valide, constaté en conditions réelles.
    'passeport': {'PASSEPORT', 'PASPO'},           # "Paspò nimewo / N° Passeport"
    # bare "CARTE"/"KAT" (créole, "carte") matchent aussi le TITRE du document
    # ("CARTE D'IDENTIFICATION NATIONALE", "KAT IDANTIFIKASYON NASYONAL"), la
    # ligne de signature ("Siyati mèt KAT la") et les libellés de date
    # d'émission/expiration ("Dat KAT la fèt/fini", littéralement "date de la
    # carte faite/finie") — toutes ces boîtes concurrencent alors la vraie
    # boîte de libellé et la recherche de voisin peut renvoyer un fragment de
    # l'une d'elles au lieu du vrai numéro de carte — constaté en conditions
    # réelles. Les paires "DE CARTE"/"NIMEWO KAT" ne désignent, elles,
    # jamais que le vrai champ numéro de carte sur cette pièce.
    # "Nimewo kat" seul capturerait aussi le "la" qui suit (article défini
    # créole, "LE numéro de carte" — pas un fragment de la valeur) : la
    # recherche de valeur fusionnée dans la même boîte (_valeur_dans_meme_boite)
    # coupe alors juste après "kat", et le reste de la boîte (" la") est pris
    # à tort pour la valeur — constaté en conditions réelles. Inclure "la"
    # dans le libellé lui-même règle ce cas.
    'cin':       {'DE CARTE', 'NIMEWO KAT LA'},     # "Numéro de carte / Nimewo kat la"
    'permis':    {'NIF'},                           # identifiant retenu pour le permis (voir consigne produit)
}
_LABELS_NUMERO_PATENTE = {'PATENTE'}                # "Numéro de Patente"
_LABELS_ENTREPRISE     = {'DELIVREA', 'DELIVRE'}    # "Délivré à" (nom de l'entreprise sur le certificat)
_LABELS_NAISSANCE = {'NAISSANCE'}                   # "Date de naissance / Dat li fèt"
# "Lieu de naissance / Kote li fèt" contient lui aussi le mot "naissance",
# mais désigne le LIEU, pas la date — voir _chercher_date_naissance.
_LABELS_EXCLUS_NAISSANCE = {'LIEU', 'KOTE'}


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


def _distance_levenshtein(a, b):
    """
    Distance de Levenshtein (nombre minimal de substitutions/insertions/
    suppressions d'un caractère pour passer de `a` à `b`) — implémentation
    naïve en Python pur, largement suffisante ici : les chaînes comparées
    sont toujours très courtes (un libellé de pièce d'identité, quelques
    caractères).
    """
    if a == b:
        return 0
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    precedente = list(range(n + 1))
    for i in range(1, m + 1):
        courante = [i] + [0] * n
        for j in range(1, n + 1):
            cout = 0 if a[i - 1] == b[j - 1] else 1
            courante[j] = min(
                precedente[j] + 1,          # suppression
                courante[j - 1] + 1,        # insertion
                precedente[j - 1] + cout,   # substitution
            )
        precedente = courante
    return precedente[n]


def _tolerance_ocr(label):
    """
    Nombre d'erreurs OCR tolérées (une lettre substituée, ajoutée ou
    manquante) pour reconnaître ce libellé — 1 pour les libellés de 6
    caractères ou plus, 0 (correspondance exacte) en dessous.

    PaddleOCR corrompt parfois un libellé d'une seule lettre — constaté en
    conditions réelles sous deux formes différentes : substitution d'une
    lettre visuellement proche ("Prénom"→"Pranom", "Non"→"Nan" : é/o→a) et
    suppression pure et simple d'une lettre ("Siyati"→"Siyat",
    "Naissance"→"Nassance"). Une tolérance de 1 sur la distance de
    Levenshtein couvre les deux cas de façon générale, plutôt que de
    répertorier au cas par cas chaque variante déjà observée — au risque de
    rater la prochaine.

    Réservée aux libellés d'UN SEUL mot d'au moins 6 caractères. En dessous
    de 6 (ex: "NOM", "NON", "NIF", "LIEU", "KOTE"), une seule lettre de
    différence peut transformer le mot en un autre mot plausible du texte
    environnant et provoquer un faux positif — constaté en conditions
    réelles ("Nan", préposition créole courante ["dans/à", ex: "nan biwo
    ONI"], à une lettre de "Non" et présente dans le texte légal au dos
    d'une CIN). Pour un libellé à PLUSIEURS mots (ex: "DE CARTE"), la
    fenêtre glissante de _sous_chaine_floue ignore les frontières de mots :
    un seul caractère de différence peut alors faire glisser la fenêtre sur
    une phrase sans rapport ("...trouvé cet**te carte** est prié..." matche
    "DE CARTE" à distance 1 : "TE CARTE") — constaté en conditions réelles,
    d'où l'exclusion des libellés à espace(s) de la tolérance.
    """
    return 1 if len(label) >= 6 and ' ' not in label else 0


def _sous_chaine_floue(texte, label, tolerance):
    """Vrai si `label` apparaît dans `texte` en sous-chaîne EXACTE, ou (si
    `tolerance` > 0) à une distance de Levenshtein <= `tolerance` d'une
    fenêtre de taille proche à une position quelconque de `texte`."""
    if label in texte:
        return True
    if tolerance == 0:
        return False
    n = len(label)
    for taille in range(max(1, n - tolerance), n + tolerance + 1):
        for i in range(0, len(texte) - taille + 1):
            if _distance_levenshtein(texte[i:i + taille], label) <= tolerance:
                return True
    return False


def _position_floue(texte, label, tolerance):
    """
    Comme _sous_chaine_floue, mais retourne la position (index de début,
    longueur) de la MEILLEURE fenêtre correspondante (distance minimale) au
    lieu d'un simple booléen — nécessaire pour _valeur_dans_meme_boite, qui a
    besoin de savoir OÙ s'arrête le libellé dans le texte pour extraire ce
    qui suit, pas seulement s'il est présent quelque part (constaté en
    conditions réelles sur un certificat de patente : "Délivré à" lu par
    l'OCR comme "Delive a" — lettre manquante — n'était alors JAMAIS
    retrouvé par une simple recherche de sous-chaîne exacte, et la valeur
    fusionnée juste après passait inaperçue, la recherche de repli sur la
    boîte voisine renvoyant un champ sans rapport à la place).

    Retourne None si aucune fenêtre n'est dans la tolérance.
    """
    if label in texte:
        return texte.find(label), len(label)
    if tolerance == 0:
        return None
    n = len(label)
    meilleure = None  # (distance, index, taille)
    for taille in range(max(1, n - tolerance), n + tolerance + 1):
        for i in range(0, len(texte) - taille + 1):
            distance = _distance_levenshtein(texte[i:i + taille], label)
            if distance <= tolerance and (meilleure is None or distance < meilleure[0]):
                meilleure = (distance, i, taille)
    return (meilleure[1], meilleure[2]) if meilleure else None


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

    Tolère aussi jusqu'à une erreur OCR sur le libellé lui-même, pour les
    libellés assez longs pour que ce soit sûr — voir _tolerance_ocr et
    _sous_chaine_floue.
    """
    normalise = _normalise(texte)
    return any(_sous_chaine_floue(normalise, label, _tolerance_ocr(label)) for label in labels)


def _est_un_label(ligne):
    """Vrai si la ligne correspond à une étiquette connue — sert à ne jamais
    retourner un autre label comme si c'était une valeur (voir _chercher_valeur_liee)."""
    # _LABELS_NAISSANCE manquait ici : une boîte "NAISSANCE"/"Dat li fèt"
    # proche du libellé recherché (ex. NOM) n'était jamais reconnue comme un
    # autre libellé, et pouvait donc être renvoyée à tort comme si c'était la
    # valeur cherchée (voir _chercher_valeur_liee).
    tous_labels = _LABELS_NOM | _LABELS_PRENOM | _LABELS_NUMERO_PATENTE | _LABELS_ENTREPRISE | _LABELS_NAISSANCE
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

    Recherche du libellé TOLÉRANTE aux erreurs OCR (_position_floue), pas une
    simple sous-chaîne exacte : sans ça, un libellé mal lu (une lettre en
    moins, ex. "Délivré à" lu "Delive a") n'est jamais retrouvé ICI même s'il
    l'est par _label_present (qui, lui, tolère déjà ces erreurs pour
    sélectionner la boîte candidate en amont dans _chercher_valeur_liee) —
    l'extraction retombe alors sur la boîte voisine la plus proche, sans
    rapport avec la vraie valeur (constaté en conditions réelles).
    """
    normalise = _normalise(texte)
    for label in labels:
        position = _position_floue(normalise, label, _tolerance_ocr(label))
        if position is None:
            continue
        idx, taille_matchee = position
        fin = idx + taille_matchee
        # le libellé doit être un mot complet, pas le préfixe d'un mot plus
        # long (ex: "PRENOM" ne doit pas matcher dans "PRENOMS" et renvoyer
        # juste le "S" final comme si c'était une valeur, constaté en
        # conditions réelles) — sauf s'il est immédiatement suivi d'un
        # chiffre, cas réel d'une valeur fusionnée ("Patente590712200"), ou
        # si le libellé se termine déjà par la préposition "A"/"À" (ex.
        # "DELIVREA" = "délivré à" sans accent, voir _LABELS_ENTREPRISE) :
        # dans ce cas la lettre suivante est le DÉBUT de la valeur
        # elle-même, jamais la continuation du libellé — un certificat de
        # patente réel fusionne typiquement tout "Délivré à" directement à
        # la valeur sans aucun séparateur ("DelivreaGRAIDLOCAL" pour
        # "Délivré à : GRAID LOCAL") ; sans cette exception, l'ancien
        # garde-fou bloquait alors totalement l'extraction, la recherche de
        # repli (boîte voisine, priorité 2) renvoyant un champ sans rapport
        # à la place (constaté en conditions réelles, voir capture d'écran
        # jointe — le champ "Numéro d'Immatriculation Fiscale" était
        # renvoyé à tort comme nom d'entreprise).
        if fin < len(normalise) and normalise[fin].isalpha() and not label.endswith('A'):
            continue
        reste = texte[fin:].strip(" :./-")
        # "Délivré à" : PaddleOCR omet parfois l'accent, et la préposition
        # "a"/"à" se retrouve alors collée à la vraie valeur sans séparateur
        # franc — soit avec un ":"/"." collé ("Delivre a:GRAIDLOCAL"), soit
        # juste un espace ("Delivre a GRAIDLOCAL") — un simple
        # .strip(" :./-") ne l'enlève pas seul car "a" n'est pas un
        # caractère de ponctuation. On la retire explicitement quand elle
        # apparaît seule en tête, immédiatement suivie d'une limite
        # (espace/":"/"."/fin de chaîne) plutôt que d'une autre lettre —
        # condition volontairement étroite (limite stricte après la lettre
        # isolée) pour ne jamais rogner une vraie valeur commençant par "A"
        # (ex. un nom d'entreprise "Alpha SA", où "A" est immédiatement
        # suivi de "l", pas d'une limite).
        reste = re.sub(r'^[aà](?=[\s:.]|$)[\s:.]*', '', reste, flags=re.IGNORECASE)
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


_RE_NUMERO_PASSEPORT = re.compile(r'^[A-Z][A-Z0-9]{5,15}$')


def _ressemble_a_un_numero_passeport(texte):
    """
    Vrai si le texte a la forme d'un numéro de passeport haïtien : une lettre
    suivie de chiffres, sans tiret (ex: "R12186923") — PAS un NIF, qui lui est
    purement numérique, parfois avec tirets (ex: "0085147906", "008-512-877-0").

    Le seul libellé disponible pour ancrer la recherche du numéro de passeport
    est le mot "PASSEPORT", qui désigne à la fois le TITRE du document
    ("PASPÒ/PASSEPORT" à gauche de la page) et le vrai libellé du champ
    ("Paspò nimewo/N° Passeport" en haut) — sur une photo où ce dernier est
    mal reconnu par l'OCR (glissé/tronqué), seule la boîte-titre reste
    candidate, et sa boîte voisine géométrique la plus proche peut être le NIF
    plutôt que le vrai numéro de passeport — constaté en conditions réelles.
    Ce filtre, plus strict que _ressemble_a_un_numero (qui accepte aussi bien
    le NIF), départage les deux quelle que soit la boîte-ancre retenue.
    """
    valeur = texte.strip().upper()
    return bool(_RE_NUMERO_PASSEPORT.match(valeur)) and any(c.isdigit() for c in valeur)


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

    `filtre` : si fourni, s'applique à la fois à la valeur fusionnée dans la
    même boîte (priorité 1) et à la recherche par position (priorité 2), qui
    préfère parmi les boîtes voisines candidates la première qui le satisfait
    plutôt que la plus proche géométriquement (voir _ressemble_a_un_numero).

    Sans ce filtre en priorité 1, un libellé court comme "PASPO" peut aussi
    matcher un fragment sans rapport à l'intérieur d'une boîte d'en-tête plus
    longue (ex: "...Paspo nimewo/NPassepor", tronquée par l'OCR) et en
    renvoyer le reste comme si c'était la valeur, avant même d'essayer la
    vraie boîte-titre courte — constaté en conditions réelles.

    En priorité 2, si la boîte-libellé retenue n'a AUCUN voisin satisfaisant
    le filtre, les autres boîtes-libellé candidates sont essayées avant
    d'abandonner — ex: sur un passeport, le titre bilingue "PASPÒ/PASSEPORT"
    tient sur deux boîtes, et selon la photo le numéro de passeport peut être
    un voisin valide de l'une sans l'être de l'autre (la même règle
    géométrique "jamais au-dessus" qui sert à écarter le NIF ailleurs peut
    aussi exclure le vrai numéro pour la première boîte-titre essayée) ; ne
    retomber sur la plus proche géométrique sans filtre qu'en tout dernier
    recours, une fois toutes les boîtes-libellé épuisées — constaté en
    conditions réelles.

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
    # le libellé "riche" (ex: la ligne d'en-tête complète "Kalite/Type Peyi ki
    # fè l/Pays émetteur Paspo nimewo/N° Passeport") porte plus de contexte
    # que le simple titre du document répétant le même mot (ex: la boîte
    # "PASSEPORT" du titre "PASPÒ/PASSEPORT") ; sur certains documents,
    # l'ordre de détection place le titre AVANT le vrai libellé, et comme la
    # recherche s'arrête à la première boîte candidate ayant un voisin
    # exploitable, le titre gagnait à tort et sa boîte voisine géométrique
    # (parfois un champ sans rapport, ex: le NIF) était renvoyée comme valeur
    # — constaté en conditions réelles sur un passeport. Essayer d'abord la
    # boîte la plus riche en texte règle ce cas sans dépendre de l'ordre de
    # détection.
    candidats.sort(key=lambda d: len(d['texte']), reverse=True)

    for detection in candidats:
        meme_boite = _valeur_dans_meme_boite(detection['texte'], labels)
        if meme_boite and not _est_un_label(meme_boite) and (not filtre or filtre(meme_boite)):
            return meme_boite.strip(" :.-")

    # priorité 2 : repli sur la boîte voisine la plus proche du premier
    # libellé trouvé (mise en page en tableau/ligne)
    repli = None
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
        if not filtre:
            return proches[0][1].strip(" :.-")
        correspond = next((texte for _, texte in proches if filtre(texte)), None)
        if correspond:
            return correspond.strip(" :.-")
        if repli is None:
            repli = proches[0][1]
    return repli.strip(" :.-") if repli else None


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


def _date_valide(jour, mois, annee):
    """
    Vrai si (jour, mois, année) forme une date calendaire réelle — sert à
    écarter les faux positifs de _RE_DATE : un motif JJ-MM-AAAA peut matcher
    par coïncidence un nombre sans rapport ailleurs sur la pièce (ex: le
    "Numéro d'identification nationale" imprimé sur la page opposée d'un
    passeport, visible en transparence sur la photo — "13-38-06-6598" contient
    "38-06-6598", qui matche le motif JJ-MM-AAAA alors que 38 n'est pas un
    jour valide) — constaté en conditions réelles.
    """
    try:
        date(annee, mois, jour)
        return True
    except ValueError:
        return False


def _chercher_date_naissance(detections):
    """
    Retourne la date de naissance trouvée sur la ligne juste EN DESSOUS du
    libellé "Naissance" (voir _LABELS_NAISSANCE ; le libellé "Lieu de
    naissance" contient aussi ce mot mais désigne le lieu, pas la date — voir
    _LABELS_EXCLUS_NAISSANCE), au format JJ/MM/AAAA.

    Recherche par POSITION plutôt qu'un balayage de la première date trouvée
    dans tout le document : un passeport peut afficher d'autres nombres à
    motif de date sans rapport ailleurs sur la page (ex: le "Numéro
    d'identification nationale" imprimé sur la page opposée du passeport,
    visible en transparence sur la photo, qui correspond par coïncidence au
    motif JJ-MM-AAAA), ou d'autres dates bien réelles mais d'un autre champ
    (émission/expiration) — un balayage global peut prendre l'une ou l'autre
    à tort avant même d'atteindre la vraie date de naissance — constaté en
    conditions réelles.

    La marge de recherche verticale est proportionnelle à la hauteur moyenne
    des boîtes détectées (donc indépendante de la résolution/du zoom de la
    photo), pas un nombre de pixels fixe. Contrairement à _chercher_valeur_liee
    (utilisé pour nom/prénom/numéro), aucune contrainte horizontale n'est
    appliquée : en pratique, le centre de la boîte de valeur (un token court,
    ex: "01Me/Mai02") est souvent décalé à GAUCHE du centre de sa boîte de
    libellé, bien plus longue (ex: "Dat li fèt/Date de naissance") — une
    contrainte "jamais à gauche" exclurait alors à tort la vraie valeur,
    constaté en conditions réelles.

    Chaque candidat est validé avant d'être retourné (voir _date_valide) : un
    format reconnu (JJ-MM-AAAA ou JJ + mois abrégé + AA) qui ne correspond à
    aucune date calendaire réelle est ignoré silencieusement, et le suivant
    essayé.
    """
    hauteurs = [d['h'] for d in detections if d.get('h')]
    marge_y = 4 * (sum(hauteurs) / len(hauteurs)) if hauteurs else 60

    ancres = [
        d for d in detections
        if _label_present(d['texte'], _LABELS_NAISSANCE)
        and not _label_present(d['texte'], _LABELS_EXCLUS_NAISSANCE)
    ]
    candidats = []
    for ancre in ancres:
        for autre in detections:
            if autre is ancre or _est_un_label(autre['texte']):
                continue
            dy = autre['cy'] - ancre['cy']
            if 0 < dy <= marge_y:
                candidats.append((dy, autre['texte']))
    candidats.sort(key=lambda p: p[0])

    for _, texte in candidats:
        trouve = _RE_DATE.search(texte)
        if trouve:
            jour, mois, annee = (int(x) for x in trouve.groups())
            if _date_valide(jour, mois, annee):
                return trouve.group(0)
    for _, texte in candidats:
        trouve = _RE_DATE_MOIS_LETTRES.search(texte)
        if trouve:
            jour, mois_texte, annee_deux_chiffres = trouve.groups()
            mois = _mois_depuis_texte(mois_texte)
            if mois:
                annee = _annee_sur_quatre_chiffres(annee_deux_chiffres)
                if _date_valide(int(jour), mois, annee):
                    return f"{int(jour):02d}/{mois:02d}/{annee}"
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
            hauteur = max(p[1] for p in boite) - min(p[1] for p in boite)
            detections.append({'texte': texte, 'cx': cx, 'cy': cy, 'h': hauteur})
            confiances.append(float(confiance))   # float natif : évite un numpy.float32 non JSON-sérialisable

    lignes = [d['texte'] for d in detections]

    labels_numero   = _LABELS_NUMERO_PAR_TYPE.get(type_document, _LABELS_NUMERO_PATENTE)
    filtre_numero   = _ressemble_a_un_numero_passeport if type_document == 'passeport' else _ressemble_a_un_numero
    numero_brut     = _chercher_valeur_liee(detections, labels_numero, filtre=filtre_numero)

    nom_entreprise = None
    nom = prenom = None
    if type_document is None:   # mode générique (patente) uniquement
        nom_entreprise = _chercher_valeur_liee(detections, _LABELS_ENTREPRISE)
        # un certificat de patente n'a ni champ NOM ni PRENOM (document
        # d'entreprise, pas une pièce individuelle) : les chercher quand même
        # ne peut que produire un faux positif — les labels "NOM"/"NON" sont
        # de simples sous-chaînes (voir _label_present), qui matchent aussi
        # à l'intérieur de mots sans rapport (ex: "NOM" dans "ÉCONOMIE" sur
        # l'en-tête, "NON" dans "Non Classées" du secteur d'activité) ; le
        # garde-fou "lettre suivante non-alphabétique" de
        # _valeur_dans_meme_boite empêche seulement la valeur fusionnée dans
        # LA MÊME boîte, pas le repli "boîte voisine la plus proche"
        # (priorité 2 de _chercher_valeur_liee) qui renvoie alors un fragment
        # de texte totalement sans rapport — constaté en conditions réelles.
    else:
        # "SIYATI" d'abord : contrairement à "NOM" (voir _LABELS_NOM_PRIORITAIRE),
        # ce token n'a aucune chance de se cacher dans une variante mal lue du
        # libellé PRÉNOM voisin — on ne retombe sur le jeu complet _LABELS_NOM
        # (avec son exclusion PRENOM, tolérante aux fautes d'OCR, en filet de
        # sécurité) que si "SIYATI" n'apparaît nulle part sur la pièce, même
        # approximativement (OCR n'a pas su le lire du tout).
        nom = _chercher_valeur_liee(detections, _LABELS_NOM_PRIORITAIRE, exclure=_LABELS_EXCLUS_NOM)
        if not nom:
            nom = _chercher_valeur_liee(detections, _LABELS_NOM, exclure=_LABELS_EXCLUS_NOM)
        prenom = _chercher_valeur_liee(detections, _LABELS_PRENOM)

    # le permis n'a pas de champ PRENOM distinct : nom et prénom sont fusionnés
    # dans la boîte NOM, séparés par un point (ex: "Napoleon.Wagnerson" : nom
    # avant le point, prénom après) — voir consigne produit
    if type_document == 'permis' and nom and '.' in nom:
        nom_part, _, prenom_part = nom.partition('.')
        nom    = nom_part.strip() or nom
        prenom = prenom_part.strip() or prenom

    return {
        'nom':            nom,
        'prenom':         prenom,
        'numero_piece':   _nettoyer_numero(numero_brut),
        'nom_entreprise': nom_entreprise,
        'date_naissance': _chercher_date_naissance(detections),
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
