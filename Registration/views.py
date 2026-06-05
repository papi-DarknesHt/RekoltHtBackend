from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.authtoken.models import Token


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')
    email    = request.data.get('email', '').strip()
    role     = request.data.get('role', 'acheteur')

    if not username or not password:
        return Response({'error': 'Username et password sont requis'}, status=status.HTTP_400_BAD_REQUEST)
    if len(password) < 6:
        return Response({'error': 'Le mot de passe doit avoir au moins 6 caractères'}, status=status.HTTP_400_BAD_REQUEST)
    if User.objects.filter(username=username).exists():
        return Response({'error': "Ce nom d'utilisateur existe déjà"}, status=status.HTTP_400_BAD_REQUEST)
    if email and User.objects.filter(email=email).exists():
        return Response({'error': 'Cet email est déjà utilisé'}, status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.create_user(username=username, email=email, password=password)
    user.first_name = role
    user.save()

    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'message': 'Inscription réussie',
        'token': token.key,
        'user': {
            'id':       user.id,
            'username': user.username,
            'email':    user.email,
            'role':     role,
        }
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def user_login(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')

    if not username or not password:
        return Response({'error': 'Username et password sont requis'}, status=status.HTTP_400_BAD_REQUEST)

    user = authenticate(request, username=username, password=password)

    if user is None:
        return Response({'error': 'Identifiants incorrects'}, status=status.HTTP_401_UNAUTHORIZED)
    if not user.is_active:
        return Response({'error': 'Ce compte est désactivé'}, status=status.HTTP_403_FORBIDDEN)

    login(request, user)
    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'message': 'Connexion réussie',
        'token':   token.key,
        'user': {
            'id':       user.id,
            'username': user.username,
            'email':    user.email,
            'role':     user.first_name or 'acheteur',
        }
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def user_logout(request):
    try:
        request.user.auth_token.delete()
    except Token.DoesNotExist:
        pass
    logout(request)
    return Response({'message': 'Déconnexion réussie'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_profile(request):
    user = request.user
    return Response({
        'user': {
            'id':          user.id,
            'username':    user.username,
            'email':       user.email,
            'role':        user.first_name or 'acheteur',
            'date_joined': user.date_joined.isoformat(),
        }
    }, status=status.HTTP_200_OK)