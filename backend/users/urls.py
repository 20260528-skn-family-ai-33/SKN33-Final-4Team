from django.urls import path

from config.screens import screen

app_name = "users"

urlpatterns = [
    path("login/", screen("users/login.html", next_url="home"), name="login"),
    path("signup/", screen("users/signup.html", next_url="users:onboard"), name="signup"),
    path("start/", screen("users/onboard.html"), name="onboard"),
    path("profile/", screen("users/profile.html", next_url="finance:connect"), name="profile"),
    path("my/", screen("users/my.html"), name="my"),
]
