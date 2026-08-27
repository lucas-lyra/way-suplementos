"""Controle de acesso por perfil, usando django.contrib.auth.Group como perfil
(cd / compra_venda / admin / motorista), conforme pedido no briefing ("usar
sistema de auth do Django + grupos/permissions").

compra_venda e Coordenador/Admin têm exatamente o mesmo nível de acesso hoje
(recebimento direto, remanejamento, rotas, aprovação/rejeição, gestão de
lojas e usuários) — os dois nomes de grupo continuam existindo separados só
para refletir o cargo da pessoa na empresa, não uma diferença de permissão."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

GRUPO_CD = "CD"
GRUPO_COMPRA_VENDA = "Compra e Venda"
GRUPO_ADMIN = "Coordenador/Admin"
GRUPO_MOTORISTA = "Motorista"

GRUPOS_ACESSO_TOTAL = (GRUPO_COMPRA_VENDA, GRUPO_ADMIN)


def em_grupo(user, *nomes_grupos):
    """True se o usuário pertence a algum dos grupos informados. Superusuário
    (ex: criado via createsuperuser) sempre tem acesso, como convenção padrão
    do Django."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=nomes_grupos).exists()


def tem_acesso_total(user):
    """compra_venda e admin: recebimento direto, remanejamento, retirada,
    aprova/rejeita. Regra central do briefing."""
    return em_grupo(user, *GRUPOS_ACESSO_TOTAL)


def eh_admin(user):
    return em_grupo(user, GRUPO_ADMIN)


def eh_motorista(user):
    return em_grupo(user, GRUPO_MOTORISTA)


def perfil_required(*nomes_grupos):
    """Decorator de view: exige login E que o usuário esteja em algum dos
    grupos informados. Quem não tem o perfil recebe 403 (PermissionDenied)."""
    def decorador(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not em_grupo(request.user, *nomes_grupos):
                raise PermissionDenied("Seu perfil não tem acesso a esta tela.")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorador
