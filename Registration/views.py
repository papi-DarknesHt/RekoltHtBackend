import json
import secrets
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Utilisateur, Profil,  haser_password, verifier_password


# Dictionnaire en mémoire pour stocker les tokens
# clé = token, valeur = id de l'utilisateur
TOKENS = {}


# ──-------- INSCRIPTION ───────────────────────────────────────────────────────────────
@csrf_exempt
def sinscrire(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    data = json.loads(request.body)

    # vérifier les champs obligatoires
    for field in ['nom', 'prenom', 'email', 'mot_de_passe', 'telephone']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier si l'email existe déjà
    if Utilisateur.objects.filter(email=data['email']).exists():
        return JsonResponse({'error': "L'email existe déjà"}, status=400)

    # créer l'utilisateur
    utilisateur = Utilisateur.objects.create(
        nom          = data['nom'],
        prenom       = data['prenom'],
        email        = data['email'],
        mot_de_passe = haser_password(data['mot_de_passe']),
        telephone    = data['telephone'],
        est_actif    = False,
    )

    # le profil est créé automatiquement par le signal
    # on met à jour les champs supplémentaires
    profil          = utilisateur.profil
    profil.bio      = data.get('bio', '')
    photo_profil    = data.get('photo_profil', None)
    if photo_profil:
        profil.photo_profil.save(photo_profil['filename'], photo_profil['content'], save=False)
    profil.adresse  = data.get('adresse', '')
    profil.commune  = data.get('commune', '')
    profil.ville    = data.get('ville',   '')
    profil.pays     = data.get('pays',    'Haiti')
    profil.role     = data.get('role',    'acheteur')
    profil.latitude  = data.get('latitude', None)
    profil.longitude = data.get('longitude', None)
    profil.save()

    # créer le token
    token         = secrets.token_hex(32)
    TOKENS[token] = utilisateur.id

    return JsonResponse({
        'message':     'Utilisateur inscrit avec succès',
        'token':       token,
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=201)


# ── CONNEXION ─────────────────────────────────────────────────────────────────
@csrf_exempt
def seConnecter(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    data = json.loads(request.body)

    # vérifier les champs obligatoires
    for field in ['email', 'mot_de_passe']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier si l'utilisateur existe
    try:
        utilisateur = Utilisateur.objects.get(email=data['email'])
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Email ou mot de passe incorrect'}, status=401)

    # vérifier le mot de passe
    if not verifier_password(data['mot_de_passe'], utilisateur.mot_de_passe):
        return JsonResponse({'error': 'Email ou mot de passe incorrect'}, status=401)

    # activer l'utilisateur via la méthode du modèle
    if not utilisateur.est_actif:
        utilisateur.modifier_est_actif()

    # créer le token
    token         = secrets.token_hex(32)
    TOKENS[token] = utilisateur.id

    return JsonResponse({
        'message':     'Utilisateur connecté avec succès',
        'token':       token,
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=200)


# ── DÉCONNEXION ───────────────────────────────────────────────────────────────
@csrf_exempt
def seDeconnecter(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification invalide"}, status=401)

    # désactiver l'utilisateur via la méthode du modèle
    if utilisateur.est_actif:
        utilisateur.modifier_est_actif()

    # supprimer le token
    token_key = request.headers.get('Authorization', '').replace('Token ', '')
    TOKENS.pop(token_key, None)

    return JsonResponse({'message': 'Utilisateur déconnecté avec succès'}, status=200)


# ── PROFIL ────────────────────────────────────────────────────────────────────
@csrf_exempt
def profilAfficher(request):

    # vérifier le token sur toutes les méthodes
    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil

    # ── GET — retourner le profil ──
    if request.method == 'GET':
        return JsonResponse({
            'utilisateur': _serialiseUtilisateur(utilisateur),
            'profil':      _serialiseProfil(profil),
        }, status=200)

    # ── PUT — mettre à jour le profil ──
    if request.method == 'PUT':
        data = json.loads(request.body)

        # mettre à jour les champs de l'utilisateur
        for champ in ['nom', 'prenom', 'email', 'telephone']:
            if champ in data:
                setattr(utilisateur, champ, data[champ])
        utilisateur.save()

        # mettre à jour les champs du profil
        for champ in ['bio', 'adresse', 'commune', 'ville', 'pays', 'role', 'latitude', 'longitude']:
            if champ in data:
                setattr(profil, champ, data[champ])
        profil.save()

        return JsonResponse({
            'message':     'Profil mis à jour avec succès',
            'utilisateur': _serialiseUtilisateur(utilisateur),
            'profil':      _serialiseProfil(profil),
        }, status=200)

    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


# ── MODIFIER MOT DE PASSE ─────────────────────────────────────────────────────
@csrf_exempt
def modifierMotDePasse(request):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    data = json.loads(request.body)

    # vérifier les champs obligatoires
    for field in ['ancien_mot_de_passe', 'nouveau_mot_de_passe']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier l'ancien mot de passe
    if not verifier_password(data['ancien_mot_de_passe'], utilisateur.mot_de_passe):
        return JsonResponse({'error': 'Ancien mot de passe incorrect'}, status=401)

    # modifier via la méthode du modèle
    utilisateur.modifier_mot_de_passe(data['nouveau_mot_de_passe'])

    return JsonResponse({'message': 'Mot de passe modifié avec succès'}, status=200)


# ── FONCTIONS UTILITAIRES ─────────────────────────────────────────────────────
def _get_user_from_token(request):
    """Récupère l'utilisateur depuis le header Authorization: Token xxx"""
    auth = request.headers.get('Authorization', '')
    if not auth:
        return None

    token_key = auth.replace('Token ', '')
    user_id   = TOKENS.get(token_key)

    if not user_id:
        return None

    try:
        return Utilisateur.objects.get(id=user_id)
    except Utilisateur.DoesNotExist:
        return None


def _serialiseUtilisateur(utilisateur):
    """Sérialise un utilisateur en dict JSON"""
    return {
        'id':               utilisateur.id,
        'nom':              utilisateur.nom,
        'prenom':           utilisateur.prenom,
        'email':            utilisateur.email,
        'telephone':        utilisateur.telephone,
        'est_actif':        utilisateur.est_actif,
        'date_inscription': utilisateur.date_inscription.isoformat(),
    }


def _serialiseProfil(profil):
    """Sérialise un profil en dict JSON"""
    return {
        'id':          profil.id,
        'bio':         profil.bio,
        'photo_profil': profil.photo_profil.url if profil.photo_profil else None,
        'adresse':     profil.adresse,
        'commune':     profil.commune,
        'ville':       profil.ville,
        'pays':        profil.pays,
        'longitude':   profil.longitude,
        'latitude':    profil.latitude,
        'date_maj':    profil.date_maj.isoformat(),
        'role':        profil.role,
    }