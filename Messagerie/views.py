import json

from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from Registration.models import Utilisateur, Token, Entreprise, verifier_droit_admin, enregistrer_audit
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


def _serialiseProduitPartage(produit, request=None):
    """Résumé léger d'un produit partagé dans un message (voir maquette :
    photo, prix, disponibilité, nom, lien détails) — pas la sérialisation
    complète de _serialiseProduit (Produits/views/produitsViews.py).

    request : fourni par toutes les vues de ce module (build_absolute_uri),
    absent uniquement lors de la diffusion WebSocket depuis un signal (voir
    signals.py::broadcast_message) — sans hôte à cet endroit pour construire
    une URL absolue, même limitation déjà assumée pour les produits (voir
    Produits/signals.py::_serialiser_produit). build_absolute_uri n'altère
    pas une URL déjà absolue (stockage Cloudinary en production), donc cet
    appel est sans risque même quand une URL absolue existe déjà."""
    premiere_photo = produit.photos.first()
    photo = premiere_photo.url_photo.url if premiere_photo and premiere_photo.url_photo else None
    return {
        'id':              produit.id,
        'nom':             produit.nom,
        'prix':            produit.prix,
        'unitePrix':       produit.unitePrix,
        'unite_De_Mesure': produit.unite_De_Mesure,
        'est_disponible':  produit.est_disponible,
        'photo':           (request.build_absolute_uri(photo) if request else photo) if photo else None,
    }


def _serialiseMessage(message, request=None):
    return {
        'id':              message.id,
        'conversation_id': message.conversation_id,
        'expediteur_id':   message.expediteur_id,
        'contenu':         message.contenu,
<<<<<<< Updated upstream
        'produit':         _serialiseProduitPartage(message.produit) if message.produit_id else None,
=======
        'chiffre':         message.chiffre,   # True = contenu est du ciphertext AES-GCM (base64), à déchiffrer côté client
        'iv':              message.iv,
        'produit':         _serialiseProduitPartage(message.produit, request) if message.produit_id else None,
        # id du message auquel celui-ci répond (voir Message.repond_a) —
        # jamais le contenu : le frontend retrouve/déchiffre l'original
        # lui-même depuis les messages déjà chargés de la conversation
        # (voir Messagerie.jsx), donc rien à dupliquer/déchiffrer ici
        'repond_a_id':     message.repond_a_id,
>>>>>>> Stashed changes
        'date_envoi':      message.date_envoi.isoformat(),
        'lu':              message.lu,
    }


def _serialiseConversation(conversation, utilisateur_courant, request=None):
    autre = conversation.autre_participant(utilisateur_courant)
    dernier_message = conversation.messages.order_by('-date_envoi').first()
    non_lus = conversation.messages.filter(lu=False).exclude(expediteur=utilisateur_courant).count()
    return {
        'id': conversation.id,
        'autre_utilisateur': {
            'id':          autre.id,
            'nom_affiche': _nomAffiche(autre),
        },
        'dernier_message': _serialiseMessage(dernier_message, request) if dernier_message else None,
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

    # exclut les conversations que CET utilisateur a supprimées pour lui-même
    # (voir supprimerConversationPourMoi plus bas) — l'autre participant les
    # voit toujours normalement, ce filtre ne s'applique qu'à lui
    conversations = Conversation.objects.filter(
        Q(participant_a=utilisateur) | Q(participant_b=utilisateur)
    ).exclude(supprime_pour=utilisateur).select_related('participant_a', 'participant_b')

    return JsonResponse({
        'conversations': [_serialiseConversation(c, utilisateur, request) for c in conversations],
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

    # un compte bloqué (voir Utilisateur.bloquer, Registration/models.py) ne
    # peut plus démarrer de nouvelle conversation, SAUF avec un administrateur
    # — c'est son seul recours pour demander un déblocage
    if utilisateur.est_bloquer and destinataire.profil.role != 'admin':
        return JsonResponse({
            'error': "Votre compte a été bloqué ; vous ne pouvez plus contacter d'autres utilisateurs. "
                     "Vous pouvez uniquement contacter un administrateur.",
            'error_code': 'COMPTE_BLOQUE',
        }, status=403)

    conversation = Conversation.obtenir_ou_creer(utilisateur, destinataire)

    produit_id = data.get('produit_id')
    if produit_id and not conversation.messages.filter(produit_id=produit_id).exists():
        try:
            produit = Produits.objects.get(id=produit_id)
            Message.objects.create(conversation=conversation, expediteur=utilisateur, produit=produit)
        except Produits.DoesNotExist:
            pass

    return JsonResponse({
        'conversation': _serialiseConversation(conversation, utilisateur, request),
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

    # exclut les messages que CET utilisateur a supprimés pour lui-même (voir
    # supprimerMessagePourMoi plus bas) — l'autre participant les voit
    # toujours normalement, ce filtre ne s'applique qu'à lui
    messages = conversation.messages.exclude(supprime_pour=utilisateur).select_related('expediteur', 'produit').all()

    conversation.messages.filter(lu=False).exclude(expediteur=utilisateur).update(lu=True)

    return JsonResponse({
        'messages': [_serialiseMessage(m, request) for m in messages],
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

    # un compte bloqué (voir Utilisateur.bloquer, Registration/models.py) ne
    # peut plus écrire, SAUF à un administrateur — même règle que
    # demarrerConversation ci-dessus
    if utilisateur.est_bloquer and conversation.autre_participant(utilisateur).profil.role != 'admin':
        return JsonResponse({
            'error': "Votre compte a été bloqué ; vous ne pouvez plus contacter d'autres utilisateurs. "
                     "Vous pouvez uniquement contacter un administrateur.",
            'error_code': 'COMPTE_BLOQUE',
        }, status=403)

    produit = None
    if produit_id:
        try:
            produit = Produits.objects.get(id=produit_id)
        except Produits.DoesNotExist:
            return JsonResponse({'error': 'Produit introuvable'}, status=404)

    # message auquel celui-ci répond (voir Message.repond_a) — optionnel,
    # doit appartenir à la même conversation ; silencieusement ignoré sinon
    # (ex. l'original a été supprimé/vidé entre-temps côté client) plutôt que
    # de bloquer l'envoi, même philosophie que le repli en clair de l'E2E
    repond_a = None
    repond_a_id = data.get('repond_a_id')
    if repond_a_id:
        repond_a = conversation.messages.filter(id=repond_a_id).first()

    message = Message.objects.create(
<<<<<<< Updated upstream
        conversation=conversation, expediteur=utilisateur, contenu=contenu, produit=produit
=======
        conversation=conversation, expediteur=utilisateur, contenu=contenu, produit=produit,
        chiffre=bool(iv), iv=iv, repond_a=repond_a,
>>>>>>> Stashed changes
    )

    # une conversation supprimée par l'un des deux participants (voir
    # supprimerConversationPourMoi plus bas) redevient visible pour lui dès
    # qu'un nouveau message y est échangé — dans un sens comme dans l'autre
    if conversation.supprime_pour.exists():
        conversation.supprime_pour.clear()

    return JsonResponse({
        'message': _serialiseMessage(message, request),
    }, status=201)


# ── SUPPRIMER UN MESSAGE POUR SOI (participant) ───────────────────────────────
@csrf_exempt
def supprimerMessagePourMoi(request):
    """
    Retire un message de l'historique de l'utilisateur connecté SEULEMENT —
    l'autre participant continue de le voir normalement. Contrairement à
    supprimerMessageAdmin (suppression définitive, réservée à la modération),
    rien n'est supprimé en base : l'utilisateur est simplement ajouté à
    Message.supprime_pour (voir Messagerie/models.py).
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    message_id = data.get('id')
    if not message_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        message = Message.objects.get(
            Q(conversation__participant_a=utilisateur) | Q(conversation__participant_b=utilisateur),
            id=message_id,
        )
    except Message.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    message.supprime_pour.add(utilisateur)

    from Api.broadcast import broadcast_to_user
    # ne concerne que MOI (mes autres onglets/sessions ouverts) — jamais
    # diffusé à l'autre participant, qui continue de voir ce message
    broadcast_to_user(utilisateur.id, 'message.supprime_pour_moi', {
        'id': message.id, 'conversation_id': message.conversation_id,
    })

    return JsonResponse({'message': 'Message supprimé pour vous'}, status=200)


# ── SUPPRIMER UNE CONVERSATION POUR SOI (participant) ─────────────────────────
@csrf_exempt
def supprimerConversationPourMoi(request):
    """
    Retire une conversation de la liste de l'utilisateur connecté SEULEMENT —
    l'autre participant continue de la voir, avec tous ses messages. Réapparaît
    automatiquement dès que l'un des deux y écrit à nouveau (voir
    envoyerMessage ci-dessus, qui vide Conversation.supprime_pour).
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    conversation_id = data.get('id')
    if not conversation_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        conversation = Conversation.objects.get(
            Q(participant_a=utilisateur) | Q(participant_b=utilisateur), id=conversation_id
        )
    except Conversation.DoesNotExist:
        return JsonResponse({'error': 'Conversation introuvable'}, status=404)

    conversation.supprime_pour.add(utilisateur)

    from Api.broadcast import broadcast_to_user
    broadcast_to_user(utilisateur.id, 'conversation.supprime_pour_moi', {'id': conversation.id})

    return JsonResponse({'message': 'Conversation supprimée pour vous'}, status=200)


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

<<<<<<< Updated upstream
    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)
=======
    if not verifier_droit_admin(utilisateur, 'gestion_support'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_support'}}, status=403)
>>>>>>> Stashed changes

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

<<<<<<< Updated upstream
    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs"}, status=403)
=======
    if not verifier_droit_admin(utilisateur, 'gestion_support'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_support'}}, status=403)
>>>>>>> Stashed changes

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
<<<<<<< Updated upstream
    # notifie le vendeur en temps réel de la réponse reçue
    broadcast_to_user(message_admin.vendeur_id, 'message_admin.repondu', payload)
=======
    # notifie le vendeur en temps réel de la réponse reçue, avec SA propre
    # enveloppe de clé (voir _serialiseMessageAdmin)
    payload_vendeur = _serialiseMessageAdmin(message_admin, message_admin.vendeur)
    broadcast_to_user(message_admin.vendeur_id, 'message_admin.repondu', payload_vendeur)
    enregistrer_audit(utilisateur, 'message_support.repondre', f"A répondu au message support de {_nomAffiche(message_admin.vendeur)} (message id {message_admin.id})")
>>>>>>> Stashed changes

    return JsonResponse({
        'message_admin': payload,
    }, status=200)
<<<<<<< Updated upstream
=======


# ── SIGNALER UN MESSAGE (participant de la conversation) ──────────────────────
def _serialiseSignalementMessage(signalement):
    message = signalement.message
    # Pour un message chiffré, le serveur ne peut pas lire message.contenu
    # (ciphertext) : on affiche à la place la copie en clair fournie par le
    # signaleur au moment du signalement (il l'a déjà déchiffrée pour
    # l'afficher, voir contenu_signale). Pour un message legacy non chiffré,
    # message.contenu est directement exploitable — comportement inchangé.
    message_contenu = signalement.contenu_signale or (message.contenu if not message.chiffre else None)
    return {
        'id':                signalement.id,
        'message_id':        signalement.message_id,
        'message_contenu':   message_contenu,
        'message_expediteur_id':  message.expediteur_id,
        'message_expediteur_nom': _nomAffiche(message.expediteur),
        'conversation_id':   message.conversation_id,
        'signaleur_id':      signalement.signaleur_id,
        'signaleur_nom':     _nomAffiche(signalement.signaleur) if signalement.signaleur_id else None,
        'type_probleme':     signalement.type_probleme,
        'motif':             signalement.motif,
        'date_signalement':  signalement.date_signalement.isoformat(),
        'admin_traitant_id': signalement.admin_traitant_id,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
    }


@csrf_exempt
def signalerMessage(request):
    """
    Signale un message privé directement aux administrateurs — jamais à
    l'autre participant. Réservé aux participants de la conversation
    concernée (accès au contenu du message), même logique que
    signalerProduit/signalerVendeur (Produits/views/signalementsViews.py).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    message_id = data.get('message_id')
    if not message_id:
        return JsonResponse({'error': 'Le champ message_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'message_id'}}, status=400)

    type_probleme = data.get('type_probleme')
    if type_probleme not in dict(SignalementMessage.TYPE_PROBLEME):
        return JsonResponse({'error': 'Le champ type_probleme est requis et doit être valide'}, status=400)

    motif = (data.get('motif') or '').strip()
    if not motif:
        return JsonResponse({'error': 'Le motif est requis'}, status=400)

    # copie en clair du message déjà déchiffré côté client (voir _serialiseSignalementMessage
    # ci-dessus) — sans objet pour un message legacy non chiffré, dans ce cas
    # ignorée silencieusement puisque message.contenu suffit déjà
    contenu_dechiffre = (data.get('contenu_dechiffre') or '').strip()

    try:
        message = Message.objects.select_related('conversation').get(id=message_id)
    except Message.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    conversation = message.conversation
    if utilisateur.id not in (conversation.participant_a_id, conversation.participant_b_id):
        return JsonResponse({'error': "Vous n'avez pas accès à ce message"}, status=403)

    if message.expediteur_id == utilisateur.id:
        return JsonResponse({'error': 'Vous ne pouvez pas signaler votre propre message'}, status=400)

    signalement = SignalementMessage.objects.create(
        message=message, signaleur=utilisateur, type_probleme=type_probleme, motif=motif,
        contenu_signale=contenu_dechiffre,
    )

    from Api.broadcast import broadcast_to_admins
    broadcast_to_admins('signalement_message.created', _serialiseSignalementMessage(signalement))

    return JsonResponse({
        'message': 'Signalement envoyé aux administrateurs',
        'signalement': _serialiseSignalementMessage(signalement),
    }, status=201)


# ── SIGNALEMENTS DE MESSAGES EN ATTENTE (admin) ───────────────────────────────
@csrf_exempt
def listerSignalementsMessagesAdmin(request):
    """Signalements de messages pas encore traités — même principe de file
    partagée que listerSignalementsAdmin (Produits/views/signalementsViews.py)."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementMessage.objects.filter(admin_traitant__isnull=True).select_related(
        'message', 'message__expediteur', 'signaleur'
    )

    return JsonResponse({
        'signalements': [_serialiseSignalementMessage(s) for s in signalements],
    }, status=200)


# ── TRAITER UN SIGNALEMENT DE MESSAGE SANS SUPPRIMER (admin) ─────────────────
@csrf_exempt
def traiterSignalementMessage(request):
    """
    Marque un signalement de message comme traité SANS supprimer le message
    (l'admin a jugé que le contenu ne pose pas de problème) — pour supprimer
    le message, voir supprimerMessageAdmin ci-dessous. Même prise en charge
    atomique "premier arrivé premier servi" que traiterSignalement.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    signalement_id = data.get('id')
    if not signalement_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        signalement = SignalementMessage.objects.get(id=signalement_id)
    except SignalementMessage.DoesNotExist:
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    from django.utils import timezone

    maj = SignalementMessage.objects.filter(id=signalement_id, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now()
    )
    if maj == 0:
        signalement.refresh_from_db()
        nom_autre_admin = _nomAffiche(signalement.admin_traitant) if signalement.admin_traitant_id else None
        return JsonResponse({
            'error': f"Ce signalement a déjà été traité par {nom_autre_admin}" if nom_autre_admin
                     else "Ce signalement a déjà été traité",
        }, status=409)

    signalement.refresh_from_db()

    from Api.broadcast import broadcast_to_admins
    broadcast_to_admins('signalement_message.traite', {'id': signalement.id})
    enregistrer_audit(utilisateur, 'signalement_message.traiter', f"A traité le signalement du message id {signalement.message_id} (signalement id {signalement.id})")

    return JsonResponse({
        'signalement': _serialiseSignalementMessage(signalement),
    }, status=200)


# ── SUPPRIMER UN MESSAGE SIGNALÉ (admin) ──────────────────────────────────────
@csrf_exempt
def supprimerMessageAdmin(request):
    """
    Supprime définitivement un message (accès réservé aux administrateurs) —
    CASCADE : supprime aussi tout SignalementMessage lié. Notifie les deux
    participants en temps réel (voir broadcast_message_supprime,
    Messagerie/signals.py) pour que le message disparaisse immédiatement de
    leur fil s'ils ont la conversation ouverte.
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    message_id = data.get('id')
    if not message_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        message = Message.objects.get(id=message_id)
    except Message.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    # capturés avant suppression : plus accessibles une fois message.delete() exécuté
    signalements_lies = list(message.signalements.filter(admin_traitant__isnull=True).values_list('id', flat=True))

    message.delete()  # déclenche broadcast_message_supprime (Messagerie/signals.py) vers les 2 participants
    enregistrer_audit(utilisateur, 'message.supprimer', f"A supprimé le message signalé id {message_id}")

    from Api.broadcast import broadcast_to_admins
    # retire ce signalement (et tout autre signalement en attente sur ce même
    # message) de la file des admins — CASCADE l'a déjà supprimé en base
    for signalement_id in signalements_lies:
        broadcast_to_admins('signalement_message.traite', {'id': signalement_id})

    return JsonResponse({'message': 'Message supprimé avec succès'}, status=200)
>>>>>>> Stashed changes
