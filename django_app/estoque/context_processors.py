from .permissions import eh_admin, tem_acesso_total


def perfil_contexto(request):
    """Disponibiliza os booleans de perfil em todo template (usado na navbar em
    base.html para mostrar/esconder links conforme o perfil logado)."""
    if not request.user.is_authenticated:
        return {}
    return {
        "perms_acesso_total": tem_acesso_total(request.user),
        "perms_admin": eh_admin(request.user),
    }
