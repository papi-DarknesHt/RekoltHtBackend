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


def generer_rapport_support(*, entrees, date_debut, date_fin, nom_admin_filtre=None) -> ContentFile:
    """
    Construit le rapport PDF d'audit des messages support déjà répondus
    (MessageSupport, Messagerie/models.py) pour la période et le filtre admin
    choisis — réservé à "Tous les droits"/le propriétaire (voir
    genererRapportSupport, Messagerie/views.py). Même structure que
    Registration/services/audit_rapport_service.py, mais montre le CONTENU du
    message et de la réponse (demande explicite), pas juste une description
    d'action. `entrees` : liste de dicts déjà préparés côté vue (contenu/
    réponse déjà déchiffrés quand c'est possible côté serveur — voir
    _champAffiche) — aucune requête ni déchiffrement ici.
    """
    entrees = list(entrees)

    tampon   = BytesIO()
    document = SimpleDocTemplate(
        tampon, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    # cellule de tableau — un Paragraph s'enroule dans sa colonne, contrairement
    # à une chaîne brute dans une Table (voir audit_rapport_service.py)
    style_cellule = ParagraphStyle('cellule', parent=styles['Normal'], fontSize=8, leading=10)
    elements = [
        Paragraph("Rapport d'audit — Support RekoltHt", styles['Title']),
        Paragraph(f"Période : {date_debut.strftime('%d/%m/%Y')} — {date_fin.strftime('%d/%m/%Y')}", styles['Heading2']),
        Paragraph(f"Filtre : {nom_admin_filtre or 'Tous les administrateurs'}", styles['Normal']),
        Paragraph(f"Généré le {timezone.now().strftime('%d/%m/%Y à %Hh%M')}", styles['Normal']),
        Spacer(1, 1 * cm),
    ]

    if entrees:
        entetes = ["Admin", "Utilisateur", "Message", "Réponse", "Date"]
        lignes = []
        for e in entrees:
            cellule_admin = escape(e['admin_nom'])
            if e.get('admin_email'):
                cellule_admin += f"<br/><font size=7 color='#7a7970'>{escape(e['admin_email'])}</font>"
            lignes.append([
                Paragraph(cellule_admin, style_cellule),
                Paragraph(escape(e['vendeur_nom']), style_cellule),
                Paragraph(escape(e['contenu']), style_cellule),
                Paragraph(escape(e['reponse']), style_cellule),
                Paragraph(
                    f"Envoyé : {timezone.localtime(e['date_envoi']).strftime('%d/%m/%Y %H:%M')}"
                    f"<br/>Répondu : {timezone.localtime(e['date_reponse']).strftime('%d/%m/%Y %H:%M')}",
                    style_cellule,
                ),
            ])
        tableau = Table([entetes] + lignes, colWidths=[2.5 * cm, 2.5 * cm, 4.5 * cm, 4.5 * cm, 3 * cm])
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
        elements.append(Paragraph(f"{len(entrees)} message(s) au total.", styles['Normal']))
    else:
        elements.append(Paragraph("Aucun message répondu sur cette période.", styles['Normal']))

    document.build(elements)
    return ContentFile(tampon.getvalue())
