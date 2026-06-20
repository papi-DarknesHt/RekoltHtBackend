import base64
import json
import secrets
import requests
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import IntegrityError
from .models import Utilisateur, Profil,  haser_password, verifier_password


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

    # Pré-validation pour inscription en tant qu'entreprise (faire avant de créer l'utilisateur)
    type_vendeur_supplied = data.get('type_vendeur')
    prepared_piece_for_signup = None
    if type_vendeur_supplied is not None:
        if type_vendeur_supplied not in ('individu', 'entreprise'):
            return JsonResponse({'error': 'type_vendeur invalide'}, status=400)

        if type_vendeur_supplied == 'entreprise':
            # nom_entreprise requis
            if not data.get('nom_entreprise'):
                return JsonResponse({'error': 'nom_entreprise est requis pour un compte entreprise.'}, status=400)

            # piece_justificative requis et validé via la fonction existante
            piece_data = data.get('piece_justificative')
            if not piece_data:
                return JsonResponse({'error': 'Une pièce justificative est requise pour un compte entreprise.'}, status=400)

            prepared = _valider_et_preparer_piece(piece_data)
            if isinstance(prepared, JsonResponse):
                # propager l'erreur sans créer d'utilisateur
                return prepared
            prepared_piece_for_signup = prepared

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

    # Si l'inscription demandait la création d'un compte entreprise, créer
    # l'objet Entreprise maintenant que le profil est sauvé. Nous utilisons
    # prepared_piece_for_signup qui a été validé avant la création de
    # l'utilisateur pour éviter les comptes orphelins en cas d'erreur.
    if type_vendeur_supplied == 'entreprise':
        # appeler la nouvelle méthode du modèle pour créer l'entreprise
        profil.creer_compte_entreprise(data.get('nom_entreprise'), prepared_piece_for_signup)
    elif type_vendeur_supplied == 'individu':
        profil.type_vendeur = 'individu'
        profil.save()

    # créer le token
    token         = secrets.token_hex(32)
    TOKENS[token] = utilisateur.id

    return JsonResponse({
        'message':     'Utilisateur inscrit avec succès',
        'token':       token,
        'utilisateur': _serialiseUtilisateur(utilisateur),
        'profil':      _serialiseProfil(profil, request, utilisateur),
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
        # message générique pour éviter de divulguer l'existence d'un email
        return JsonResponse({'error': 'Email ou mot de passe incorrect'}, status=401)

    # vérifier le mot de passe
    if not verifier_password(data['mot_de_passe'], utilisateur.mot_de_passe):
        # même message générique
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
    # Ce endpoint gère GET (affichage) et PUT (mise à jour) pour compatibilité
    if request.method not in ('GET', 'PUT'):
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil

    # GET: retourner le profil
    if request.method == 'GET':
        return JsonResponse({
            'utilisateur': _serialiseUtilisateur(utilisateur),
            'profil':      _serialiseProfil(profil, request, utilisateur),
        }, status=200)

    # PUT: mise à jour (comme l'ancien modifierProfil)
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

    # mettre à jour les champs du profil (ne pas accepter role/statut depuis le client)
    for champ in ['bio', 'adresse', 'commune', 'ville', 'pays', 'latitude', 'longitude']:
        if champ in data:
            setattr(profil, champ, data[champ])

    _enregistrer_photo_profil(profil, data.get('photo_profil'))

    profil.save()

    return JsonResponse({
        'message': 'Profil mis à jour avec succès',
        'utilisateur': _serialiseUtilisateur(utilisateur),
        'profil':  _serialiseProfil(profil, request, utilisateur),
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

    # mettre à jour les champs du profil (ne pas permettre au client de modifier 'role')
    for champ in ['bio', 'adresse', 'commune', 'ville', 'pays', 'latitude', 'longitude']:
        if champ in data:
            setattr(profil, champ, data[champ])

    _enregistrer_photo_profil(profil, data.get('photo_profil'))

    profil.save()

    return JsonResponse({
        'message': 'Profil mis à jour avec succès',
        'profil':  _serialiseProfil(profil, request, utilisateur),
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


def _valider_et_preparer_piece(piece_data):
    """
    Valide le fichier envoyé en base64 (vérifie extension et taille) et retourne
    (filename, ContentFile(binaire)) si valide, sinon lève JsonResponse.
    """
    if not piece_data:
        return None

    contenu = piece_data.get('content')
    filename = piece_data.get('filename')

    if not contenu or not filename:
        return JsonResponse({'error': 'Données de fichier incomplètes'}, status=400)

    # extraire le préfixe data: si présent
    if contenu.startswith('data:'):
        try:
            contenu = contenu.split(',', 1)[1]
        except Exception:
            return JsonResponse({'error': 'Format de contenu fichier invalide'}, status=400)

    try:
        fichier_binaire = base64.b64decode(contenu)
    except Exception:
        return JsonResponse({'error': 'Contenu base64 invalide'}, status=400)

    # Taille maximale 5 Mo
    if len(fichier_binaire) > 5 * 1024 * 1024:
        return JsonResponse({'error': 'Le fichier dépasse la taille maximale autorisée (5 Mo).'}, status=400)

    # Vérifier extension
    allowed_ext = ['.pdf', '.jpg', '.jpeg', '.png']
    lower = filename.lower()
    if not any(lower.endswith(ext) for ext in allowed_ext):
        return JsonResponse({'error': 'Format de fichier non autorisé. Formats acceptés : PDF, JPG, PNG.'}, status=400)

    return (filename, ContentFile(fichier_binaire))

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


def _serialiseProfil(profil, request=None, utilisateur_courant=None):
    """Sérialise un profil en dict JSON.

    La pièce justificative n'est incluse QUE si l'utilisateur courant est le
    propriétaire du profil ou si l'utilisateur courant est admin.
    """
    photo_url = None
    if profil.photo_profil:
        photo_url = profil.photo_profil.url
        if request is not None:
            photo_url = request.build_absolute_uri(photo_url)

    # Gestion de la visibilité du document sensible via la relation entreprise
    entreprise = getattr(profil, 'entreprise', None)

    nom_entreprise = entreprise.nom_entreprise if entreprise else None
    statut_verification = entreprise.statut_verification if entreprise else 'non_requis'

    piece_url = None
    if entreprise and entreprise.piece_justificative:
        inclure_document = False
        if utilisateur_courant is not None:
            try:
                if utilisateur_courant.id == profil.utilisateur.id:
                    inclure_document = True
                elif getattr(utilisateur_courant.profil, 'role', None) == 'admin':
                    inclure_document = True
            except Exception:
                inclure_document = False

        if inclure_document:
            piece_url = entreprise.piece_justificative.url
            if request is not None:
                piece_url = request.build_absolute_uri(piece_url)

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
        'type_vendeur': profil.type_vendeur,
        'nom_entreprise': nom_entreprise,
        'statut_verification': statut_verification,
        'piece_justificative': piece_url,
    }


@csrf_exempt
def devenirVendeur(request):
    """Endpoint pour que l'utilisateur devienne vendeur (individu ou entreprise)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': 'Token d\'authentification requis'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    type_vendeur = data.get('type_vendeur')
    if type_vendeur not in ('individu', 'entreprise'):
        return JsonResponse({'error': 'type_vendeur invalide'}, status=400)

    profil = utilisateur.profil

    # Ne jamais prendre statut_verification ni role depuis le client — on l'ignore

    # Cas individu : suppression éventuelle de l'ancien document
    if type_vendeur == 'individu':
        # on supprime toute pièce justificative si existante
        profil.soumettre_demande_vendeur('individu')
        return JsonResponse({'message': 'Devenu vendeur (individu) avec succès', 'profil': _serialiseProfil(profil, request, utilisateur)}, status=200)

    # Cas entreprise
    entreprise_existante = getattr(profil, 'entreprise', None)

    # déterminer nom_entreprise final (peut provenir du body ou de l'objet Entreprise existant)
    nom_entreprise_final = data.get('nom_entreprise') or (entreprise_existante.nom_entreprise if entreprise_existante else None)

    # vérifier présence d'une pièce dans le JSON
    piece_data = data.get('piece_justificative')

    # première soumission : ni entreprise existante avec document, ni nouveau document fourni
    if not (entreprise_existante and entreprise_existante.piece_justificative) and not piece_data:
        return JsonResponse({'error': 'Une pièce justificative est requise pour un compte entreprise.'}, status=400)

    # si nom entreprise manquant
    if not nom_entreprise_final:
        return JsonResponse({'error': 'nom_entreprise est requis pour un compte entreprise.'}, status=400)

    # préparer nouveau_fichier si fourni
    nouveau_fichier = None
    if piece_data:
        prepared = _valider_et_preparer_piece(piece_data)
        # si la validation a retourné un JsonResponse (erreur), le propager
        if isinstance(prepared, JsonResponse):
            return prepared
        nouveau_fichier = prepared

    # appeler la méthode du modèle; elle gère suppression de l'ancien fichier si un
    # nouveau est fourni, et met le statut à en_attente si nouveau_fichier fourni
    profil.soumettre_demande_vendeur('entreprise', nom_entreprise=nom_entreprise_final, nouveau_fichier=nouveau_fichier)

    return JsonResponse({'message': 'Demande vendeur entreprise soumise', 'profil': _serialiseProfil(profil, request, utilisateur)}, status=200)

