# ── IMPORTS ───────────────────────────────────────────────────────────────────
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# import direct (pas de risque circulaire ici : ce module n'est importé que
# paresseusement par DemandeVerification.marquer_verifie() et par les vues de
# prévisualisation, bien après que Registration.models soit entièrement
# chargé — voir Registration/models.py)
from ..models import DemandeVerification, Entreprise


# ── CONDITIONS GÉNÉRALES (texte statique, placeholder) ────────────────────────
CONDITIONS_GENERALES = (
    "Le présent contrat atteste que le vendeur identifié ci-dessus a été "
    "vérifié par la plateforme RekoltHt conformément à sa politique de "
    "vérification d'identité (KYC). En utilisant la plateforme, le vendeur "
    "s'engage à fournir des informations exactes sur les produits proposés, "
    "à respecter les lois en vigueur en République d'Haïti, et à traiter les "
    "acheteurs avec loyauté. RekoltHt se réserve le droit de suspendre tout "
    "compte en cas de manquement à ces engagements."
)

# libellés lisibles des types de pièce (DemandeVerification.TYPE_DOCUMENT)
TYPE_DOCUMENT_LIBELLES = {
    'passeport': 'Passeport',
    'permis':    'Permis de conduire',
    'cin':       "Carte d'identité nationale",
}


def _lire_fichier(fichier):
    """
    Lit un fichier (FieldField déjà sauvegardé OU UploadedFile en mémoire, pas
    encore associé à un modèle — voir generer_apercu_contrat) en BytesIO.
    Ne dépend jamais de .path : indisponible sur un stockage externe
    (Cloudinary en prod, voir BackendRekoltHt/settings/prod.py) et de toute
    façon inexistant pour un fichier tout juste reçu dans request.FILES.
    """
    if not fichier:
        return None
    fichier.seek(0)
    return BytesIO(fichier.read())


def _est_pdf(fichier):
    """Vrai si le nom du fichier se termine en .pdf (certificat_patente peut être une image OU un PDF)."""
    nom = getattr(fichier, 'name', '') or ''
    return nom.lower().endswith('.pdf')


def _construire_contrat_pdf(*, nom_affiche, type_piece_libelle, numero_piece, photo_identite, photo_document, document_est_pdf):
    """
    Construit le PDF du contrat vendeur : photo d'identité (selfie ou logo
    entreprise), identité, type de pièce fournie + son numéro, photo du
    document lui-même (sauf si PDF — non embarquable comme image), date,
    conditions générales, et un placeholder de signature électronique.
    """
    tampon   = BytesIO()
    document = SimpleDocTemplate(
        tampon, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles   = getSampleStyleSheet()
    elements = [
        Paragraph("Contrat de vérification vendeur — RekoltHt", styles['Title']),
        Spacer(1, 1 * cm),
    ]

    if photo_identite is not None:
        elements.append(RLImage(photo_identite, width=3 * cm, height=3 * cm))
        elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph(f"Nom : {nom_affiche}", styles['Normal']))
    elements.append(Paragraph(f"Type de pièce fournie : {type_piece_libelle}", styles['Normal']))
    elements.append(Paragraph(f"Numéro : {numero_piece or '(non renseigné)'}", styles['Normal']))
    elements.append(Paragraph(f"Date : {timezone.now().strftime('%d/%m/%Y')}", styles['Normal']))
    elements.append(Spacer(1, 1 * cm))

    if photo_document is not None and not document_est_pdf:
        elements.append(Paragraph("Document fourni", styles['Heading2']))
        elements.append(RLImage(photo_document, width=10 * cm, height=6.5 * cm, kind='bound'))
        elements.append(Spacer(1, 1 * cm))

    elements.append(Paragraph("Conditions générales", styles['Heading2']))
    elements.append(Paragraph(CONDITIONS_GENERALES, styles['Normal']))
    elements.append(Spacer(1, 1.5 * cm))

    # placeholder de signature électronique : aucune signature manuscrite/
    # cryptographique n'est capturée pour l'instant, seule la date fait foi
    elements.append(Paragraph(
        f"Signé électroniquement le {timezone.now().strftime('%d/%m/%Y à %Hh%M')}",
        styles['Normal'],
    ))

    document.build(elements)
    return ContentFile(tampon.getvalue())


def generer_contrat(demande: DemandeVerification) -> ContentFile:
    """
    Construit le contrat vendeur (PDF) à partir d'une DemandeVerification déjà
    sauvegardée (fichiers accessibles via les FieldFile du modèle). Retourne
    un ContentFile prêt à assigner à demande.contrat_pdf, sur le même principe
    que _enregistrer_photo_profil (Registration/views.py) : à l'appelant de
    faire demande.contrat_pdf.save(nom_fichier, contenu, save=False) puis .save().
    """
    if demande.type_demandeur == 'entreprise':
        entreprise = Entreprise.objects.get(pk=demande.utilisateur_id)
        return _construire_contrat_pdf(
            nom_affiche         = entreprise.nom_Entreprise,
            type_piece_libelle  = "Certificat de patente",
            numero_piece        = demande.numero_piece_saisi or demande.numero_patente_extrait or entreprise.num_Enregistrement,
            photo_identite      = _lire_fichier(entreprise.logo),
            photo_document      = _lire_fichier(demande.certificat_patente),
            document_est_pdf    = _est_pdf(demande.certificat_patente),
        )
    return _construire_contrat_pdf(
        nom_affiche         = f"{demande.utilisateur.prenom} {demande.utilisateur.nom}".strip(),
        type_piece_libelle  = TYPE_DOCUMENT_LIBELLES.get(demande.type_document, demande.type_document or ''),
        numero_piece        = demande.numero_piece_saisi or demande.numero_piece_extrait,
        photo_identite      = _lire_fichier(demande.selfie),
        photo_document      = _lire_fichier(demande.document_recto),
        document_est_pdf    = False,   # document_recto est toujours un ImageField
    )


def generer_apercu_contrat(*, type_demandeur, nom_affiche, type_document, numero_piece_saisi,
                            fichier_identite, fichier_document, document_est_pdf=False) -> ContentFile:
    """
    Construit un contrat de PRÉVISUALISATION avant toute soumission finale —
    à partir des fichiers bruts reçus dans request.FILES (jamais sauvegardés
    en base), pour que l'utilisateur puisse voir le contrat avant d'envoyer sa
    demande (voir vue previsualiser_contrat). Ne dépend d'aucune
    DemandeVerification : mêmes données que soumettre_verification recevrait,
    mais rien n'est persisté.
    """
    libelle = "Certificat de patente" if type_demandeur == 'entreprise' else TYPE_DOCUMENT_LIBELLES.get(type_document, type_document or '')
    return _construire_contrat_pdf(
        nom_affiche         = nom_affiche,
        type_piece_libelle  = libelle,
        numero_piece        = numero_piece_saisi,
        photo_identite      = _lire_fichier(fichier_identite),
        photo_document      = _lire_fichier(fichier_document),
        document_est_pdf    = document_est_pdf,
    )
