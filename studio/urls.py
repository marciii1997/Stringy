from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("anmelden/", LoginView.as_view(), name="login"),
    path("abmelden/", LogoutView.as_view(), name="logout"),
    path("verwaltung/", admin.site.urls),
    path("kalender/<int:pk>/", views.calendar, name="calendar"),
    path("kalender/<int:pk>/<int:day>/", views.door, name="door"),
    path("kalender/<int:pk>/<int:day>/fertig/", views.complete, name="complete"),
    path("atelier/<int:pk>/", views.simulation, name="simulation"),
    path("kalender/<int:pk>/<int:day>/schritt/", views.thread_step, name="thread_step"),
    path("atelier/<int:pk>/neu-berechnen/", views.recalculate, name="recalculate"),
    path("atelier/<int:pk>/laser/", views.laser, name="laser"),
    path("atelier/<int:pk>/<str:kind>/", views.asset, name="asset"),
]
