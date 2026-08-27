from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, User

from .models import Loja


def _select(extra=""):
    return forms.Select(attrs={"class": f"form-select {extra}".strip()})


def _text(extra=""):
    return forms.TextInput(attrs={"class": f"form-control {extra}".strip()})


class EntradaProdutoExistenteForm(forms.Form):
    """Usada quando o código de barras já é reconhecido no catálogo — só pede
    os dados do lote."""
    lote = forms.CharField(label="Número do Lote", required=False, widget=_text())
    validade = forms.DateField(
        label="Validade",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    quantidade = forms.IntegerField(
        label="Quantidade", min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    loja_destino = forms.ModelChoiceField(
        label="Loja/CD de Destino", queryset=Loja.objects.all().order_by("nome"), widget=_select(),
    )
    observacoes = forms.CharField(label="Observações", required=False, widget=_text())


class EntradaProdutoNovoForm(EntradaProdutoExistenteForm):
    """Usada quando o código de barras ainda não existe — pede também o
    cadastro do produto (1º recebimento dele)."""
    nome = forms.CharField(label="Nome do Produto", widget=_text())
    marca = forms.CharField(label="Marca", required=False, widget=_text())

    field_order = ["nome", "marca", "lote", "validade", "quantidade", "loja_destino", "observacoes"]


class RemanejamentoForm(forms.Form):
    loja_destino = forms.ModelChoiceField(
        label="Loja de Destino", queryset=Loja.objects.all().order_by("nome"), widget=_select(),
    )
    quantidade = forms.IntegerField(
        label="Quantidade a Remanejar", min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    observacoes = forms.CharField(label="Observações / Guia de Transferência", required=False, widget=_text())


class UsuarioPerfilForm(forms.Form):
    grupo = forms.ModelChoiceField(label="Perfil", queryset=Group.objects.all(), widget=_select())
    ativo = forms.BooleanField(
        label="Conta ativa", required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class CadastroForm(UserCreationForm):
    """Auto-cadastro. A conta nasce sem perfil e travada (ativo=False) — o
    primeiro cadastro do sistema é a única exceção (vira admin automático na
    view, ver estoque/views.py:cadastro)."""

    class Meta:
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nome_campo in self.fields:
            self.fields[nome_campo].widget.attrs["class"] = "form-control"
