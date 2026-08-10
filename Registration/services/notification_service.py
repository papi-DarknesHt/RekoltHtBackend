from django.conf import settings
from django.core.mail import send_mail


def envoyer_email_decision(utilisateur, sujet, corps):
    """
    Envoie un email à un utilisateur pour l'informer d'une décision admin le
    concernant (blocage/déblocage de compte, désactivation de produit,
    suppression d'avis, réponse à un message support...). Même pattern
    try/except que DemandeVerification.marquer_verifie/marquer_echoue
    (Registration/models.py) : un échec d'envoi (SMTP injoignable, etc.) ne
    doit JAMAIS faire échouer la décision déjà persistée en base — elle est
    déjà appliquée avant cet appel, ce n'est qu'une notification.
    """
    try:
        send_mail(
            subject=sujet,
            message=corps,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[utilisateur.email],
            fail_silently=False,
        )
    except Exception as e:
        print(f"[email décision] échec envoi à {utilisateur.email} : {e}")


def pied_de_page(lien_demande_administrative=True):
    """Formule de fin commune à tous les emails de décision — inclut le lien
    vers la page "Demande à l'administration" (voir DemandeAdministrative,
    Registration/models.py) quand la décision peut être contestée."""
    corps = ""
    if lien_demande_administrative:
        corps += (
            f"\nSi vous souhaitez faire un suivi ou contester cette décision, "
            f"contactez l'administration : {settings.FRONTEND_URL}/demande-administration\n"
        )
    return corps + "\nL'équipe RekoltHt"
