import json
import re

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from Registration.models import Utilisateur, Token, Entreprise, verifier_droit_admin, enregistrer_audit
from Produits.models import Produits
from .models import Conversation, Message, MessageSupport, SignalementMessage
from .services.support_chiffrement_service import chiffrer as chiffrer_support, ErreurChiffrementSupport, dechiffrer as dechiffrer_support
from .services.messages_chiffrement_service import chiffrer as chiffrer_message, ErreurChiffrementMessages, dechiffrer as dechiffrer_message
from .services.chatbot_ia_service import obtenir_reponse_ia, ErreurChatbotIA

# limite de longueur d'un message "Contacter un admin" (voir contacterAdmin
# ci-dessous et ContacterAdmin.jsx côté frontend, même valeur des deux côtés)
LONGUEUR_MAX_MESSAGE_ADMIN = 1000

# détection de lien dans un message "Contacter un admin" — même règle que
# ../RekoltHtFront/src/utils/detectionLien.js côté frontend (protocole/www
# explicite, ou domaine nu suivi d'un TLD courant)
REGEX_LIEN_MESSAGE_ADMIN = re.compile(
    r'(https?://|www\.)\S+|\b[a-z0-9-]+\.(com|net|org|ht|io|co|info|biz|xyz|link|app|gov|edu|me|tv|shop)(/\S*)?\b',
    re.IGNORECASE,
)


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


def _url_absolue_media(chemin, request=None):
    """Construit une URL absolue pour un fichier média (photo produit, ...).

    request.build_absolute_uri() n'existe que dans une vue — absent lors de
    la diffusion WebSocket depuis un signal (voir signals.py::broadcast_message).
    Sans ça, une URL relative (stockage local, dev — ex. "/media/xyz.jpg")
    part telle quelle vers le frontend, qui la résout contre SA PROPRE
    origine (ex. localhost:5173) au lieu de celle du backend : la photo
    partagée dans le chat n'apparaît alors jamais en temps réel (seul un
    rechargement de page, qui repasse par une vraie requête REST avec
    request, corrige l'URL) — bug corrigé ici en retombant sur
    BACKEND_BASE_URL (voir BackendRekoltHt/settings/dev.py, prod.py) quand
    aucune requête n'est disponible. Une URL déjà absolue (Cloudinary, ou
    build_absolute_uri sur une URL déjà absolue) est retournée telle quelle."""
    if not chemin or chemin.startswith('http://') or chemin.startswith('https://'):
        return chemin
    if request:
        return request.build_absolute_uri(chemin)
    return settings.BACKEND_BASE_URL.rstrip('/') + chemin


def _serialiseProduitPartage(produit, request=None):
    """Résumé léger d'un produit partagé dans un message (voir maquette :
    photo, prix, disponibilité, nom, lien détails) — pas la sérialisation
    complète de _serialiseProduit (Produits/views/produitsViews.py)."""
    premiere_photo = produit.photos.first()
    photo = premiere_photo.url_photo.url if premiere_photo and premiere_photo.url_photo else None
    return {
        'id':              produit.id,
        'nom':             produit.nom,
        'prix':            produit.prix,
        'unitePrix':       produit.unitePrix,
        'unite_De_Mesure': produit.unite_De_Mesure,
        'est_disponible':  produit.est_disponible,
        'photo':           _url_absolue_media(photo, request),
    }


def _contenuMessageAffiche(message):
    """Texte à afficher pour un Message — déchiffre côté SERVEUR si besoin
    (voir chiffrer_message/dechiffrer_message plus haut) : contrairement à
    l'ancien chiffrement de bout en bout, le frontend ne reçoit plus jamais
    de ciphertext ni ne fait de déchiffrement lui-même (voir
    MESSAGES_MASTER_KEY, BackendRekoltHt/settings/base.py — décision
    explicitement inversée par le propriétaire : accès immédiat aux messages
    sur n'importe quel appareil/navigateur dès la connexion)."""
    if not message.chiffre or not message.contenu:
        return message.contenu
    dechiffre = dechiffrer_message(message.contenu)
    return dechiffre if dechiffre is not None else "[Message illisible]"


def _serialiseMessage(message, request=None):
    return {
        'id':              message.id,
        'conversation_id': message.conversation_id,
        'expediteur_id':   message.expediteur_id,
        'contenu':         _contenuMessageAffiche(message),
        'produit':         _serialiseProduitPartage(message.produit, request) if message.produit_id else None,
        # id du message auquel celui-ci répond (voir Message.repond_a) —
        # jamais le contenu : le frontend retrouve l'original lui-même depuis
        # les messages déjà chargés de la conversation (voir Messagerie.jsx)
        'repond_a_id':     message.repond_a_id,
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
            'nom_affiche': _nomAffiche(autre, viewer=utilisateur_courant),
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
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

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

    return JsonResponse({
        'conversation': _serialiseConversation(conversation, utilisateur, request),
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
            return JsonResponse({'error': 'Produit introuvable', 'error_code': 'PRODUCT_NOT_FOUND'}, status=404)

    # message auquel celui-ci répond (voir Message.repond_a) — optionnel,
    # doit appartenir à la même conversation ; silencieusement ignoré sinon
    # (ex. l'original a été supprimé/vidé entre-temps côté client)
    repond_a = None
    repond_a_id = data.get('repond_a_id')
    if repond_a_id:
        repond_a = conversation.messages.filter(id=repond_a_id).first()

    # chiffré côté SERVEUR avant enregistrement (voir Messagerie/services/
    # messages_chiffrement_service.py, MESSAGES_MASTER_KEY) — remplace
    # l'ancien chiffrement de bout en bout géré par le navigateur : protège
    # contre un accès base de données brut, tout en restant lisible
    # immédiatement par le serveur pour les deux participants, sur n'importe
    # quel appareil, sans mot de passe ni clé à saisir
    contenu_stocke, est_chiffre = contenu, False
    if contenu:
        try:
            contenu_stocke = chiffrer_message(contenu)
            est_chiffre = True
        except ErreurChiffrementMessages as e:
            return JsonResponse({'error': str(e), 'error_code': 'CHIFFREMENT_INDISPONIBLE'}, status=503)

    message = Message.objects.create(
        conversation=conversation, expediteur=utilisateur, contenu=contenu_stocke, produit=produit,
        chiffre=est_chiffre, repond_a=repond_a,
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
    # supprime_pour est M2M : contrairement à un .save() classique, y ajouter
    # quelqu'un ne déclenche PAS auto_now sur date_maj — sans ce .save()
    # explicite, une sauvegarde incrémentale (voir Sauvegarde/services/
    # export_service.py, basée sur date_maj) ne verrait jamais passer cette
    # conversation après cette suppression locale
    conversation.save(update_fields=['date_maj'])

    from Api.broadcast import broadcast_to_user
    broadcast_to_user(utilisateur.id, 'conversation.supprime_pour_moi', {'id': conversation.id})

    return JsonResponse({'message': 'Conversation supprimée pour vous'}, status=200)


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


def _champAffiche(texte_stocke, chiffre, format_chiffrement):
    """Retourne (texte_a_afficher, format) pour contenu OU reponse d'un
    MessageSupport :
      - pas chiffré du tout (legacy, avant l'introduction du chiffrement) :
        ('clair', texte tel quel) ;
      - 'coffre_serveur' : le SERVEUR déchiffre lui-même ici — le texte en
        clair part directement dans la réponse JSON, n'importe quel admin
        gestion_support peut le lire, même attribué après l'envoi (voir
        Messagerie/services/support_chiffrement_service.py) ;
      - 'e2e_client' (ou legacy sans format_chiffrement renseigné) : le
        ciphertext part TEL QUEL, le frontend le déchiffre lui-même en
        enveloppe (voir cle_contenu_moi/cle_reponse_moi ci-dessous) —
        uniquement pour les admins déjà configurés à l'envoi."""
    if not chiffre:
        return texte_stocke, 'clair'
    if format_chiffrement == 'coffre_serveur':
        # dechiffrer_support renvoie None si le jeton est corrompu ou provient
        # d'une clé différente (voir son docstring) — sans ce garde-fou, ce
        # None partait tel quel dans le JSON ('contenu': null), affiché tel
        # quel côté frontend au lieu du message "illisible" prévu.
        dechiffre = dechiffrer_support(texte_stocke)
        return (dechiffre if dechiffre is not None else "[Message illisible — erreur de déchiffrement]"), 'coffre_serveur'
    return texte_stocke, 'e2e_client'


def _serialiseMessageAdmin(message_admin, pour_utilisateur):
    """pour_utilisateur détermine QUELLE enveloppe de clé renvoyer pour un
    message au format legacy 'e2e_client' (vendeur d'origine ou tel admin —
    chacun a sa propre copie chiffrée de la clé AES, jamais la même) ; sans
    effet pour le format 'coffre_serveur', déjà déchiffré ici pour tout le
    monde. Voir _champAffiche ci-dessus."""
    cle_contenu = _entreeClePour(message_admin.cles_contenu, pour_utilisateur.id)
    cle_reponse = _entreeClePour(message_admin.cles_reponse, pour_utilisateur.id)
    # pas de booléen "reponse_chiffre" dédié en base (contrairement à
    # `chiffre` pour contenu) — déduit de la présence d'un ciphertext connu :
    # legacy (iv_reponse posé) ou coffre_serveur (reponse_format_chiffrement)
    reponse_chiffree = bool(message_admin.reponse) and bool(message_admin.iv_reponse or message_admin.reponse_format_chiffrement == 'coffre_serveur')
    contenu_affiche, format_contenu = _champAffiche(message_admin.contenu, message_admin.chiffre, message_admin.format_chiffrement)
    reponse_affichee, format_reponse = _champAffiche(message_admin.reponse, reponse_chiffree, message_admin.reponse_format_chiffrement)
    return {
        'id':                 message_admin.id,
        'vendeur_id':         message_admin.vendeur_id,
        'vendeur_nom':        _nomAffiche(message_admin.vendeur),
        'contenu':            contenu_affiche,
        'chiffre':            message_admin.chiffre,
        'format_chiffrement': format_contenu,
        'iv_contenu':         message_admin.iv_contenu,
        # ma copie chiffrée de la clé AES du message (None si je n'ai pas
        # encore de clé E2E configurée à l'envoi, si le message est legacy,
        # ou si le message est déjà au format coffre_serveur — inutile alors)
        'cle_contenu_moi':    cle_contenu['cle_chiffree'] if cle_contenu else None,
        'iv_cle_contenu_moi': cle_contenu['iv'] if cle_contenu else None,
        'date_envoi':         message_admin.date_envoi.isoformat(),
        'admin_repondant_id':  message_admin.admin_repondant_id,
        # viewer=pour_utilisateur : un vendeur voit toujours "Admin" (identité
        # masquée, voir _nomAffiche), mais un ADMIN qui consulte doit voir le
        # nom réel de l'admin qui a répondu — bug corrigé (viewer manquant
        # faisait toujours retomber sur "Admin", même entre admins). L'email
        # suit la même règle, réservé aux viewers admin (jamais exposé à un
        # vendeur/acheteur).
        'admin_repondant_nom':   _nomAffiche(message_admin.admin_repondant, viewer=pour_utilisateur) if message_admin.admin_repondant_id else None,
        'admin_repondant_email': message_admin.admin_repondant.email if (message_admin.admin_repondant_id and pour_utilisateur.profil.role == 'admin') else None,
        'reponse':                    reponse_affichee,
        'reponse_format_chiffrement': format_reponse,
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
    # filet de sécurité serveur (voir maxLength={LONGUEUR_MAX_MESSAGE_ADMIN},
    # ContacterAdmin.jsx) — l'appel direct à l'API sans passer par le
    # formulaire ne doit pas pouvoir contourner la limite
    if len(contenu) > LONGUEUR_MAX_MESSAGE_ADMIN:
        return JsonResponse({
            'error': f"Le message ne peut pas dépasser {LONGUEUR_MAX_MESSAGE_ADMIN} caractères",
            'error_code': 'MESSAGE_TROP_LONG',
        }, status=400)
    # aucun lien autorisé (voir ../RekoltHtFront/src/utils/detectionLien.js) —
    # même filet de sécurité serveur que la limite de longueur ci-dessus
    if REGEX_LIEN_MESSAGE_ADMIN.search(contenu):
        return JsonResponse({
            'error': "Les liens ne sont pas autorisés dans ce message",
            'error_code': 'LIEN_INTERDIT',
        }, status=400)

    # chiffré côté serveur ("coffre support", voir Messagerie/services/
    # support_chiffrement_service.py) — décision assumée par le propriétaire :
    # n'importe quel admin gestion_support peut lire ce message, MÊME si ce
    # droit lui est accordé après cet envoi (impossible avec le chiffrement de
    # bout en bout, voir format_chiffrement, Messagerie/models.py).
    try:
        contenu_stocke = chiffrer_support(contenu)
    except ErreurChiffrementSupport as e:
        return JsonResponse({'error': str(e), 'error_code': 'COFFRE_SUPPORT_INDISPONIBLE'}, status=503)

    # le broadcast temps réel vers les admins (event 'message_admin.created')
    # se fait dans Messagerie/signals.py (post_save), comme le reste des
    # broadcasts de création/suppression de ce fichier
    message_admin = MessageSupport.objects.create(
        vendeur=utilisateur, contenu=contenu_stocke,
        chiffre=True, format_chiffrement='coffre_serveur',
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

    if not verifier_droit_admin(utilisateur, 'gestion_support'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_support'}}, status=403)

    messages_admin = (
        MessageSupport.objects
        .filter(admin_repondant__isnull=True)
        .select_related('vendeur')
        .order_by('date_envoi')
    )

    return JsonResponse({
        'messages_admin': [_serialiseMessageAdmin(m, utilisateur) for m in messages_admin],
    }, status=200)


# ── MESSAGES DÉJÀ RÉPONDUS (admin connecté) ────────────────────────────────────
@csrf_exempt
def listerMessagesAdminRepondus(request):
    """
    Historique des messages déjà pris en charge — MOI SEUL par défaut (un
    admin gestion_support à droits limités ne voit que ses propres réponses,
    pas celles des autres), TOUT LE MONDE pour un compte "Tous les droits" ou
    le propriétaire (voir verifier_droit_admin(utilisateur, 'super_admin') —
    True dans ces deux cas précis, voir DroitsAdmin.a_droit,
    Registration/models.py). Symétrique de listerMessagesAdminEnAttente.
    Exclut aussi ce que MOI j'ai retiré de MON historique (voir
    historique_masque_pour, MessageSupport — et supprimerHistoriqueMessagesSupport
    ci-dessous) : chaque admin a son propre historique, masquer une entrée
    n'affecte jamais ce que voient les autres.
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_support'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_support'}}, status=403)

    messages_admin = MessageSupport.objects.filter(admin_repondant__isnull=False).exclude(historique_masque_pour=utilisateur)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        messages_admin = messages_admin.filter(admin_repondant=utilisateur)
    messages_admin = messages_admin.select_related('vendeur', 'admin_repondant').order_by('-date_reponse')

    return JsonResponse({
        'messages_admin': [_serialiseMessageAdmin(m, utilisateur) for m in messages_admin],
    }, status=200)


# ── RETIRER DE MON HISTORIQUE DES MESSAGES RÉPONDUS (admin connecté) ─────────
@csrf_exempt
def supprimerHistoriqueMessagesSupport(request):
    """
    Retire une ou plusieurs entrées de l'historique "messages déjà répondus"
    — MAIS SEULEMENT de la vue de l'admin qui clique (historique_masque_pour,
    voir MessageSupport et listerMessagesAdminRepondus ci-dessus) : ce n'est
    JAMAIS un vrai delete() de la ligne MessageSupport, contrairement à
    Sauvegarde/views.py::historique_supprimer (qui journalise dans une table
    séparée, sans équivalent ici — la ligne MessageSupport reste la source de
    vérité pour mesMessagesAdmin côté vendeur ET l'historique des AUTRES
    admins). Chaque admin a son propre historique (demande explicite) : "Tous
    les droits"/le propriétaire retirer une entrée ne l'efface pas de
    l'historique de l'admin qui a réellement répondu, et vice-versa. Portée
    de sélection identique à ce que l'admin peut déjà VOIR : un admin à
    droits limités ne peut retirer que SES PROPRES réponses.
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_support'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_support'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    messages_admin = MessageSupport.objects.filter(id__in=ids, admin_repondant__isnull=False)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        messages_admin = messages_admin.filter(admin_repondant=utilisateur)

    nombre_masque = 0
    for message_admin in messages_admin:
        message_admin.historique_masque_pour.add(utilisateur)
        nombre_masque += 1

    enregistrer_audit(utilisateur, 'message_support.masquer_historique', f"A retiré {nombre_masque} entrée(s) de son historique des messages support")

    return JsonResponse({'nombre_supprime': nombre_masque}, status=200)


# ── RAPPORT PDF D'AUDIT — MESSAGES SUPPORT (Tous les droits / propriétaire) ───
@csrf_exempt
def genererRapportSupport(request):
    """
    Génère le rapport PDF listant, sur une période choisie, chaque message
    support déjà répondu avec son CONTENU et la RÉPONSE donnée (pas juste une
    ligne de journal — demande explicite) — réservé à "Tous les droits" ou au
    propriétaire (verifier_droit_admin(utilisateur, 'super_admin'), même
    logique que listerMessagesAdminRepondus mais ici JAMAIS restreint à "moi
    seul" : ce rapport n'a de sens que pour auditer TOUT le monde, ou un admin
    précis choisi via ?admin_id=. Symétrique de Registration/views.py::
    genererRapportAudit (mêmes conventions de validation de dates).
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'super_admin'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'super_admin'}}, status=403)

    from datetime import datetime
    from django.utils import timezone as dj_timezone

    date_debut_str = request.GET.get('date_debut')
    date_fin_str   = request.GET.get('date_fin')
    if not date_debut_str or not date_fin_str:
        return JsonResponse({'error': 'Les champs date_debut et date_fin (AAAA-MM-JJ) sont requis', 'error_code': 'FIELD_REQUIRED'}, status=400)

    try:
        date_debut = datetime.strptime(date_debut_str, '%Y-%m-%d').date()
        date_fin   = datetime.strptime(date_fin_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Dates invalides, format attendu AAAA-MM-JJ', 'error_code': 'INVALID_DATE'}, status=400)

    if date_debut > date_fin:
        return JsonResponse({'error': 'La date de début doit précéder la date de fin'}, status=400)

    if date_fin > dj_timezone.localdate():
        return JsonResponse({'error': 'La date de fin ne peut pas être dans le futur', 'error_code': 'INVALID_DATE'}, status=400)

    messages_admin = MessageSupport.objects.filter(
        admin_repondant__isnull=False,
        date_reponse__date__gte=date_debut, date_reponse__date__lte=date_fin,
    )

    admin_id = request.GET.get('admin_id')
    nom_admin_filtre = None
    if admin_id:
        try:
            admin_filtre = Utilisateur.objects.get(id=admin_id)
        except Utilisateur.DoesNotExist:
            return JsonResponse({'error': 'Administrateur introuvable', 'error_code': 'USER_NOT_FOUND'}, status=404)
        messages_admin = messages_admin.filter(admin_repondant_id=admin_id)
        nom_admin_filtre = _nomAffiche(admin_filtre)

    messages_admin = messages_admin.select_related('vendeur', 'admin_repondant').order_by('date_reponse')

    # texte affiché exactement comme dans _serialiseMessageAdmin (déchiffrement
    # coffre_serveur transparent) — un message encore au format legacy
    # 'e2e_client' n'est PAS déchiffrable côté serveur : signalé comme tel
    # plutôt qu'omis, pour que le rapport reste honnête sur ce qu'il couvre
    entrees = []
    for m in messages_admin:
        reponse_chiffree = bool(m.reponse) and bool(m.iv_reponse or m.reponse_format_chiffrement == 'coffre_serveur')
        contenu, format_contenu = _champAffiche(m.contenu, m.chiffre, m.format_chiffrement)
        reponse, format_reponse = _champAffiche(m.reponse, reponse_chiffree, m.reponse_format_chiffrement)
        if format_contenu == 'e2e_client':
            contenu = '[message chiffré de bout en bout — non lisible côté serveur]'
        elif contenu is None:
            contenu = '[message illisible — clé de chiffrement invalide]'
        if format_reponse == 'e2e_client':
            reponse = '[réponse chiffrée de bout en bout — non lisible côté serveur]'
        elif reponse is None:
            reponse = '[réponse illisible — clé de chiffrement invalide]' if m.reponse else ''
        entrees.append({
            # viewer=utilisateur (qui génère ce rapport, forcément admin —
            # droit 'super_admin' déjà vérifié plus haut) : nom RÉEL, jamais
            # "Admin" générique dans un rapport d'audit interne
            'admin_nom':   _nomAffiche(m.admin_repondant, viewer=utilisateur),
            'admin_email': m.admin_repondant.email,
            'vendeur_nom': _nomAffiche(m.vendeur),
            'contenu':     contenu,
            'reponse':     reponse,
            'date_envoi':   m.date_envoi,
            'date_reponse': m.date_reponse,
        })

    enregistrer_audit(
        utilisateur, 'message_support.rapport_audit',
        f"A généré le rapport d'audit support ({date_debut} → {date_fin}, filtre : {nom_admin_filtre or 'tous les administrateurs'})",
    )

    from .services.rapport_support_service import generer_rapport_support
    pdf = generer_rapport_support(
        entrees=entrees, date_debut=date_debut, date_fin=date_fin,
        nom_admin_filtre=nom_admin_filtre,
    )

    return HttpResponse(pdf.read(), content_type='application/pdf')


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

    if not verifier_droit_admin(utilisateur, 'gestion_support'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_support'}}, status=403)

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

    # chiffré côté serveur ("coffre support"), même principe que
    # contacterAdmin — voir Messagerie/services/support_chiffrement_service.py
    try:
        reponse_stockee = chiffrer_support(reponse)
    except ErreurChiffrementSupport as e:
        return JsonResponse({'error': str(e), 'error_code': 'COFFRE_SUPPORT_INDISPONIBLE'}, status=503)

    try:
        message_admin = MessageSupport.objects.get(id=message_admin_id)
    except MessageSupport.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    from django.utils import timezone

    # UPDATE conditionnel : ne réussit que si personne n'a encore répondu —
    # empêche deux admins de "prendre" le même message en même temps
    maj = MessageSupport.objects.filter(id=message_admin_id, admin_repondant__isnull=True).update(
        admin_repondant=utilisateur, reponse=reponse_stockee, reponse_format_chiffrement='coffre_serveur',
        date_reponse=timezone.now(),
    )
    if maj == 0:
        message_admin.refresh_from_db()
        nom_autre_admin = _nomAffiche(message_admin.admin_repondant, viewer=utilisateur) if message_admin.admin_repondant_id else None
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
    enregistrer_audit(utilisateur, 'message_support.repondre', f"A répondu au message support de {_nomAffiche(message_admin.vendeur)} (message id {message_admin.id})")

    from Registration.services.notification_service import envoyer_email_decision, pied_de_page
    envoyer_email_decision(
        message_admin.vendeur, "Réponse de l'administration à votre message — RekoltHt",
        f"Bonjour {_nomAffiche(message_admin.vendeur)},\n\n"
        f"L'administration a répondu à votre message :\n\n{reponse}\n{pied_de_page(lien_demande_administrative=False)}",
    )

    return JsonResponse({
        'message_admin': _serialiseMessageAdmin(message_admin, utilisateur),
    }, status=200)


# ── MIGRATION D'UN MESSAGE LEGACY VERS LE COFFRE SUPPORT (admin connecté) ─────
@csrf_exempt
def migrerMessageVersCoffre(request):
    """
    Fait passer le contenu et/ou la réponse d'un message legacy
    (format_chiffrement == 'e2e_client') vers le coffre support
    (format_chiffrement == 'coffre_serveur') — voir MessageSupport,
    Messagerie/models.py. Appelée automatiquement par AdminDashboard.jsx dès
    qu'un admin parvient à déchiffrer un message legacy côté client (il a
    donc la preuve qu'il possède la bonne clé E2E) : une fois migré, TOUT
    admin gestion_support (même arrivé après l'envoi) peut ensuite le lire
    directement, sans plus jamais dépendre de cette clé ni d'un appareil
    précis. Le texte en clair transite en HTTPS le temps de cet appel,
    jamais stocké autrement que re-chiffré via le coffre serveur.
    Idempotente : un champ déjà au format coffre_serveur est ignoré.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_support'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_support'}}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    message_admin_id = data.get('id')
    if not message_admin_id:
        return JsonResponse({'error': 'Le champ id est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'id'}}, status=400)

    try:
        message_admin = MessageSupport.objects.get(id=message_admin_id)
    except MessageSupport.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    try:
        champs_maj = {}
        contenu_dechiffre = (data.get('contenu_dechiffre') or '').strip()
        if contenu_dechiffre and message_admin.format_chiffrement != 'coffre_serveur':
            champs_maj['contenu'] = chiffrer_support(contenu_dechiffre)
            champs_maj['format_chiffrement'] = 'coffre_serveur'

        reponse_dechiffree = (data.get('reponse_dechiffree') or '').strip()
        if reponse_dechiffree and message_admin.reponse and message_admin.reponse_format_chiffrement != 'coffre_serveur':
            champs_maj['reponse'] = chiffrer_support(reponse_dechiffree)
            champs_maj['reponse_format_chiffrement'] = 'coffre_serveur'
    except ErreurChiffrementSupport as e:
        return JsonResponse({'error': str(e), 'error_code': 'COFFRE_SUPPORT_INDISPONIBLE'}, status=503)

    if not champs_maj:
        return JsonResponse({'message': 'Rien à migrer', 'message_admin': _serialiseMessageAdmin(message_admin, utilisateur)}, status=200)

    for champ, valeur in champs_maj.items():
        setattr(message_admin, champ, valeur)
    message_admin.save(update_fields=list(champs_maj.keys()))

    enregistrer_audit(
        utilisateur, 'message_support.migrer_coffre',
        f"A fait basculer le message support id {message_admin.id} vers le coffre support ({', '.join(champs_maj.keys())})"
    )

    return JsonResponse({
        'message': 'Message migré vers le coffre support',
        'message_admin': _serialiseMessageAdmin(message_admin, utilisateur),
    }, status=200)


# ── SIGNALER UN MESSAGE (participant de la conversation) ──────────────────────
def _serialiseSignalementMessage(signalement):
    message = signalement.message
    # contenu_signale = copie prise au moment du signalement (voir
    # signalerMessage) — reste lisible même si le message d'origine est
    # ensuite supprimé par la modération (supprimerMessageAdmin). Repli sur
    # le déchiffrement à la volée pour un signalement antérieur à
    # l'introduction de cette copie.
    message_contenu = signalement.contenu_signale or _contenuMessageAffiche(message)
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
        'admin_traitant_id':  signalement.admin_traitant_id,
        # signalements = endpoints 100% réservés aux admins, jamais consultés
        # par un vendeur/acheteur — nom réel toujours affiché ici (pas de
        # viewer à passer, contrairement à _serialiseMessageAdmin), email
        # ajouté pour la même raison que Produits/views/signalementsViews.py
        'admin_traitant_nom':   _nomAffiche(signalement.admin_traitant) if signalement.admin_traitant_id else None,
        'admin_traitant_email': signalement.admin_traitant.email if signalement.admin_traitant_id else None,
        'date_traitement':   signalement.date_traitement.isoformat() if signalement.date_traitement else None,
        'explication_decision': signalement.explication_decision,
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

    try:
        message = Message.objects.select_related('conversation').get(id=message_id)
    except Message.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    conversation = message.conversation
    if utilisateur.id not in (conversation.participant_a_id, conversation.participant_b_id):
        return JsonResponse({'error': "Vous n'avez pas accès à ce message"}, status=403)

    if message.expediteur_id == utilisateur.id:
        return JsonResponse({'error': 'Vous ne pouvez pas signaler votre propre message'}, status=400)

    # copie en clair prise au moment du signalement — déchiffrée côté SERVEUR
    # (voir _contenuMessageAffiche plus haut) : reste lisible pour la
    # modération même si le message d'origine est ensuite supprimé
    # (supprimerMessageAdmin ci-dessous)
    signalement = SignalementMessage.objects.create(
        message=message, signaleur=utilisateur, type_probleme=type_probleme, motif=motif,
        contenu_signale=_contenuMessageAffiche(message),
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


# ── SIGNALEMENTS DE MESSAGES DÉJÀ TRAITÉS (admin) ─────────────────────────────
@csrf_exempt
def listerSignalementsMessagesTraites(request):
    """Historique des signalements de messages déjà traités — MOI SEUL par
    défaut, TOUT LE MONDE pour un compte "Tous les droits" ou le propriétaire
    (voir listerMessagesAdminRepondus ci-dessus, même principe). Exclut aussi
    ce que MOI j'ai retiré de MON historique (historique_masque_pour,
    SignalementMessage) — chaque admin a son propre historique."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis", 'error_code': 'AUTH_TOKEN_REQUIRED'}, status=401)

    if not verifier_droit_admin(utilisateur, 'gestion_signalements'):
        return JsonResponse({'error': "Ce droit administrateur est requis", 'error_code': 'DROIT_REQUIS', 'error_params': {'droit': 'gestion_signalements'}}, status=403)

    signalements = SignalementMessage.objects.filter(admin_traitant__isnull=False).exclude(historique_masque_pour=utilisateur)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)
    signalements = signalements.select_related('message', 'message__expediteur', 'signaleur', 'admin_traitant').order_by('-date_traitement')

    return JsonResponse({
        'signalements': [_serialiseSignalementMessage(s) for s in signalements],
    }, status=200)


# ── RETIRER DE MON HISTORIQUE DES SIGNALEMENTS DE MESSAGES (admin connecté) ──
@csrf_exempt
def supprimerHistoriqueSignalementsMessages(request):
    """
    Retire une ou plusieurs entrées du SIGNALEMENT (pas le message privé
    lui-même, qui reste intact dans la conversation — distinct de
    supprimerMessageAdmin plus bas, qui supprime le message ET son
    signalement en cascade) de MON historique SEULEMENT
    (historique_masque_pour) — jamais un vrai delete(), même principe que
    supprimerHistoriqueMessagesSupport ci-dessus : chaque admin a son propre
    historique. Portée de sélection = ce que l'admin peut déjà voir (moi
    seul, sauf Tous les droits/propriétaire).
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

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    signalements = SignalementMessage.objects.filter(id__in=ids, admin_traitant__isnull=False)
    if not verifier_droit_admin(utilisateur, 'super_admin'):
        signalements = signalements.filter(admin_traitant=utilisateur)

    nombre_masque = 0
    for signalement in signalements:
        signalement.historique_masque_pour.add(utilisateur)
        nombre_masque += 1

    enregistrer_audit(utilisateur, 'signalement_message.masquer_historique', f"A retiré {nombre_masque} entrée(s) de son historique des signalements de messages")

    return JsonResponse({'nombre_supprime': nombre_masque}, status=200)


# ── TRAITER UN OU PLUSIEURS SIGNALEMENTS DE MESSAGE SANS SUPPRIMER (admin) ───
@csrf_exempt
def traiterSignalementMessage(request):
    """
    Marque un ou plusieurs signalements du MÊME message comme traités en un
    seul geste, SANS supprimer le message (l'admin a jugé que le contenu ne
    pose pas de problème) — pour supprimer le message, voir
    supprimerMessageAdmin ci-dessous. Même principe de regroupement par
    cible et d'explication obligatoire que
    Produits/views/signalementsViews.py::traiterSignalement.
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

    ids = data.get('ids')
    if not isinstance(ids, list) or not ids:
        return JsonResponse({'error': 'Le champ ids (liste non vide) est requis', 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'ids'}}, status=400)

    explication = (data.get('explication') or '').strip()
    if not explication:
        return JsonResponse({'error': "Le champ explication est requis", 'error_code': 'FIELD_REQUIRED', 'error_params': {'champ': 'explication'}}, status=400)

    from django.utils import timezone

    signalements_cibles = SignalementMessage.objects.filter(id__in=ids)
    if not signalements_cibles.exists():
        return JsonResponse({'error': 'Signalement introuvable', 'error_code': 'REPORT_NOT_FOUND'}, status=404)

    ids_deja_traites = list(signalements_cibles.filter(admin_traitant__isnull=False).values_list('id', flat=True))

    nombre_maj = SignalementMessage.objects.filter(id__in=ids, admin_traitant__isnull=True).update(
        admin_traitant=utilisateur, date_traitement=timezone.now(), explication_decision=explication
    )
    if nombre_maj == 0:
        return JsonResponse({'error': 'Ce ou ces signalements ont déjà été traités'}, status=409)

    signalements_traites = list(
        SignalementMessage.objects.filter(id__in=ids, admin_traitant=utilisateur).select_related('message', 'message__expediteur', 'signaleur')
    )

    from Api.broadcast import broadcast_to_admins
    for signalement in signalements_traites:
        broadcast_to_admins('signalement_message.traite', {'id': signalement.id})

    enregistrer_audit(
        utilisateur, 'signalement_message.traiter',
        f"A traité {nombre_maj} signalement(s) de message (ids {ids}) — Raison : {explication}"
    )

    return JsonResponse({
        'signalements': [_serialiseSignalementMessage(s) for s in signalements_traites],
        'ids_deja_traites': ids_deja_traites,
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
        message = Message.objects.select_related('expediteur').get(id=message_id)
    except Message.DoesNotExist:
        return JsonResponse({'error': 'Message introuvable'}, status=404)

    raison = (data.get('raison') or '').strip()

    # capturés avant suppression : plus accessibles une fois message.delete() exécuté
    signalements_lies = list(message.signalements.filter(admin_traitant__isnull=True).values_list('id', flat=True))
    expediteur = message.expediteur

    message.delete()  # déclenche broadcast_message_supprime (Messagerie/signals.py) vers les 2 participants
    enregistrer_audit(utilisateur, 'message.supprimer', f"A supprimé le message signalé id {message_id}")

    # notifie l'auteur du message par email — jusqu'ici la seule notification
    # était le broadcast temps réel ci-dessus (retire juste le message du fil
    # ouvert), rien n'informait l'auteur si sa session n'était pas active au
    # même moment (demande explicite : toute action admin sur un compte doit
    # être notifiée par email). Le contenu du message n'est jamais cité : il
    # est chiffré de bout en bout, le serveur n'y a jamais accès en clair.
    from Registration.services.notification_service import envoyer_email_decision, pied_de_page
    envoyer_email_decision(
        expediteur, "Un de vos messages a été supprimé — RekoltHt",
        f"Bonjour {_nomAffiche(expediteur)},\n\n"
        f"Suite à un signalement, l'un de vos messages a été supprimé par l'administration."
        + (f"\n\nMotif : {raison}" if raison else "")
        + pied_de_page(),
    )

    from Api.broadcast import broadcast_to_admins
    # retire ce signalement (et tout autre signalement en attente sur ce même
    # message) de la file des admins — CASCADE l'a déjà supprimé en base
    for signalement_id in signalements_lies:
        broadcast_to_admins('signalement_message.traite', {'id': signalement_id})

    return JsonResponse({'message': 'Message supprimé avec succès'}, status=200)


# ── ASSISTANT IA DU CHATBOT ────────────────────────────────────────────────
def _adresse_ip_client(request):
    """Adresse IP du visiteur, en tenant compte d'un éventuel proxy inverse
    (Render est derrière un load-balancer : REMOTE_ADDR seul y vaudrait
    l'adresse interne du proxy, pas celle du visiteur)."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'inconnue')


# nombre max de questions par adresse IP sur la fenêtre ci-dessous — chaque
# appel a un coût réel (API OpenRouter payante, voir chatbot_ia_service.py) et
# l'endpoint est accessible
# SANS connexion (le widget est visible sur tout le site) : seule protection
# contre un abus grossier (script qui spam l'endpoint), pas une garantie
# stricte d'usage normal (un foyer/bureau derrière la même IP la partage)
LIMITE_QUESTIONS_IA = 20
FENETRE_LIMITE_IA_SECONDES = 600  # 10 minutes


def _limite_ia_atteinte(ip):
    """Compteur glissant approximatif via le cache Django (mémoire locale par
    défaut sur ce projet — best-effort, pas garanti entre plusieurs instances
    du serveur, largement suffisant pour ce qu'on cherche à éviter ici)."""
    cle = f'chatbot_ia_limite_{ip}'
    compteur = cache.get(cle, 0)
    if compteur >= LIMITE_QUESTIONS_IA:
        return True
    cache.set(cle, compteur + 1, timeout=FENETRE_LIMITE_IA_SECONDES)
    return False


@csrf_exempt
def chatbotRepondre(request):
    """
    Assistant IA du chatbot (voir ChatbotVendeur.jsx, remplace l'ancien
    recoupement de mots-clés utils/faqMatcher.js côté frontend, retiré).
    Accessible SANS connexion, comme le widget lui-même (contrairement à
    contacterAdmin, réservé aux comptes connectés) — protégé uniquement par
    la limite de requêtes par IP ci-dessus vu le coût réel de chaque appel.

    Corps attendu : {
      question : str, non vide, courte (voir garde-fou ci-dessous),
      contexte : str, bloc de documentation déjà assemblé et traduit côté
                 frontend (Centre d'aide + À propos + Conditions, voir
                 ChatbotVendeur.jsx) — jamais interprété comme des
                 instructions par le modèle (voir chatbot_ia_service.py),
      historique : [{role: "user"|"assistant", contenu: str}, ...] optionnel,
                    tronqué ici par sécurité même si déjà tronqué côté appelant.
    }
    Réponse 200 : { reponse: str, hors_sujet: bool } — voir chatbot_ia_service.py.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée', 'error_code': 'METHOD_NOT_ALLOWED'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide', 'error_code': 'INVALID_JSON_BODY'}, status=400)

    question = (data.get('question') or '').strip()
    if not question:
        return JsonResponse({'error': 'La question ne peut pas être vide', 'error_code': 'QUESTION_VIDE'}, status=400)
    if len(question) > 800:
        return JsonResponse({'error': 'Question trop longue', 'error_code': 'QUESTION_TROP_LONGUE'}, status=400)

    contexte = (data.get('contexte') or '')[:20000]  # garde-fou, même si déjà raisonnable côté frontend

    historique_brut = data.get('historique')
    historique = historique_brut[-12:] if isinstance(historique_brut, list) else []

    if _limite_ia_atteinte(_adresse_ip_client(request)):
        return JsonResponse({'error': 'Trop de questions envoyées, réessayez dans quelques minutes', 'error_code': 'TROP_DE_REQUETES'}, status=429)

    try:
        resultat = obtenir_reponse_ia(question, contexte, historique)
    except ErreurChatbotIA:
        # jamais le détail technique au client — juste de quoi dégrader
        # proprement vers l'escalade côté frontend (voir ChatbotVendeur.jsx)
        return JsonResponse({'error': "L'assistant n'est pas disponible pour le moment", 'error_code': 'IA_INDISPONIBLE'}, status=503)

    return JsonResponse(resultat, status=200)