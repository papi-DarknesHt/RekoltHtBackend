# ── IMPORTS ───────────────────────────────────────────────────────────────────
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import HorizontalBarChart

# même palette catégorielle validée (accessibilité daltonisme) que les
# graphiques du tableau de bord côté frontend — voir assets/CSS/Charts.css
# et le skill dataviz (palette.md, slots 1/2 clairs)
COULEUR_VUES     = colors.HexColor('#2a78d6')
COULEUR_CONTACTS = colors.HexColor('#eb6834')

NOMBRE_PRODUITS_GRAPHE = 8


def _construire_graphe_barres(*, titre, categories, valeurs, couleur):
    """
    Construit un diagramme en bâton horizontal (reportlab.graphics, aucune
    dépendance supplémentaire) — même principe que HistogramChart côté
    frontend (AdminCharts.jsx) : une seule teinte, magnitude par catégorie.
    Retourne None si aucune valeur (évite un graphe vide illisible).
    """
    if not valeurs or max(valeurs) == 0:
        return None

    hauteur = max(24 * len(categories), 60)
    dessin = Drawing(430, hauteur + 30)

    graphe = HorizontalBarChart()
    graphe.x = 110
    graphe.y = 10
    graphe.height = hauteur
    graphe.width = 300
    graphe.data = [valeurs]
    graphe.categoryAxis.categoryNames = categories
    graphe.categoryAxis.labels.fontSize = 7
    graphe.valueAxis.valueMin = 0
    graphe.valueAxis.labels.fontSize = 7
    graphe.bars[0].fillColor = couleur
    graphe.barLabels.fontSize = 7
    graphe.barLabelFormat = '%d'
    graphe.barLabels.nudge = 7

    dessin.add(graphe)
    return dessin


def generer_rapport_vendeur(*, vendeur, nom_affiche, produits, nombre_vues_profil) -> ContentFile:
    """
    Construit le rapport statistique PDF d'un vendeur (accessible depuis le
    tableau de bord vendeur, voir statistiquesVendeurPdf, Produits/views/
    produitsViews.py) : résumé chiffré, diagrammes des produits les plus
    consultés/contactés, tableau détaillé de tous les produits.

    `produits` : itérable de Produits déjà filtré sur ce vendeur (voir
    mesProduits, Produits/views/produitsViews.py) — aucune requête
    supplémentaire n'est faite ici, même séparation des responsabilités que
    contrat_service.py (Registration/services/).
    """
    produits = list(produits)
    total_produits    = len(produits)
    total_disponibles = sum(1 for p in produits if p.est_disponible)
    total_vues        = sum(p.nombre_vues for p in produits)
    total_contacts    = sum(p.nombre_contacts for p in produits)

    tampon   = BytesIO()
    document = SimpleDocTemplate(
        tampon, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles   = getSampleStyleSheet()
    elements = [
        Paragraph("Rapport statistique — RekoltHt", styles['Title']),
        Paragraph(nom_affiche, styles['Heading2']),
        Paragraph(f"Généré le {timezone.now().strftime('%d/%m/%Y à %Hh%M')}", styles['Normal']),
        Spacer(1, 1 * cm),
    ]

    # ── résumé chiffré ────────────────────────────────────────────────────
    donnees_resume = [
        ["Produits publiés", str(total_produits)],
        ["Produits disponibles", str(total_disponibles)],
        ["Vues cumulées (produits)", str(total_vues)],
        ["Contacts reçus (cumulés)", str(total_contacts)],
        ["Vues du profil", str(nombre_vues_profil)],
    ]
    tableau_resume = Table(donnees_resume, colWidths=[9 * cm, 4 * cm])
    tableau_resume.setStyle(TableStyle([
        ('FONTSIZE',    (0, 0), (-1, -1), 10),
        ('TEXTCOLOR',   (0, 0), (0, -1), colors.HexColor('#52514e')),
        ('FONTNAME',    (1, 0), (1, -1), 'Helvetica-Bold'),
        ('LINEBELOW',   (0, 0), (-1, -2), 0.5, colors.HexColor('#e1e0d9')),
        ('TOPPADDING',  (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(tableau_resume)
    elements.append(Spacer(1, 1 * cm))

    # ── produits les plus consultés / contactés ──────────────────────────
    plus_vus      = sorted(produits, key=lambda p: p.nombre_vues, reverse=True)[:NOMBRE_PRODUITS_GRAPHE]
    plus_contactes = sorted(produits, key=lambda p: p.nombre_contacts, reverse=True)[:NOMBRE_PRODUITS_GRAPHE]

    graphe_vues = _construire_graphe_barres(
        titre="Produits les plus consultés",
        categories=[p.nom[:28] for p in reversed(plus_vus)],
        valeurs=[p.nombre_vues for p in reversed(plus_vus)],
        couleur=COULEUR_VUES,
    )
    if graphe_vues is not None:
        elements.append(Paragraph("Produits les plus consultés", styles['Heading2']))
        elements.append(graphe_vues)
        elements.append(Spacer(1, 0.8 * cm))

    graphe_contacts = _construire_graphe_barres(
        titre="Produits les plus contactés",
        categories=[p.nom[:28] for p in reversed(plus_contactes)],
        valeurs=[p.nombre_contacts for p in reversed(plus_contactes)],
        couleur=COULEUR_CONTACTS,
    )
    if graphe_contacts is not None:
        elements.append(Paragraph("Produits les plus contactés", styles['Heading2']))
        elements.append(graphe_contacts)
        elements.append(Spacer(1, 1 * cm))

    # ── tableau détaillé de tous les produits ────────────────────────────
    if produits:
        elements.append(Paragraph("Détail par produit", styles['Heading2']))
        entetes = ["Produit", "Disponible", "Vues", "Contacts"]
        lignes = [
            [p.nom[:40], "Oui" if p.est_disponible else "Non", str(p.nombre_vues), str(p.nombre_contacts)]
            for p in sorted(produits, key=lambda p: p.nombre_vues, reverse=True)
        ]
        tableau_produits = Table([entetes] + lignes, colWidths=[8 * cm, 3 * cm, 2 * cm, 2 * cm])
        tableau_produits.setStyle(TableStyle([
            ('FONTSIZE',      (0, 0), (-1, -1), 9),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor('#f6f4f1')),
            ('LINEBELOW',     (0, 0), (-1, 0), 0.75, colors.HexColor('#c3c2b7')),
            ('LINEBELOW',     (0, 1), (-1, -1), 0.25, colors.HexColor('#e1e0d9')),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(tableau_produits)

    document.build(elements)
    return ContentFile(tampon.getvalue())
