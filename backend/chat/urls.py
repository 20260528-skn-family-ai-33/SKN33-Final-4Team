from django.urls import path

from config.screens import popup

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.assistant, name="assistant"),
    # 팝업
    path("popup/adjust/", popup("chat/_adjust.html"), name="pop_adjust"),
]
