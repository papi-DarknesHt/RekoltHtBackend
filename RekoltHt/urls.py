from django.urls import path
from . import views
from Registration import views as registration_views # Import views from Registration app

urlpatterns = [
    path("test/", views.test_conn), # Assuming this is also an API endpoint
    path('register/', registration_views.register, name='api_register'), # Removed 'api/' prefix
    path('login/', registration_views.user_login, name='api_login'),     # Removed 'api/' prefix
    path('logout/', registration_views.user_logout, name='api_logout'),   # Removed 'api/' prefix
]
