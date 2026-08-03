import json

from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from Registration.models import Utilisateur, Token, Entreprise
from Produits.models import Produits
from .models import Conversation, Message, MessageSupport, SignalementMessage


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


def _nomAffiche(utilisateur, viewer=None):
    """Nom affiché : raison sociale si compte entreprise, sinon prénom + nom —
    même détection que _nomVendeur (Produits/views/produitsViews.py) et
    Profil.obtenir_utilisateur_type (Registration/models.py).

    Si `utilisateur` est un admin, son identité réelle reste masquée pour
    quiconque n'est pas lui-même admin (viewer=None ou viewer non-admin) : la
    messagerie affiche alors simplement "Admin", jamais le nom personnel —
    un vendeur/acheteur qui échange avec l'équipe RekoltHt n'a pas à savoir
    QUEL admin lui écrit. Un admin qui consulte reste vu normalement par un
    autre admin (viewer admin)."""
    if utilisateur.profil.role == 'admin' and (viewer is None or viewer.profil.role != 'admin'):
        return "Admin"
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
        'chiffre':         message.chiffre,   # True = contenu est du ciphertext AES-GCM (base64), à déchiffrer côté client
        'iv':              message.iv,
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
            'nom_affiche': _nomAffiche(autre, viewer=utilisateur_courant),
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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

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
    N'envoie jamais de message automatiquement : le produit d'origine (bouton
    "Contacter" d'une fiche produit) est proposé côté frontend comme aperçu
    "en réponse à" au-dessus du champ de saisie (voir Messagerie.jsx), et
    n'est réellement envoyé que si l'utilisateur valide lui-même via
    envoyerMessage (produit_id, avec ou sans texte additionnel).
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

    destinataire_id = data.get('destinataire_id')
    if not destinataire_id:
        return JsonResponse({'error': 'Le champ destinataire_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'destinataire_id'}}, status=400)

    if str(destinataire_id) == str(utilisateur.id):
        return JsonResponse({'error': 'Vous ne pouvez pas démarrer une conversation avec vous-même'}, status=400)

    try:
        destinataire = Utilisateur.objects.get(id=destinataire_id)
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Destinataire introuvable'}, status=404)

    conversation = Conversation.obtenir_ou_creer(utilisateur, destinataire)

    return JsonResponse({
        'conversation': _serialiseConversation(conversation, utilisateur),
    }, status=200)


# ── LISTER LES MESSAGES D'UNE CONVERSATION (participant) ─────────────────────
@csrf_exempt
def messagesConversation(request):
    """Liste les messages d'une conversation (?conversation_id=) et marque
    comme lus ceux de l'autre participant."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    conversation_id = request.GET.get('conversation_id')
    if not conversation_id:
        return JsonResponse({'error': 'Le champ conversation_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'conversation_id'}}, status=400)

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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return JsonResponse({'error': 'Le champ conversation_id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'conversation_id'}}, status=400)

    contenu = (data.get('contenu') or '').strip()
    # présent uniquement pour un message chiffré côté client (voir
    # src/utils/e2eCrypto.js) — contenu porte alors le ciphertext base64, pas
    # le texte en clair (voir Message.chiffre/iv, Messagerie/models.py)
    iv = data.get('iv')
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
            return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    message = Message.objects.create(
        conversation=conversation, expediteur=utilisateur, contenu=contenu, produit=produit,
        chiffre=bool(iv), iv=iv,
    )

    return JsonResponse({
        'message': _serialiseMessage(message),
    }, status=201)


# ── MESSAGES VENDEUR -> ADMINS (pas de destinataire précis) ───────────────────
def _entreeClePour(cles, utilisateur_id):
    """Retrouve, dans une liste de clés enveloppées {utilisateur_id, cle_chiffree, iv}
    (chiffrement multi-destinataires, voir MessageSupport dans Messagerie/models.py),
    l'entrée destinée à cet utilisateur — ou None si absente (ex : admin ayant
    configuré sa clé E2E après l'envoi du message, voir clesPubliquesAdmins
    dans Registration/views.py)."""
    if not cles:
        return None
    for entree in cles:
        if str(entree.get('utilisateur_id')) == str(utilisateur_id):
            return entree
    return None


def _serialiseMessageAdmin(message_admin, pour_utilisateur):
    """pour_utilisateur détermine QUELLE enveloppe de clé renvoyer (vendeur
    d'origine ou tel admin) — chacun a sa propre copie chiffrée de la clé AES
    du message, jamais la même (chiffrement enveloppe multi-destinataires)."""
    cle_contenu = _entreeClePour(message_admin.cles_contenu, pour_utilisateur.id)
    cle_reponse = _entreeClePour(message_admin.cles_reponse, pour_utilisateur.id)
    return {
        'id':                 message_admin.id,
        'vendeur_id':         message_admin.vendeur_id,
        'vendeur_nom':        _nomAffiche(message_admin.vendeur),
        'contenu':            message_admin.contenu,
        'chiffre':            message_admin.chiffre,
        'iv_contenu':         message_admin.iv_contenu,
        # ma copie chiffrée de la clé AES du message (None si je n'ai pas
        # encore de clé E2E configurée à l'envoi, ou si le message est legacy)
        'cle_contenu_moi':    cle_contenu['cle_chiffree'] if cle_contenu else None,
        'iv_cle_contenu_moi': cle_contenu['iv'] if cle_contenu else None,
        'date_envoi':         message_admin.date_envoi.isoformat(),
        'admin_repondant_id':  message_admin.admin_repondant_id,
        'admin_repondant_nom': _nomAffiche(message_admin.admin_repondant) if message_admin.admin_repondant_id else None,
        'reponse':            message_admin.reponse,
        'iv_reponse':         message_admin.iv_reponse,
        'cle_reponse_moi':    cle_reponse['cle_chiffree'] if cle_reponse else None,
        'iv_cle_reponse_moi': cle_reponse['iv'] if cle_reponse else None,
        'date_reponse':       message_admin.date_reponse.isoformat() if message_admin.date_reponse else None,
    }


@csrf_exempt
def contacterAdmin(request):
    """
    Envoie un message "aux administrateurs" (n'importe quel compte acheteur
    ou vendeur connecté — le champ du modèle s'appelle `vendeur` pour des
    raisons historiques mais accepte maintenant les deux) — pas à un admin
    précis : visible par TOUS les admins tant que personne n'a répondu (voir
    listerMessagesAdminEnAttente), pour un support type "file partagée".
    Utilisé à la fois par ContacterAdmin.jsx (formulaire dédié) et par
    ChatbotVendeur.jsx quand celui-ci ne trouve pas de réponse dans la FAQ.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role == 'admin':
        return JsonResponse({'error': "Un compte administrateur ne peut pas envoyer ce type de message", 'error_code': 'ADMIN_CANNOT_CONTACT_ADMIN'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    contenu = (data.get('contenu') or '').strip()
    if not contenu:
        return JsonResponse({'error': 'Le message ne peut pas être vide'}, status=400)

    # chiffrement enveloppe calculé côté client (voir src/utils/e2eCrypto.js) :
    # une clé AES par message, chiffrée séparément pour chaque admin actuel
    # (récupérés via GET cle-chiffrement/admins/) ET pour l'expéditeur
    # lui-même (pour pouvoir relire son propre historique) — le destinataire
    # exact n'est pas connu à l'avance, voir MessageSupport dans
    # Messagerie/models.py.
    iv_contenu = data.get('iv_contenu')
    cles_contenu = data.get('cles_contenu')

    # le broadcast temps réel vers les admins (event 'message_admin.created')
    # se fait dans Messagerie/signals.py (post_save), comme le reste des
    # broadcasts de création/suppression de ce fichier
    message_admin = MessageSupport.objects.create(
        vendeur=utilisateur, contenu=contenu,
        chiffre=bool(iv_contenu and cles_contenu),
        iv_contenu=iv_contenu, cles_contenu=cles_contenu,
    )

    return JsonResponse({
        'message_admin': _serialiseMessageAdmin(message_admin, utilisateur),
    }, status=201)


# ── MES MESSAGES ENVOYÉS AUX ADMINS (vendeur connecté) ────────────────────────
@csrf_exempt
def mesMessagesAdmin(request):
    """Historique des messages envoyés par le vendeur connecté aux admins,
    avec la réponse si un admin a déjà répondu."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    messages_admin = MessageSupport.objects.filter(vendeur=utilisateur).select_related('vendeur', 'admin_repondant')

    return JsonResponse({
        'messages_admin': [_serialiseMessageAdmin(m, utilisateur) for m in messages_admin],
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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs", 'error_code': 'ADMIN_ONLY'}, status=403)

    messages_admin = (
        MessageSupport.objects
        .filter(admin_repondant__isnull=True)
        .select_related('vendeur')
        .order_by('date_envoi')
    )

    return JsonResponse({
        'messages_admin': [_serialiseMessageAdmin(m, utilisateur) for m in messages_admin],
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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs", 'error_code': 'ADMIN_ONLY'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    message_admin_id = data.get('id')
    if not message_admin_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    reponse = (data.get('reponse') or '').strip()
    if not reponse:
        return JsonResponse({'error': 'La réponse ne peut pas être vide'}, status=400)

    # chiffrement enveloppe de la réponse, même principe que contacterAdmin :
    # une clé AES chiffrée pour le vendeur d'origine + tous les admins actuels
    iv_reponse = data.get('iv_reponse')
    cles_reponse = data.get('cles_reponse')

    try:
        message_admin = MessageSupport.objects.get(id=message_admin_id)
    except MessageSupport.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    from django.utils import timezone

    # UPDATE conditionnel : ne réussit que si personne n'a encore répondu —
    # empêche deux admins de "prendre" le même message en même temps
    maj = MessageSupport.objects.filter(id=message_admin_id, admin_repondant__isnull=True).update(
        admin_repondant=utilisateur, reponse=reponse, date_reponse=timezone.now(),
        iv_reponse=iv_reponse, cles_reponse=cles_reponse,
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
    # retire le message de la file des AUTRES admins (voir listerMessagesAdminEnAttente)
    # — juste l'id, aucun contenu à déchiffrer ici, group-wide reste correct
    broadcast_to_admins('message_admin.repondu', {'id': message_admin.id})
    # notifie le vendeur en temps réel de la réponse reçue, avec SA propre
    # enveloppe de clé (voir _serialiseMessageAdmin)
    payload_vendeur = _serialiseMessageAdmin(message_admin, message_admin.vendeur)
    broadcast_to_user(message_admin.vendeur_id, 'message_admin.repondu', payload_vendeur)

    return JsonResponse({
        'message_admin': _serialiseMessageAdmin(message_admin, utilisateur),
    }, status=200)


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

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs", 'error_code': 'ADMIN_ONLY'}, status=403)

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

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs", 'error_code': 'ADMIN_ONLY'}, status=403)

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

    if utilisateur.profil.role != 'admin':
        return JsonResponse({'error': "Accès réservé aux administrateurs", 'error_code': 'ADMIN_ONLY'}, status=403)

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

    from Api.broadcast import broadcast_to_admins
    # retire ce signalement (et tout autre signalement en attente sur ce même
    # message) de la file des admins — CASCADE l'a déjà supprimé en base
    for signalement_id in signalements_lies:
        broadcast_to_admins('signalement_message.traite', {'id': signalement_id})

    return JsonResponse({'message': 'Message supprimé avec succès'}, status=200)
