from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [

    # ── INTERFACE D'ADMINISTRATION DJANGO ─────────────────────────────────────
    # Accessible uniquement aux superutilisateurs via /admin/
    path('admin/', admin.site.urls),

    # ── API PRINCIPALE (RekoltHt) ─────────────────────────────────────────────
    # Routes définies dans RekoltHt/urls.py — ex: /api/test/
    path('api/', include("RekoltHt.urls")),

    # ── AUTHENTIFICATION ET GESTION DES COMPTES ───────────────────────────────
    # Routes définies dans Registration/urls.py
    # Ex: /Registration/inscription/, /Registration/connexion/, /Registration/profil/…
    path('Registration/', include('Registration.urls')),

    # ── AUTHENTIFICATION SOCIALE (Google OAuth2) ──────────────────────────────
    # Géré par social-auth-app-django (social_django)
    # Ex: /auth/login/google-oauth2/ → redirige vers la page de connexion Google
    path('auth/', include('social_django.urls', namespace='social')),

]

# ── FICHIERS MÉDIAS EN DÉVELOPPEMENT ──────────────────────────────────────────
# En production, les fichiers médias (photos, logos) doivent être servis
# par le serveur web (nginx, Render…). En développement, Django les sert directement.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
