from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "estoque"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="estoque/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="estoque:login"), name="logout"),
    path("cadastro/", views.cadastro, name="cadastro"),

    path("", views.recebimento, name="recebimento"),
    path("remanejamento/", views.remanejamento, name="remanejamento"),
    path("planilha/", views.planilha, name="planilha"),
    path("pendencias/", views.pendencias, name="pendencias"),
    path("usuarios/", views.usuarios, name="usuarios"),
]
