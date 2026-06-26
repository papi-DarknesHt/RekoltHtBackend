import base64
import json
import secrets
import requests
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import IntegrityError
from .models import Utilisateur, Profil, Entreprise, haser_password, verifier_password


# Dictionnaire en mémoire pour stocker les tokens
# clé = token, valeur = id de l'utilisateur
TOKENS = {}

# -----------------------------------Google Authntification connection -------------------
@csrf_exempt
def google_connection(request):
    """Connexion via Google — l'utilisateur doit déjà exister"""
    if request.method != "POST":
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data         = json.loads(request.body)
        google_token = data.get('token')

        if not google_token:
            return JsonResponse({'error': 'Token Google manquant'}, status=400)
         # vérifie le token auprès de Google
        google_response = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {google_token}'}
        )

        if google_response.status_code != 200:
            return JsonResponse({'error': 'Token Google invalide'}, status=401)

        google_data = google_response.json()
        email       = google_data.get('email')

        if not email:
            return JsonResponse({'error': 'Email Google non disponible'}, status=400)

        # vérifie si l'utilisateur existe
        try:
            utilisateur = Utilisateur.objects.get(email=email)
        except Utilisateur.DoesNotExist:
            # ← message clair si le compte n'existe pas
            return JsonResponse({
                'error': 'Kont sa a pa egziste. Tanpri enskri dabò.'
            }, status=404)

        # active l'utilisateur
        if not utilisateur.est_actif:
            utilisateur.modifier_est_actif()

        token         = secrets.token_hex(32)
        TOKENS[token] = utilisateur.id

        return JsonResponse({
            'message':     'Koneksyon reyisi via Google',
            'token':       token,
            'utilisateur': _serialiseUtilisateur(utilisateur),
        })
    except Exception as e:
        print("ERREUR google_connexion :", str(e))
        return JsonResponse({'error': str(e)}, status=500)

# ------------------------------------Google auth inscription-------------------
@csrf_exempt
def google_inscription(request):
    """Inscription via Google — crée un nouveau compte"""
    if request.method != "POST":
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data         = json.loads(request.body)
        google_token = data.get('token')

        if not google_token:
            return JsonResponse({'error': 'Token Google manquant'}, status=400)

        # vérifie le token auprès de Google
        google_response = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {google_token}'}
        )
        if google_response.status_code != 200:
            return JsonResponse({'error': 'Token Google invalide'}, status=401)

        google_data = google_response.json()
        email       = google_data.get('email')
        nom         = google_data.get('family_name',  'Inconnu')
        prenom      = google_data.get('given_name',   'Inconnu')

        if not email:
            return JsonResponse({'error': 'Email Google non disponible'}, status=400)
        if Utilisateur.objects.filter(email=email).exists():
            return JsonResponse({
                'error': 'Kont sa a deja egziste. Tanpri konekte.'
            }, status=400)
        utilisateur = Utilisateur.objects.create(
            nom          = nom,
            prenom       = prenom,
            email        = email,
            mot_de_passe = haser_password(secrets.token_hex(16)),
            telephone    = '',
            est_actif    = False,
        )

        # le profil est créé par le signal
        # on met juste à jour les champs supplémentaires
        profil      = utilisateur.profil
        profil.pays = 'Haiti'
        profil.role = data.get('role', 'acheteur')
        profil.latitude  = data.get('latitude', None)
        profil.longitude = data.get('longitude', None)
        profil.save()

        token         = secrets.token_hex(32)
        TOKENS[token] = utilisateur.id

        return JsonResponse({
            'message':     'Enskripsyon reyisi via Google',
            'token':       token,
            'utilisateur': _serialiseUtilisateur(utilisateur),
        }, status=201)

    except Exception as e:
        print("ERREUR google_inscription :", str(e))
        return JsonResponse({'error': str(e)}, status=500)

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
    _enregistrer_photo_profil(profil, data.get('photo_profil'))
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
        return JsonResponse({'error': 'Email n\'existe pas ou est incorrect'}, status=401)

    # vérifier le mot de passe
    if not verifier_password(data['mot_de_passe'], utilisateur.mot_de_passe):
        return JsonResponse({'error': 'Le mot de passe n\'existe pas ou incorrect'}, status=401)

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
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil

    return JsonResponse({
        'utilisateur': _serialiseUtilisateur(utilisateur),
        'profil':      _serialiseProfil(profil, request),
    }, status=200)


# ── MODIFIER UTILISATEUR ──────────────────────────────────────────────────────
@csrf_exempt
def modifierUtilisateur(request):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # mettre à jour les champs de l'utilisateur
    for champ in ['nom', 'prenom', 'email', 'telephone']:
        if champ in data:
            setattr(utilisateur, champ, data[champ])

    try:
        utilisateur.save()
    except IntegrityError:
        return JsonResponse({'error': "L'email existe déjà"}, status=400)

    return JsonResponse({
        'message':     'Utilisateur mis à jour avec succès',
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=200)


# ── MODIFIER PROFIL ───────────────────────────────────────────────────────────
@csrf_exempt
def modifierProfil(request):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # mettre à jour les champs du profil
    for champ in ['bio', 'adresse', 'commune', 'ville', 'pays', 'role', 'latitude', 'longitude']:
        if champ in data:
            setattr(profil, champ, data[champ])

    _enregistrer_photo_profil(profil, data.get('photo_profil'))

    profil.save()

    return JsonResponse({
        'message': 'Profil mis à jour avec succès',
        'profil':  _serialiseProfil(profil, request),
    }, status=200)


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


# ── ENTREPRISE — VÉRIFIER (avant inscription, sans authentification) ─────────
@csrf_exempt
def verifierEntreprise(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    nom_Entreprise     = request.GET.get('nom_Entreprise', '').strip()
    num_Enregistrement = request.GET.get('num_Enregistrement', '').strip()

    if not nom_Entreprise or not num_Enregistrement:
        return JsonResponse({'error': 'Le nom et le numéro d\'enregistrement sont requis'}, status=400)

    existe_combo = Entreprise.objects.filter(
        nom_Entreprise=nom_Entreprise,
        num_Enregistrement=num_Enregistrement,
    ).exists()
    existe_nom = Entreprise.objects.filter(nom_Entreprise=nom_Entreprise).exists()
    existe_num = Entreprise.objects.filter(num_Enregistrement=num_Enregistrement).exists()

    if existe_combo:
        message = "Cette entreprise existe déjà"
    elif existe_nom:
        message = "Le nom de l'entreprise existe déjà"
    elif existe_num:
        message = "Le numéro d'enregistrement existe déjà"
    else:
        message = None

    return JsonResponse({
        'existe':  bool(existe_combo or existe_nom or existe_num),
        'message': message,
    }, status=200)


# ── ENTREPRISE — CRÉER ────────────────────────────────────────────────────────
@csrf_exempt
def creerEntreprise(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # vérifier les champs obligatoires
    for field in ['nom_Entreprise', 'num_Enregistrement']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier si cette entreprise (même nom + même numéro) existe déjà
    if Entreprise.objects.filter(
        nom_Entreprise=data['nom_Entreprise'],
        num_Enregistrement=data['num_Enregistrement'],
    ).exists():
        return JsonResponse({'error': "Cette entreprise existe déjà"}, status=400)

    # vérifier si le nom ou le numéro d'enregistrement existe déjà séparément
    if Entreprise.objects.filter(nom_Entreprise=data['nom_Entreprise']).exists():
        return JsonResponse({'error': "Le nom de l'entreprise existe déjà"}, status=400)
    if Entreprise.objects.filter(num_Enregistrement=data['num_Enregistrement']).exists():
        return JsonResponse({'error': "Le numéro d'enregistrement existe déjà"}, status=400)

    entreprise = Entreprise.objects.create(
        proprietaire       = utilisateur,
        nom_Entreprise     = data['nom_Entreprise'],
        num_Enregistrement = data['num_Enregistrement'],
        secteur             = data.get('secteur',     'agriculture'),
        description         = data.get('description', ''),
        email               = data.get('email',       ''),
        telephone           = data.get('telephone',   ''),
        adresse             = data.get('adresse',     ''),
        commune             = data.get('commune',     ''),
        pays                = data.get('pays',        'Haiti'),
        longitude           = data.get('longitude',   None),
        latitude            = data.get('latitude',    None),
    )

    _enregistrer_logo_entreprise(entreprise, data.get('logo'))
    entreprise.save()

    return JsonResponse({
        'message':    'Entreprise créée avec succès',
        'entreprise': _serialiseEntreprise(entreprise, request),
    }, status=201)


# ── ENTREPRISE — LISTER ───────────────────────────────────────────────────────
@csrf_exempt
def listerEntreprises(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    # un admin voit toutes les entreprises créées, un utilisateur normal ne voit que les siennes
    if utilisateur.profil.role == 'admin':
        entreprises = Entreprise.objects.all()
    else:
        entreprises = utilisateur.entreprises.all()

    return JsonResponse({
        'entreprises': [_serialiseEntreprise(e, request) for e in entreprises],
    }, status=200)


# ── ENTREPRISE — MODIFIER ─────────────────────────────────────────────────────
@csrf_exempt
def modifierEntreprise(request):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': "Le champ id est requis"}, status=400)

    try:
        entreprise = utilisateur.entreprises.get(id=data['id'])
    except Entreprise.DoesNotExist:
        return JsonResponse({'error': "Entreprise introuvable"}, status=404)

    # mettre à jour les champs de l'entreprise
    for champ in ['nom_Entreprise', 'num_Enregistrement', 'secteur', 'description',
                  'email', 'telephone', 'adresse', 'commune','pays',
                  'longitude', 'latitude']:
        if champ in data:
            setattr(entreprise, champ, data[champ])

    _enregistrer_logo_entreprise(entreprise, data.get('logo'))

    try:
        entreprise.save()
    except IntegrityError:
        return JsonResponse({'error': "Le nom de l'entreprise ou le numéro d'enregistrement existe déjà"}, status=400)

    return JsonResponse({
        'message':    'Entreprise mise à jour avec succès',
        'entreprise': _serialiseEntreprise(entreprise, request),
    }, status=200)


# ── ENTREPRISE — SUPPRIMER ────────────────────────────────────────────────────
@csrf_exempt
def supprimerEntreprise(request):
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': "Le champ id est requis"}, status=400)

    try:
        entreprise = utilisateur.entreprises.get(id=data['id'])
    except Entreprise.DoesNotExist:
        return JsonResponse({'error': "Entreprise introuvable"}, status=404)

    entreprise.delete()

    return JsonResponse({'message': 'Entreprise supprimée avec succès'}, status=200)


# ── ENTREPRISE — SUPPRIMER LE LOGO ────────────────────────────────────────────
@csrf_exempt
def supprimerLogoEntreprise(request):
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    if 'id' not in data:
        return JsonResponse({'error': "Le champ id est requis"}, status=400)

    try:
        entreprise = utilisateur.entreprises.get(id=data['id'])
    except Entreprise.DoesNotExist:
        return JsonResponse({'error': "Entreprise introuvable"}, status=404)

    entreprise.supprimer_logo()

    return JsonResponse({
        'message':    'Logo supprimé avec succès',
        'entreprise': _serialiseEntreprise(entreprise, request),
    }, status=200)


# ── PROFIL — SUPPRIMER LA PHOTO ───────────────────────────────────────────────
@csrf_exempt
def supprimerPhotoProfil(request):
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil
    profil.supprimer_photo_profil()

    return JsonResponse({
        'message': 'Photo de profil supprimée avec succès',
        'profil':  _serialiseProfil(profil, request),
    }, status=200)


# ── ADMIN — LISTER LES UTILISATEURS ───────────────────────────────────────────
@csrf_exempt
def listerUtilisateursAdmin(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)
    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    utilisateurs = Utilisateur.objects.all()

    return JsonResponse({
        'utilisateurs': [
            {**_serialiseUtilisateur(u), 'role': u.profil.role}
            for u in utilisateurs
        ],
    }, status=200)


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


def _enregistrer_photo_profil(profil, photo_data):
    """Décode une image envoyée en base64 (JSON) et la sauvegarde sur le profil"""
    if not photo_data:
        return

    contenu = photo_data['content']
    # retirer le préfixe data URL si présent (ex: "data:image/png;base64,...")
    if contenu.startswith('data:'):
        contenu = contenu.split(',', 1)[1]

    fichier_binaire = base64.b64decode(contenu)
    profil.photo_profil.save(photo_data['filename'], ContentFile(fichier_binaire), save=False)


def _enregistrer_logo_entreprise(entreprise, logo_data):
    """Décode un logo envoyé en base64 (JSON) et le sauvegarde sur l'entreprise"""
    if not logo_data:
        return

    contenu = logo_data['content']
    # retirer le préfixe data URL si présent (ex: "data:image/png;base64,...")
    if contenu.startswith('data:'):
        contenu = contenu.split(',', 1)[1]

    fichier_binaire = base64.b64decode(contenu)
    entreprise.logo.save(logo_data['filename'], ContentFile(fichier_binaire), save=False)

# -----------Serialiser pour utilisateur-----------
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


def _serialiseProfil(profil, request=None):
    """Sérialise un profil en dict JSON"""
    photo_url = None
    if profil.photo_profil:
        photo_url = profil.photo_profil.url
        if request is not None:
            photo_url = request.build_absolute_uri(photo_url)

    return {
        'id':          profil.id,
        'bio':         profil.bio,
        'photo_profil': photo_url,
        'adresse':     profil.adresse,
        'commune':     profil.commune,
        'ville':       profil.ville,
        'pays':        profil.pays,
        'longitude':   profil.longitude,
        'latitude':    profil.latitude,
        'date_maj':    profil.date_maj.isoformat(),
        'role':        profil.role,
    }


# -----------Serialiser pour entreprise-----------
def _serialiseEntreprise(entreprise, request=None):
    """Sérialise une entreprise en dict JSON"""
    logo_url = None
    if entreprise.logo:
        logo_url = entreprise.logo.url
        if request is not None:
            logo_url = request.build_absolute_uri(logo_url)

    return {
        'id':                   entreprise.id,
        'proprietaire_id':      entreprise.proprietaire_id,
        'nom_Entreprise':       entreprise.nom_Entreprise,
        'num_Enregistrement':   entreprise.num_Enregistrement,
        'secteur':              entreprise.secteur,
        'description':          entreprise.description,
        'email':                entreprise.email,
        'telephone':            entreprise.telephone,
        'adresse':              entreprise.adresse,
        'commune':              entreprise.commune,
        'pays':                 entreprise.pays,
        'logo':                 logo_url,
        'longitude':            entreprise.longitude,
        'latitude':             entreprise.latitude,
        'est_verifiee':         entreprise.est_verifiee,
        'statut_verification':  entreprise.statut_verification,
        'date_creation':        entreprise.date_creation.isoformat(),
        'date_maj':             entreprise.date_maj.isoformat(),
    }