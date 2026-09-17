"""
Filet de sécurité au niveau middleware (découvert lors d'un audit de sécurité,
demande explicite : "faire des tests pour voir ce qui n'est pas sécurisé").

Constat : de nombreuses vues font `Modele.objects.get(id=...)` ou
`.filter(xxx_id=...)` directement avec une valeur venue d'une query string ou
d'un corps JSON (ex: listerProduits, detailProduit, modifierProduit...),
enveloppées dans un `try/except Modele.DoesNotExist`. Si la valeur reçue
n'est PAS un entier valide (ex: `?categorie_id=' OR '1'='1`), Django lève
ValueError/TypeError AVANT même de construire une requête SQL (le champ id
est un entier — l'ORM refuse la conversion plutôt que de risquer une requête
incohérente) : ce n'est PAS une injection SQL (confirmé par test — aucune
requête SQL n'est jamais atteinte, aucune donnée n'est jamais exposée), mais
cette exception n'est PAS couverte par `except Modele.DoesNotExist`, donc
elle remontait telle quelle : une erreur 500 générique en production
(DEBUG=False, voir settings/prod.py — sans fuite d'information), mais une
page qui casse plutôt qu'une réponse propre.

Plutôt que de modifier individuellement chaque vue concernée (des dizaines,
dans plusieurs apps), ce middleware attrape ValueError/TypeError au niveau
global et répond proprement en 400 — défense en profondeur, ne remplace
jamais une validation explicite déjà présente dans une vue précise, comble
seulement les endroits qui n'en ont pas encore.
"""
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


class ExceptionJsonMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, (ValueError, TypeError)):
            logger.warning("Requête invalide (%s %s) : %s", request.method, request.path, exception)
            return JsonResponse(
                {'error': 'Requête invalide (paramètre malformé)', 'error_code': 'INVALID_QUERY_PARAM'},
                status=400,
            )
        return None  # laisse Django/les autres middlewares gérer normalement toute autre exception
