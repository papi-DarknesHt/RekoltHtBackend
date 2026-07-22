from Registration.models import Token


def _get_user_from_token(request):
    """
    Résout le token du header Authorization: Token <cle> en un objet Utilisateur.
    Retourne None si le header est absent ou si le token n'existe pas en base.
    Même logique que Registration/views.py::_get_user_from_token — dupliquée ici
    (pas de module d'auth partagé dans ce projet) car utilisée par les 3 fichiers
    de vues de l'app Produits.
    """
    auth = request.headers.get('Authorization', '')
    if not auth:
        return None

    token_key = auth.replace('Token ', '')

    try:
        token = Token.objects.select_related('utilisateur').get(cle=token_key)
        return token.utilisateur
    except Token.DoesNotExist:
        return None
