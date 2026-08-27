from .models import NotaFiscal
from .permissions import GRUPO_CD, em_grupo, eh_admin, eh_motorista, tem_acesso_total


def perfil_contexto(request):
    """Disponibiliza os booleans de perfil e o contador de pendências em todo
    template — usado na navbar (base.html) para mostrar/esconder cada seção
    do menu conforme o perfil logado."""
    if not request.user.is_authenticated:
        return {}

    acesso_total = tem_acesso_total(request.user)
    contexto = {
        "perms_acesso_total": acesso_total,
        "perms_admin": eh_admin(request.user),
        "perms_cd": em_grupo(request.user, GRUPO_CD) and not acesso_total,
        "perms_motorista": eh_motorista(request.user) and not acesso_total,
    }
    if acesso_total:
        contexto["contador_pendencias"] = NotaFiscal.objects.filter(status="pendente").count()
    return contexto
