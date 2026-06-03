from django.shortcuts import render
from django.http import JsonResponse
# Create your views here.
def test_conn(request):
    return JsonResponse({
        "message": "Bonjour test",
        "status": "ok",
        "app": "myapp"
    })
