# ── IMPORTS ───────────────────────────────────────────────────────────────────
from io import BytesIO
from xml.sax.saxutils import escape

from django.core.files.base import ContentFile
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def generer_rapport_signalements(*, entrees, date_debut, date_fin, nom_admin_filtre=None) -> ContentFile:
    """
    Construit le rapport PDF d'audit des signalements déjà traités (produits,
    vendeurs, messages, avis confondus — voir genererRapportSignalements,
    Produits/views/signalementsViews.py) pour la période et le filtre admin
    choisis — réservé à "Tous les droits"/le propriétaire. Même structure que
    Messagerie/services/rapport_support_service.py, mais une colonne "Type"
    distingue les 4 catégories de signalement. Chaque entrée porte aussi
    l'explication saisie par l'admin au moment du traitement (voir
    explication_decision, modèles *Signalement*) — colonne "Explication".
    `entrees` : liste de dicts déjà préparés côté vue, aucune requête ici.
    """
    entrees = list(entrees)

    tampon   = BytesIO()
    document = SimpleDocTemplate(
        tampon, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    style_cellule = ParagraphStyle('cellule', parent=styles['Normal'], fontSize=8, leading=10)
    elements = [
        Paragraph("Rapport d'audit — Signalements RekoltHt", styles['Title']),
        Paragraph(f"Période : {date_debut.strftime('%d/%m/%Y')} — {date_fin.strftime('%d/%m/%Y')}", styles['Heading2']),
        Paragraph(f"Filtre : {nom_admin_filtre or 'Tous les administrateurs'}", styles['Normal']),
        Paragraph(f"Généré le {timezone.now().strftime('%d/%m/%Y à %Hh%M')}", styles['Normal']),
        Spacer(1, 1 * cm),
    ]

    if entrees:
        entetes = ["Type", "Cible", "Motif", "Explication", "Traité par", "Date"]
        lignes = []
        for e in entrees:
            cellule_admin = escape(e['admin_nom'])
            if e.get('admin_email'):
                cellule_admin += f"<br/><font size=7 color='#7a7970'>{escape(e['admin_email'])}</font>"
            lignes.append([
                Paragraph(escape(e['type_libelle']), style_cellule),
                Paragraph(escape(e['cible']), style_cellule),
                Paragraph(f"<b>{escape(e['type_probleme_libelle'])}</b><br/>{escape(e['motif'])}", style_cellule),
                Paragraph(escape(e.get('explication') or '—'), style_cellule),
                Paragraph(cellule_admin, style_cellule),
                Paragraph(
                    f"Signalé : {timezone.localtime(e['date_signalement']).strftime('%d/%m/%Y %H:%M')}"
                    f"<br/>Traité : {timezone.localtime(e['date_traitement']).strftime('%d/%m/%Y %H:%M')}",
                    style_cellule,
                ),
            ])
        tableau = Table([entetes] + lignes, colWidths=[1.8 * cm, 3 * cm, 4 * cm, 3.7 * cm, 2.4 * cm, 2.6 * cm])
        tableau.setStyle(TableStyle([
            ('FONTSIZE',      (0, 0), (-1, -1), 8),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor('#f6f4f1')),
            ('LINEBELOW',     (0, 0), (-1, 0), 0.75, colors.HexColor('#c3c2b7')),
            ('LINEBELOW',     (0, 1), (-1, -1), 0.25, colors.HexColor('#e1e0d9')),
            ('TOPPADDING',    (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(tableau)
        elements.append(Spacer(1, 0.5 * cm))
        elements.append(Paragraph(f"{len(entrees)} signalement(s) au total.", styles['Normal']))
    else:
        elements.append(Paragraph("Aucun signalement traité sur cette période.", styles['Normal']))

    document.build(elements)
    return ContentFile(tampon.getvalue())
