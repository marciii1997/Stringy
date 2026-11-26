from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import motif_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("anmelden/", LoginView.as_view(), name="login"),
    path("abmelden/", LogoutView.as_view(), name="logout"),
    path("verwaltung/", admin.site.urls),
    path("kalender/<int:pk>/", views.calendar, name="calendar"),
    path("kalender/<int:pk>/verschenken/", views.publish_calendar, name="publish_calendar"),
    path("kalender/<int:pk>/<int:day>/", views.door, name="door"),
    path("kalender/<int:pk>/<int:day>/fertig/", views.complete, name="complete"),
    path("atelier/", views.atelier, name="atelier"),
    path("atelier/foto/", views.upload, name="upload"),
    path("atelier/<int:pk>/verschenken/", views.give, name="give"),
    path("atelier/<int:pk>/", views.simulation, name="simulation"),
    path("kalender/<int:pk>/<int:day>/schritt/", views.thread_step, name="thread_step"),
    path("atelier/<int:pk>/neu-berechnen/", views.recalculate, name="recalculate"),
    path("atelier/<int:pk>/version/", views.new_version, name="new_version"),
    path("atelier/<int:pk>/handfaedeln/", views.handcraft_variant, name="handcraft"),
    path("atelier/<int:pk>/motiv/", motif_views.editor, name="motif"),
    path("atelier/vergleich/<uuid:group>/", motif_views.comparison, name="comparison"),
    path("atelier/<int:pk>/laser/", views.laser, name="laser"),
    path("atelier/<int:pk>/<str:kind>/", views.asset, name="asset"),
]
