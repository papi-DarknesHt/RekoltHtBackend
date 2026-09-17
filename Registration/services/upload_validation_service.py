"""
Validation des fichiers téléversés par les utilisateurs (photo de profil,
logo d'entreprise, documents KYC, photos de produit) — trouvé lors d'un audit
de sécurité (demande explicite) : Django n'exécute JAMAIS automatiquement la
validation d'un ImageField (vérification Pillow que le contenu est bien une
image décodable, taille max...) au moment de `.save()`/`.objects.create()` —
seul un appel explicite à `full_clean()` la déclenche, jamais fait ici
(cohérent avec le reste du projet). Sans ce module, n'importe quel fichier
(taille arbitraire, contenu arbitraire) pouvait être stocké tel quel derrière
un champ censé ne contenir que des images.

Utilisé à la fois pour les images reçues en base64 (photo_profil, logo — voir
Registration/views.py) et pour les uploads multipart (photos de produit,
documents KYC — voir Produits/views/photoProduits.py et
Registration/views.py::soumettre_verification).
"""
from io import BytesIO

from PIL import Image, UnidentifiedImageError

# 8 Mo — cohérent avec la limite déjà annoncée côté frontend (~5 Mo par
# photo, marge incluse pour l'encodage base64 qui gonfle la taille de ~33%)
TAILLE_MAX_IMAGE_OCTETS = 8 * 1024 * 1024


def valider_image(fichier_binaire):
    """
    Vérifie qu'un contenu binaire est une image décodable et de taille
    raisonnable. Lève ValueError sinon (déjà attrapée et renvoyée en 400 par
    tous les appelants existants de ce module, voir _enregistrer_photo_profil/
    _enregistrer_logo_entreprise, Registration/views.py).
    """
    if not fichier_binaire:
        raise ValueError("Fichier vide")
    if len(fichier_binaire) > TAILLE_MAX_IMAGE_OCTETS:
        raise ValueError(f"Le fichier dépasse la taille maximale autorisée ({TAILLE_MAX_IMAGE_OCTETS // (1024 * 1024)} Mo)")
    try:
        image = Image.open(BytesIO(fichier_binaire))
        image.verify()  # lève une exception si le contenu n'est pas une image valide/décodable
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ValueError(f"Le fichier fourni n'est pas une image valide : {e}") from e


def valider_fichier_upload_django(fichier_django, taille_max_octets=TAILLE_MAX_IMAGE_OCTETS, exiger_image=True):
    """
    Même validation que valider_image ci-dessus, mais pour un fichier
    multipart déjà reçu comme UploadedFile Django (request.FILES[...]) —
    lit son contenu pour le vérifier PUIS remet le curseur à 0 (indispensable :
    sans ce seek(0), le fichier serait sauvegardé vide/tronqué juste après,
    la lecture de vérification ayant déjà consommé tout le flux).
    exiger_image=False : vérifie seulement la taille (ex: certificat_patente,
    qui accepte aussi un PDF, pas seulement une image).
    """
    fichier_django.seek(0)
    contenu = fichier_django.read()
    fichier_django.seek(0)

    if len(contenu) > taille_max_octets:
        raise ValueError(f"Le fichier dépasse la taille maximale autorisée ({taille_max_octets // (1024 * 1024)} Mo)")

    if exiger_image:
        try:
            image = Image.open(BytesIO(contenu))
            image.verify()
        except (UnidentifiedImageError, OSError, ValueError) as e:
            raise ValueError(f"Le fichier fourni n'est pas une image valide : {e}") from e
        finally:
            fichier_django.seek(0)  # Image.open a aussi consommé le flux
