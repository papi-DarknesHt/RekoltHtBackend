from django.urls import path
from . import views

# Toutes les routes sont préfixées par /Registration/ (défini dans BackendRekoltHt/urls.py)
urlpatterns = [

    # ── AUTHENTIFICATION CLASSIQUE ────────────────────────────────────────────
    path('inscription/',          views.sinscrire),           # POST  — créer un compte
    path('connexion/',            views.seConnecter),         # POST  — se connecter (email + mdp)
    path('deconnexion/',          views.seDeconnecter),       # POST  — se déconnecter (supprime le token)

    # ── AUTHENTIFICATION GOOGLE OAUTH2 ────────────────────────────────────────
    path('google/connexion/',     views.google_connection),   # POST  — connexion via Google
    path('google/inscription/',   views.google_inscription),  # POST  — inscription via Google

    # ── PROFIL UTILISATEUR ────────────────────────────────────────────────────
    path('profil/',               views.profilAfficher),       # GET   — afficher profil + utilisateur
    path('modifier-utilisateur/', views.modifierUtilisateur),  # PUT   — modifier nom, prénom, email, tél.
    path('modifier-profil/',      views.modifierProfil),       # PUT   — modifier bio, photo, adresse, rôle…
    path('modifier-mdp/',         views.modifierMotDePasse),   # PUT   — changer le mot de passe
    path('supprimer-photo-profil/', views.supprimerPhotoProfil), # DELETE — supprimer la photo de profil

    # ── ENTREPRISE ────────────────────────────────────────────────────────────
    path('entreprise/verifier/',       views.verifierEntreprise),       # GET    — vérifier si une entreprise existe (avant création)
    path('entreprise/creer/',          views.creerEntreprise),          # POST   — créer une entreprise
    path('entreprise/lister/',         views.listerEntreprises),        # GET    — lister les entreprises de l'utilisateur
    path('entreprise/modifier/',       views.modifierEntreprise),       # PUT    — modifier une entreprise
    path('entreprise/supprimer/',      views.supprimerEntreprise),      # DELETE — supprimer une entreprise
    path('entreprise/supprimer-logo/', views.supprimerLogoEntreprise),  # DELETE — supprimer uniquement le logo

    # ── VÉRIFICATION VENDEUR (KYC) ────────────────────────────────────────────
    path('verification/soumettre/',    views.soumettre_verification),  # POST — soumettre/mettre à jour le dossier KYC
    path('verification/statut/',       views.statut_verification),     # GET  — statut courant + motif d'échec
    path('verification/previsualiser/', views.previsualiser_contrat),  # POST — aperçu PDF du contrat, avant envoi définitif

    # ── ADMINISTRATION ────────────────────────────────────────────────────────
    path('admin/utilisateurs/', views.listerUtilisateursAdmin),   # GET — liste tous les utilisateurs (rôle admin requis)
    path('admin/utilisateurs/bloquer/', views.toggleBloquerUtilisateur),  # PUT — bloque/débloque un compte (rôle admin requis)
<<<<<<< Updated upstream
    path('admin/utilisateurs/nommer-admin/', views.nommerAdminUtilisateur),  # PUT — nomme un compte administrateur (rôle admin requis)
    path('admin/verifications-entreprise/', views.lister_demandes_admin),  # GET — demandes entreprise en attente (rôle admin requis)
=======
    path('admin/utilisateurs/supprimer/', views.supprimerUtilisateurAdmin),  # DELETE — supprime définitivement un compte (rôle admin requis)
    path('admin/utilisateurs/reactiver-vendeur/', views.reactiverVendeurAdmin),  # PUT — lève la suspension par signalements d'un vendeur (droit gestion_utilisateurs requis)
    path('admin/verifications-entreprise/', views.lister_demandes_admin),  # GET — demandes entreprise en attente (droit gestion_verifications requis)
>>>>>>> Stashed changes
    path('admin/dashboard/',    views.dashboardAdmin),             # GET — statistiques agrégées (rôle admin requis)

    # ── GESTION DES ADMs (super admin requis, voir DroitsAdmin) ──────────────
    path('admin/adms/',                  views.listerAdmins),         # GET  — liste des comptes admin + leurs droits
    path('admin/adms/creer/',            views.creerAdmin),           # POST — crée un nouveau compte admin
    path('admin/adms/promouvoir/',       views.promouvoirAdmin),      # PUT  — promeut un utilisateur existant en admin
    path('admin/adms/modifier-droits/',  views.modifierDroitsAdmin),  # PUT  — change les droits d'un admin existant
    path('admin/adms/revoquer/',         views.revoquerAdmin),        # PUT  — rétrograde un admin en acheteur
    path('admin/adms/modifier-infos/',   views.modifierInfosAdmin),   # PUT  — modifie nom/prénom/email/téléphone d'un autre admin
    path('admin/adms/reinitialiser-mdp/', views.reinitialiserMotDePasseAdmin),  # PUT — force le changement de mdp à la prochaine connexion
    path('admin/adms/rapport-audit/',    views.genererRapportAudit),  # GET  — rapport PDF du journal d'audit (?date_debut=&date_fin=&admin_id=)

    # ── RÉINITIALISATION DU MOT DE PASSE ─────────────────────────────────────
    path('reinitialisation/demander/',      views.demanderReinitialisation),      # POST — envoyer le code PIN par email
    path('reinitialisation/verifier-code/', views.verifierCodeReinitialisation),  # POST — vérifier le code sans changer le mdp
    path('reinitialisation/valider/',       views.reinitialiserMotDePasse),       # POST — changer le mdp après validation du code
]
