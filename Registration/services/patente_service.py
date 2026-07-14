"""
Vérification automatique d'une entreprise sur le registre public du Ministère
du Commerce et de l'Industrie — guichet.mci.ht/recherche (étape 05 de
soumettre_verification, entreprise uniquement).

IMPORTANT — comportement réel du site, vérifié en conditions réelles avant
d'écrire ce module (et non supposé à l'avance) :
  - guichet.mci.ht/ répond normalement à un vrai navigateur (Playwright/
    Chromium), mais renvoie 403/coupe la connexion à des clients HTTP non-
    navigateur (urllib) — probable protection anti-bot. Un Playwright headless
    standard passe, mais ce n'est pas garanti dans la durée ni depuis toutes
    les IP (ex: IP du serveur de production) : le repli "indisponible" ci-
    dessous (PatenteIndisponible) n'est donc pas qu'une précaution théorique.
  - Le moteur de recherche est une "Recherche d'antériorité" PAR NOM (champ
    <input id="q">, ex: "HS CONSTRUCTION") — il n'existe PAS de recherche par
    numéro de patente. On cherche donc par nom_Entreprise, pas par
    numero_patente_extrait.
  - Résultats rendus via Livewire : les données structurées sont dans
    l'attribut wire:snapshot (JSON) de l'élément racine du composant, pas
    seulement dans le texte affiché — bien plus fiable à parser que le HTML
    rendu. Un résultat contient {type, name, object, status, records: [...]},
    et chaque enregistrement contient {folio, registry_number, ...}.
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL_RECHERCHE = "https://guichet.mci.ht/recherche"


class PatenteIndisponible(Exception):
    """
    guichet.mci.ht n'a pas pu être interrogé avec succès (site indisponible,
    timeout, ou structure de page inattendue — sélecteurs/format wire:snapshot
    différents de ce qui a été vérifié). Le pipeline (voir
    _verifier_patente_mci, Registration/views.py) laisse alors la demande en
    'en_attente_manuelle' plutôt que d'échouer à tort une entreprise légitime.
    """


def _normaliser(nom):
    return (nom or "").strip().upper()


def _extraire_resultats(snapshot):
    """
    Transforme le JSON brut de wire:snapshot en une liste plate de résultats :
    [{'nom': str, 'type': str, 'numero': str|None}, ...]

    numero est construit comme "folio-registry_number" (ex: "358-18",
    affiché "No 358, REG. 18" sur le site) à partir du premier enregistrement
    du résultat, ou None si le résultat n'a aucun enregistrement exploitable.
    """
    resultats = []
    # data.data est enveloppé par Livewire comme [liste_reelle, marqueur_collection]
    enveloppe = snapshot.get('data', {}).get('data') or [[]]
    groupes = enveloppe[0] if enveloppe else []

    for groupe in groupes:
        if not groupe or not isinstance(groupe[0], dict):
            continue
        objet = groupe[0]

        numero = None
        try:
            enregistrement = objet['records'][0][0][0]
            numero = f"{enregistrement.get('folio')}-{enregistrement.get('registry_number')}"
        except (TypeError, IndexError, KeyError):
            numero = None

        resultats.append({
            'nom':    objet.get('name'),
            'type':   objet.get('type'),
            'numero': numero,
        })

    return resultats


def verifier_patente(nom_entreprise, numero_attendu=None, timeout_ms=20000):
    """
    Recherche nom_entreprise sur guichet.mci.ht/recherche et retourne :
      {
        'trouve':            bool,        # un résultat correspond au nom (normalisé)
        'nom_correspondant':  str | None,
        'numero_correspondant': str | None,  # "folio-registry_number" du résultat trouvé
        'numero_concorde':   bool | None,   # comparaison avec numero_attendu, None si non fourni/non comparable
        'resultats_bruts':   list,          # tous les résultats retournés, pour relecture admin
      }

    Lève PatenteIndisponible si le site ne répond pas ou si sa structure a
    changé (wire:snapshot introuvable) — voir le docstring du module : ce cas
    est attendu, pas une erreur de programmation.
    """
    try:
        with sync_playwright() as p:
            navigateur = p.chromium.launch(headless=True)
            page = navigateur.new_page()
            try:
                page.goto(URL_RECHERCHE, timeout=timeout_ms, wait_until="domcontentloaded")
                page.fill("input#q", nom_entreprise, timeout=timeout_ms)
                page.click('button:has-text("SOUMETTRE")', timeout=timeout_ms)
                # Livewire met à jour la page via AJAX (pas de navigation classique) :
                # un court délai fixe est plus fiable ici qu'un wait_for_load_state,
                # vérifié empiriquement (~2-4s pour que wire:snapshot se mette à jour)
                page.wait_for_timeout(4000)

                import json
                element = page.locator("[wire\\:snapshot]").first
                brut = element.get_attribute("wire:snapshot", timeout=timeout_ms)
                if not brut:
                    raise PatenteIndisponible("wire:snapshot introuvable — structure de page inattendue")
                snapshot = json.loads(brut)
            finally:
                navigateur.close()
    except PlaywrightTimeoutError as e:
        raise PatenteIndisponible(f"guichet.mci.ht indisponible ou trop lent : {e}") from e
    except PatenteIndisponible:
        raise
    except Exception as e:
        raise PatenteIndisponible(f"Erreur d'accès/de lecture de guichet.mci.ht : {e}") from e

    resultats = _extraire_resultats(snapshot)
    nom_cherche = _normaliser(nom_entreprise)

    correspondance = next(
        (r for r in resultats if nom_cherche == _normaliser(r['nom']) or nom_cherche in _normaliser(r['nom'])),
        None,
    )

    if correspondance is None:
        return {
            'trouve': False, 'nom_correspondant': None, 'numero_correspondant': None,
            'numero_concorde': None, 'resultats_bruts': resultats,
        }

    numero_concorde = None
    if numero_attendu and correspondance['numero']:
        numero_concorde = _normaliser(numero_attendu) == _normaliser(correspondance['numero'])

    return {
        'trouve':               True,
        'nom_correspondant':    correspondance['nom'],
        'numero_correspondant': correspondance['numero'],
        'numero_concorde':      numero_concorde,
        'resultats_bruts':      resultats,
    }
