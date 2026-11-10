import os

from django.db import connection
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok", "release": os.getenv("RELEASE_TAG", "unknown")})


urlpatterns = [
    path("api/health/", health),
    path("", include("config.urls")),
]
