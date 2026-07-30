import json

from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from Registration.models import Utilisateur, Token, Entreprise
from Produits.models import Produits
from .models import Conversation, Message, MessageSupport


def _get_user_from_token(request):
    """
    Résout le token du header Authorization: Token <cle> en un objet Utilisateur.
    Même logique que Registration/views.py et Produits/views/_auth.py —
    dupliquée ici (pas de module d'auth partagé dans ce projet).
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


def _nomAffiche(utilisateur):
    """Nom affiché : raison sociale si compte entreprise, sinon prénom + nom —
    même détection que _nomVendeur (Produits/views/produitsViews.py) et
    Profil.obtenir_utilisateur_type (Registration/models.py)."""
    entreprise = Entreprise.objects.filter(pk=utilisateur.id).first()
    if entreprise:
        return entreprise.nom_Entreprise
    return f"{utilisateur.prenom} {utilisateur.nom}"


def _serialiseProduitPartage(produit):
    """Résumé léger d'un produit partagé dans un message (voir maquette :
    photo, prix, disponibilité, nom, lien détails) — pas la sérialisation
    complète de _serialiseProduit (Produits/views/produitsViews.py)."""
    premiere_photo = produit.photos.first()
    return {
        'id':              produit.id,
        'nom':             produit.nom,
        'prix':            produit.prix,
        'unitePrix':       produit.unitePrix,
        'unite_De_Mesure': produit.unite_De_Mesure,
        'est_disponible':  produit.est_disponible,
        'photo':           premiere_photo.url_photo.url if premiere_photo and premiere_photo.url_photo else None,
    }


def _serialiseMessage(message):
    return {
        'id':              message.id,
        'conversation_id': message.conversation_id,
        'expediteur_id':   message.expediteur_id,
        'contenu':         message.contenu,
        'produit':         _serialiseProduitPartage(message.produit) if message.produit_id else None,
        'date_envoi':      message.date_envoi.isoformat(),
        'lu':              message.lu,
    }


def _serialiseConversation(conversation, utilisateur_courant):
    autre = conversation.autre_participant(utilisateur_courant)
    dernier_message = conversation.messages.order_by('-date_envoi').first()
    non_lus = conversation.messages.filter(lu=False).exclude(expediteur=utilisateur_courant).count()
    return {
        'id': conversation.id,
        'autre_utilisateur': {
            'id':          autre.id,
            'nom_affiche': _nomAffiche(autre),
        },
        'dernier_message': _serialiseMessage(dernier_message) if dernier_message else None,
        'non_lus':  non_lus,
        'date_maj': conversation.date_maj.isoformat(),
    }


# ── LISTER MES CONVERSATIONS (utilisateur connecté) ───────────────────────────
@csrf_exempt
def mesConversations(request):
    """Liste les conversations de l'utilisateur connecté, triées par dernière activité."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    conversations = Conversation.objects.filter(
        Q(participant_a=utilisateur) | Q(participant_b=utilisateur)
    ).select_related('participant_a', 'participant_b')

    return JsonResponse({
        'conversations': [_serialiseConversation(c, utilisateur) for c in conversations],
    }, status=200)


# ── DÉMARRER/RÉCUPÉRER UNE CONVERSATION (utilisateur connecté) ────────────────
@csrf_exempt
def demarrerConversation(request):
    """
    Récupère la conversation existante avec destinataire_id, ou la crée.
    Si produit_id est fourni (bouton "Contacter" d'une fiche produit) et
    qu'aucun message de cette conversation ne partage déjà ce produit, il est
    envoyé comme message initial — évite de le renvoyer à chaque nouveau clic
    "Contacter" sur le même produit.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    destinataire_id = data.get('destinataire_id')
    if not destinataire_id:
        return JsonResponse({'error': 'Le champ destinataire_id est requis'}, status=400)

    if str(destinataire_id) == str(utilisateur.id):
        return JsonResponse({'error': 'Vous ne pouvez pas démarrer une conversation avec vous-même'}, status=400)

    try:
        destinataire = Utilisateur.objects.get(id=destinataire_id)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Destinataire introuvable'}, status=404)

    conversation = Conversation.obtenir_ou_creer(utilisateur, destinataire)

    produit_id = data.get('produit_id')
    if produit_id and not conversation.messages.filter(produit_id=produit_id).exists():
        try:
            produit = Produits.objects.get(id=produit_id)
            Message.objects.create(conversation=conversation, expediteur=utilisateur, produit=produit)
        except Produits.DoesNotExist:
            pass

    return JsonResponse({
        'conversation': _serialiseConversation(conversation, utilisateur),
    }, status=200)


# ── LISTER LES MESSAGES D'UNE CONVERSATION (participant) ─────────────────────
@csrf_exempt
def messagesConversation(request):
    """Liste les messages d'une conversation (?conversation_id=) et marque
    comme lus ceux de l'autre participant."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    conversation_id = request.GET.get('conversation_id')
    if not conversation_id:
        return JsonResponse({'error': 'Le paramètre conversation_id est requis'}, status=400)

    try:
        conversation = Conversation.objects.get(
            Q(participant_a=utilisateur) | Q(participant_b=utilisateur), id=conversation_id
        )
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation introuvable'}, status=404)

    messages = conversation.messages.select_related('expediteur', 'produit').all()

    conversation.messages.filter(lu=False).exclude(expediteur=utilisateur).update(lu=True)

    return JsonResponse({
        'messages': [_serialiseMessage(m) for m in messages],
    }, status=200)


# ── ENVOYER UN MESSAGE (participant) ──────────────────────────────────────────
@csrf_exempt
def envoyerMessage(request):
    """Envoie un message texte et/ou un produit partagé dans une conversation existante."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return JsonResponse({'error': 'Le champ conversation_id est requis'}, status=400)

    contenu = (data.get('contenu') or '').strip()
    produit_id = data.get('produit_id')
    if not contenu and not produit_id:
        return JsonResponse({'error': 'Le message doit contenir du texte ou un produit'}, status=400)

    try:
        conversation = Conversation.objects.get(
            Q(participant_a=utilisateur) | Q(participant_b=utilisateur), id=conversation_id
        )
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation introuvable'}, status=404)

    produit = None
    if produit_id:
        try:
            produit = Produits.objects.get(id=produit_id)
        except Produits.DoesNotExist:
            return JsonResponse({'error': 'Produit introuvable'}, status=404)

    message = Message.objects.create(
        conversation=conversation, expediteur=utilisateur, contenu=contenu, produit=produit
    )

    return JsonResponse({
        'message': _serialiseMessage(message),
    }, status=201)


# ── MESSAGES VENDEUR -> ADMINS (pas de destinataire précis) ───────────────────
def _serialiseMessageAdmin(message_admin):
    return {
        'id':              message_admin.id,
        'vendeur_id':      message_admin.vendeur_id,
        'vendeur_nom':     _nomAffiche(message_admin.vendeur),
        'contenu':         message_admin.contenu,
        'date_envoi':      message_admin.date_envoi.isoformat(),
        'admin_repondant_id':  message_admin.admin_repondant_id,
        'admin_repondant_nom': _nomAffiche(message_admin.admin_repondant) if message_admin.admin_repondant_id else None,
        'reponse':         message_admin.reponse,
        'date_reponse':    message_admin.date_reponse.isoformat() if message_admin.date_reponse else None,
    }


@csrf_exempt
def contacterAdmin(request):
    """
    Envoie un message "aux administrateurs" (vendeur connecté) — pas à un
    admin précis : visible par TOUS les admins tant que personne n'a répondu
    (voir listerMessagesAdminEnAttente), pour un support type "file partagée".
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'vendeur':
        return JsonResponse({'error': "Accès réservé aux vendeurs"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    contenu = (data.get('contenu') or '').strip()
    if not contenu:
        return JsonResponse({'error': 'Le message ne peut pas être vide'}, status=400)

    message_admin = MessageSupport.objects.create(vendeur=utilisateur, contenu=contenu)

    return JsonResponse({
        'message_admin': _serialiseMessageAdmin(message_admin),
    }, status=201)


# ── MES MESSAGES ENVOYÉS AUX ADMINS (vendeur connecté) ────────────────────────
@csrf_exempt
def mesMessagesAdmin(request):
    """Historique des messages envoyés par le vendeur connecté aux admins,
    avec la réponse si un admin a déjà répondu."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    messages_admin = MessageSupport.objects.filter(vendeur=utilisateur).select_related('vendeur', 'admin_repondant')

    return JsonResponse({
        'messages_admin': [_serialiseMessageAdmin(m) for m in messages_admin],
    }, status=200)


# ── MESSAGES EN ATTENTE DE RÉPONSE (admin connecté) ───────────────────────────
@csrf_exempt
def listerMessagesAdminEnAttente(request):
    """
    Liste les messages vendeur->admin pas encore pris en charge
    (admin_repondant IS NULL) — visible par TOUS les admins ; dès qu'un admin
    répond (voir repondreMessageAdmin), ce message disparaît de cette liste
    pour les autres admins.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    messages_admin = (
        MessageSupport.objects
        .filter(admin_repondant__isnull=True)
        .select_related('vendeur')
        .order_by('date_envoi')
    )

    return JsonResponse({
        'messages_admin': [_serialiseMessageAdmin(m) for m in messages_admin],
    }, status=200)


# ── RÉPONDRE À UN MESSAGE VENDEUR->ADMIN (admin connecté) ─────────────────────
@csrf_exempt
def repondreMessageAdmin(request):
    """
    Répond à un message vendeur->admin — le premier admin à répondre "prend"
    le message : admin_repondant est fixé de façon atomique (filter sur
    admin_repondant__isnull=True dans l'UPDATE) pour qu'un double-clic
    simultané par deux admins différents ne puisse pas tous les deux réussir.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    message_admin_id = data.get('id')
    if not message_admin_id:
        return JsonResponse({'error': 'Le champ id est requis'}, status=400)

    reponse = (data.get('reponse') or '').strip()
    if not reponse:
        return JsonResponse({'error': 'La réponse ne peut pas être vide'}, status=400)

    try:
        message_admin = MessageSupport.objects.get(id=message_admin_id)
    except MessageSupport.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    from django.utils import timezone

    # UPDATE conditionnel : ne réussit que si personne n'a encore répondu —
    # empêche deux admins de "prendre" le même message en même temps
    maj = MessageSupport.objects.filter(id=message_admin_id, admin_repondant__isnull=True).update(
        admin_repondant=utilisateur, reponse=reponse, date_reponse=timezone.now()
    )
    if maj == 0:
        message_admin.refresh_from_db()
        nom_autre_admin = _nomAffiche(message_admin.admin_repondant) if message_admin.admin_repondant_id else None
        return JsonResponse({
            'error': f"Ce message a déjà été pris en charge par {nom_autre_admin}" if nom_autre_admin
                     else "Ce message a déjà été pris en charge",
        }, status=409)

    message_admin.refresh_from_db()

    from Api.broadcast import broadcast_to_admins, broadcast_to_user
    payload = _serialiseMessageAdmin(message_admin)
    # retire le message de la file des AUTRES admins (voir listerMessagesAdminEnAttente)
    broadcast_to_admins('message_admin.repondu', {'id': message_admin.id})
    # notifie le vendeur en temps réel de la réponse reçue
    broadcast_to_user(message_admin.vendeur_id, 'message_admin.repondu', payload)

    return JsonResponse({
        'message_admin': payload,
    }, status=200)
