from datetime import datetime, timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Count, Max
from django.db.models.functions import TruncDate

from Registration.models import VueProfilVendeur
from ..models import VueProduit
from ._auth import _get_user_from_token

NOMBRE_JOURS_PAR_DEFAUT = 7  # "7 derniers jours" par défaut (demande explicite)


def _periode_depuis_requete(request):
    """Lit date_debut/date_fin (AAAA-MM-JJ) depuis la query string — retombe
    sur les NOMBRE_JOURS_PAR_DEFAUT derniers jours si absents (défaut demandé
    explicitement). Même pattern de validation que genererRapportSignalements
    (Produits/views/signalementsViews.py)."""
    date_debut_str = request.GET.get('date_debut')
    date_fin_str = request.GET.get('date_fin')

    aujourdhui = timezone.localdate()

    if not date_debut_str and not date_fin_str:
        return aujourdhui - timedelta(days=NOMBRE_JOURS_PAR_DEFAUT - 1), aujourdhui, None

    if not date_debut_str or not date_fin_str:
        return None, None, "Les champs date_debut et date_fin (AAAA-MM-JJ) sont requis ensemble"

    try:
        date_debut = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
        date_fin = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
    except ValueError:
        return None, None, "Dates invalides, format attendu AAAA-MM-JJ"

    if date_debut > date_fin:
        return None, None, "La date de début doit précéder la date de fin"

    if date_fin > aujourdhui:
        return None, None, "La date de fin ne peut pas être dans le futur"

    return date_debut, date_fin, None


def _serie_temporelle(queryset, date_debut, date_fin):
    """Regroupe un queryset (déjà filtré) par jour et complète les jours sans
    vue à 0 — nécessaire pour un LineChart continu (AdminCharts.jsx) plutôt
    qu'une ligne qui saute les jours creux."""
    comptes_par_jour = {
        ligne['jour']: ligne['n']
        for ligne in queryset.annotate(jour=TruncDate('date_vue')).values('jour').annotate(n=Count('id'))
    }
    serie = []
    jour = date_debut
    while jour <= date_fin:
        serie.append({'date': jour.isoformat(), 'value': comptes_par_jour.get(jour, 0)})
        jour += timedelta(days=1)
    return serie


def _construire_stats_vues(date_debut, date_fin, vendeur=None):
    """Construit les statistiques de vues pour la période donnée — soit pour
    UN vendeur (tableau de bord vendeur, voir statistiquesVuesVendeur),
    soit globalement pour toute la plateforme (dashboard admin, voir
    statistiquesVuesAdmin, vendeur=None)."""
    debut_dt = timezone.make_aware(datetime.combine(date_debut, datetime.min.time()))
    fin_dt = timezone.make_aware(datetime.combine(date_fin, datetime.max.time()))

    vues_profil = VueProfilVendeur.objects.filter(date_vue__range=(debut_dt, fin_dt))
    vues_produits = VueProduit.objects.filter(date_vue__range=(debut_dt, fin_dt))
    if vendeur is not None:
        vues_profil = vues_profil.filter(vendeur=vendeur)
        vues_produits = vues_produits.filter(produit__vendeur=vendeur)

    profil = {
        'total': vues_profil.count(),
        'serie_temporelle': _serie_temporelle(vues_profil, date_debut, date_fin),
    }

    if vendeur is not None:
        # liste des personnes ayant consulté CE profil (demande explicite) —
        # n'a de sens qu'à l'échelle d'un seul vendeur, pas globalement
        visiteurs = (
            vues_profil.exclude(visiteur__isnull=True)
            .values('visiteur_id', 'visiteur__nom', 'visiteur__prenom')
            .annotate(nombre_vues=Count('id'), derniere_vue=Max('date_vue'))
            .order_by('-nombre_vues')
        )
        profil['visiteurs'] = [
            {
                'id': v['visiteur_id'],
                'nom': f"{v['visiteur__prenom']} {v['visiteur__nom']}",
                'nombre_vues': v['nombre_vues'],
                'derniere_vue': v['derniere_vue'].isoformat(),
            }
            for v in visiteurs
        ]
    else:
        # vue d'ensemble globale (admin) : top des vendeurs les plus
        # consultés à la place d'une liste de personnes (volumineux et peu
        # pertinent à l'échelle de toute la plateforme)
        top_vendeurs = (
            vues_profil.values('vendeur_id', 'vendeur__nom', 'vendeur__prenom')
            .annotate(nombre_vues=Count('id'))
            .order_by('-nombre_vues')[:10]
        )
        profil['top_vendeurs'] = [
            {'label': f"{v['vendeur__prenom']} {v['vendeur__nom']}", 'value': v['nombre_vues']}
            for v in top_vendeurs
        ]

    produits_plus_consultes = (
        vues_produits.values('produit__nom')
        .annotate(n=Count('id'))
        .order_by('-n')[:10]
    )
    categories_plus_consultees = (
        vues_produits.values('produit__categorie__nom')
        .annotate(n=Count('id'))
        .order_by('-n')[:10]
    )

    return {
        'periode': {'debut': date_debut.isoformat(), 'fin': date_fin.isoformat()},
        'profil': profil,
        'produits_plus_consultes': [
            {'label': p['produit__nom'], 'value': p['n']} for p in produits_plus_consultes
        ],
        'categories_plus_consultees': [
            {'label': c['produit__categorie__nom'], 'value': c['n']} for c in categories_plus_consultees
        ],
    }


# ── STATISTIQUES DE VUES DU VENDEUR CONNECTÉ ──────────────────────────────────
@csrf_exempt
def statistiquesVuesVendeur(request):
    """
    Statistiques de vues (profil + produits + catégories), filtrables par
    période (?date_debut=&date_fin=, AAAA-MM-JJ — défaut : 7 derniers jours)
    pour le vendeur connecté — voir TableauDeBordVendeur.jsx.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role != 'vendeur':
        return JsonResponse({'error': "Accès réservé aux vendeurs", 'error_code': 'VENDEUR_ONLY'}, status=403)

    date_debut, date_fin, erreur = _periode_depuis_requete(request)
    if erreur:
        return JsonResponse({'error': erreur, 'error_code': 'INVALID_DATE'}, status=400)

    return JsonResponse(_construire_stats_vues(date_debut, date_fin, vendeur=utilisateur), status=200)


# ── STATISTIQUES DE VUES GLOBALES (admin) ─────────────────────────────────────
@csrf_exempt
def statistiquesVuesAdmin(request):
    """
    Équivalent global (tous vendeurs confondus) de statistiquesVuesVendeur
    ci-dessus, pour le dashboard admin (voir AdminDashboard.jsx) — réservé
    au rôle admin, même pattern que dashboardAdmin (Registration/views.py).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs", 'error_code': 'ADMIN_ONLY'}, status=403)

    date_debut, date_fin, erreur = _periode_depuis_requete(request)
    if erreur:
        return JsonResponse({'error': erreur, 'error_code': 'INVALID_DATE'}, status=400)

    return JsonResponse(_construire_stats_vues(date_debut, date_fin, vendeur=None), status=200)
