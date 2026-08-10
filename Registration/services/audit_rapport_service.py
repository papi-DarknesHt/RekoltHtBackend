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


def generer_rapport_audit(*, entrees, date_debut, date_fin, nom_admin_filtre=None, droits_par_admin=None) -> ContentFile:
    """
    Construit le rapport PDF du journal d'audit (voir JournalAudit,
    Registration/models.py) pour la période et le filtre admin choisis par le
    super admin (voir genererRapportAudit, Registration/views.py). `entrees`
    est un itérable de JournalAudit déjà filtré/trié par date — aucune requête
    supplémentaire ici, même séparation des responsabilités que
    rapport_service.py (Produits/services/). `droits_par_admin` (optionnel) :
    {admin_id: libellé des droits actuels} — affiché sous le nom de chaque
    ADM dans la colonne Admin (demande explicite).
    """
    entrees = list(entrees)
    droits_par_admin = droits_par_admin or {}

    tampon   = BytesIO()
    document = SimpleDocTemplate(
        tampon, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    # cellule de tableau, pas le corps du document — les chaînes brutes dans
    # une Table de reportlab ne passent JAMAIS à la ligne (contrairement à un
    # Paragraph) : sur une description longue, le texte déborde alors par-
    # dessus les colonnes Date/Heure au lieu de s'enrouler dans sa colonne
    style_cellule = ParagraphStyle('cellule', parent=styles['Normal'], fontSize=8, leading=10)
    elements = [
        Paragraph("Rapport d'audit — RekoltHt", styles['Title']),
        Paragraph(f"Période : {date_debut.strftime('%d/%m/%Y')} — {date_fin.strftime('%d/%m/%Y')}", styles['Heading2']),
        Paragraph(f"Filtre : {nom_admin_filtre or 'Tous les administrateurs'}", styles['Normal']),
        Paragraph(f"Généré le {timezone.now().strftime('%d/%m/%Y à %Hh%M')}", styles['Normal']),
        Spacer(1, 1 * cm),
    ]

    if entrees:
        entetes = ["Admin", "Action", "Description", "Date", "Heure"]
        lignes = []
        for e in entrees:
            libelle_droits = droits_par_admin.get(e.admin_id, '')
            cellule_admin = escape(e.nom_admin_snapshot)
            if libelle_droits:
                cellule_admin += f"<br/><font size=7 color='#7a7970'>{escape(libelle_droits)}</font>"
            lignes.append([
                Paragraph(cellule_admin, style_cellule),
                Paragraph(escape(e.action), style_cellule),
                Paragraph(escape(e.description), style_cellule),
                timezone.localtime(e.date_action).strftime('%d/%m/%Y'),
                timezone.localtime(e.date_action).strftime('%H:%M'),
            ])
        tableau = Table([entetes] + lignes, colWidths=[2.8 * cm, 3 * cm, 6.7 * cm, 2 * cm, 1.5 * cm])
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
        elements.append(Paragraph(f"{len(entrees)} action(s) au total.", styles['Normal']))
    else:
        elements.append(Paragraph("Aucune action enregistrée sur cette période.", styles['Normal']))

    document.build(elements)
    return ContentFile(tampon.getvalue())
