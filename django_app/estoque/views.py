from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    CadastroForm,
    EntradaProdutoExistenteForm,
    EntradaProdutoNovoForm,
    RemanejamentoForm,
    UsuarioPerfilForm,
)
from .models import Lote, Loja, Movimentacao, Produto
from .permissions import GRUPO_ADMIN, GRUPO_CD, GRUPOS_ACESSO_TOTAL, em_grupo, perfil_required, tem_acesso_total

User = get_user_model()


def cadastro(request):
    """Auto-cadastro de conta. Se for o PRIMEIRO usuário do sistema, vira admin
    automaticamente (bootstrap — senão ninguém teria perfil para liberar os
    próximos). Qualquer cadastro seguinte nasce sem perfil e travado
    (is_active=False) até um admin liberar em Gestão de Usuários."""
    if request.method == "POST":
        form = CadastroForm(request.POST)
        if form.is_valid():
            eh_primeiro_usuario = not User.objects.exists()

            with transaction.atomic():
                novo_usuario = form.save(commit=False)
                novo_usuario.is_active = eh_primeiro_usuario
                novo_usuario.is_superuser = eh_primeiro_usuario
                novo_usuario.is_staff = eh_primeiro_usuario
                novo_usuario.save()
                if eh_primeiro_usuario:
                    grupo_admin, _ = Group.objects.get_or_create(name=GRUPO_ADMIN)
                    novo_usuario.groups.add(grupo_admin)

            if eh_primeiro_usuario:
                messages.success(request, "Conta criada como administrador (primeiro usuário do sistema)! Já pode entrar.")
            else:
                messages.info(request, "Conta criada! Peça para um administrador liberar seu acesso antes de entrar.")
            return redirect("estoque:login")
    else:
        form = CadastroForm()

    return render(request, "estoque/cadastro.html", {"form": form})


def _obter_ou_criar_lote(produto, lote_txt, validade, loja, quantidade, status, usuario):
    """Busca um lote já existente com mesmo produto+lote+validade+loja+status e
    soma a quantidade a ele; se não existir, cria um novo registro. O filtro por
    status é essencial: uma entrada pendente nunca deve se juntar a um lote já
    aprovado (e vice-versa), senão quantidade não aprovada passaria a contar
    como estoque real."""
    lote_existente = Lote.objects.filter(
        produto=produto, validade=validade, loja_atual=loja, status=status, lote=lote_txt
    ).first()
    if lote_existente:
        lote_existente.quantidade += quantidade
        lote_existente.save(update_fields=["quantidade"])
        return lote_existente

    return Lote.objects.create(
        produto=produto, lote=lote_txt, validade=validade, loja_atual=loja,
        quantidade=quantidade, status=status,
        criado_por=usuario.get_username(), criado_em=timezone.now(),
    )


@login_required
def recebimento(request):
    """Tela de Recebimento. Perfil 'cd' (sem acesso total): a entrada nasce
    pendente. Perfis com acesso total: a entrada já nasce aprovada, e a tela
    também mostra o estoque consolidado da loja."""
    perfil_cd = em_grupo(request.user, GRUPO_CD) and not tem_acesso_total(request.user)
    status_entrada = "aguardando_aprovacao" if perfil_cd else "aprovado"

    codigo_barras = (request.POST.get("codigo_barras") or request.GET.get("codigo_barras") or "").strip()
    produto = Produto.objects.filter(codigo_barras=codigo_barras).first() if codigo_barras else None
    form = None

    if request.method == "POST" and codigo_barras:
        form_cls = EntradaProdutoExistenteForm if produto else EntradaProdutoNovoForm
        form = form_cls(request.POST)

        if form.is_valid():
            with transaction.atomic():
                if not produto:
                    produto = Produto.objects.create(
                        codigo_barras=codigo_barras,
                        nome=form.cleaned_data["nome"].strip(),
                        marca=form.cleaned_data.get("marca", "").strip(),
                    )

                loja_destino = form.cleaned_data["loja_destino"]
                lote_txt = (form.cleaned_data.get("lote") or "").strip() or None
                validade = form.cleaned_data["validade"]
                quantidade = form.cleaned_data["quantidade"]

                lote = _obter_ou_criar_lote(
                    produto, lote_txt, validade, loja_destino, quantidade, status_entrada, request.user
                )

                Movimentacao.objects.create(
                    lote=lote, tipo="entrada", loja_origem=None, loja_destino=loja_destino,
                    quantidade=quantidade, data=timezone.now(),
                    responsavel=request.user.get_username(), responsavel_user=request.user,
                    observacoes=form.cleaned_data.get("observacoes", ""), status=status_entrada,
                )

            if status_entrada == "aprovado":
                messages.success(request, f"Entrada de {quantidade} un. de '{produto.nome}' registrada com sucesso!")
            else:
                messages.info(
                    request,
                    f"Entrada de {quantidade} un. de '{produto.nome}' registrada como PENDENTE — "
                    "aguardando aprovação da Compra e Venda ou do Coordenador.",
                )
            return redirect("estoque:recebimento")

    contexto = {
        "perfil_cd": perfil_cd,
        "codigo_barras": codigo_barras,
        "produto": produto,
        "form": form or (EntradaProdutoExistenteForm() if produto else (EntradaProdutoNovoForm() if codigo_barras else None)),
    }

    if perfil_cd:
        contexto["minhas_entradas"] = (
            Lote.objects.filter(criado_por=request.user.get_username())
            .exclude(status="aprovado")
            .select_related("produto", "loja_atual")
            .order_by("-criado_em")[:20]
        )
    else:
        contexto["estoque_consolidado"] = (
            Lote.objects.filter(status="aprovado", quantidade__gt=0)
            .select_related("produto", "loja_atual")
            .order_by("produto__nome", "validade")
        )

    return render(request, "estoque/recebimento.html", contexto)


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def pendencias(request):
    """Fila de aprovação — só compra_venda e admin. Aprovar libera a
    quantidade para o estoque real; rejeitar exige motivo e não soma nada."""
    if request.method == "POST":
        lote = get_object_or_404(Lote, pk=request.POST.get("lote_id"), status="aguardando_aprovacao")
        acao = request.POST.get("acao")

        if acao == "aprovar":
            with transaction.atomic():
                lote.status = "aprovado"
                lote.aprovado_por = request.user.get_username()
                lote.aprovado_em = timezone.now()
                lote.save(update_fields=["status", "aprovado_por", "aprovado_em"])
                Movimentacao.objects.filter(lote=lote, status="aguardando_aprovacao").update(status="aprovado")
            messages.success(request, f"Entrada de '{lote.produto.nome}' aprovada — já conta como estoque disponível.")

        elif acao == "rejeitar":
            motivo = (request.POST.get("motivo") or "").strip() or "Não informado"
            with transaction.atomic():
                lote.status = "rejeitado"
                lote.aprovado_por = request.user.get_username()
                lote.aprovado_em = timezone.now()
                lote.motivo_rejeicao = motivo
                lote.save(update_fields=["status", "aprovado_por", "aprovado_em", "motivo_rejeicao"])
                Movimentacao.objects.filter(lote=lote, status="aguardando_aprovacao").update(
                    status="rejeitado", motivo_rejeicao=motivo
                )
            messages.warning(request, f"Entrada de '{lote.produto.nome}' rejeitada.")

        return redirect("estoque:pendencias")

    pendentes = (
        Lote.objects.filter(status="aguardando_aprovacao")
        .select_related("produto", "loja_atual")
        .order_by("criado_em")
    )
    return render(request, "estoque/pendencias.html", {"pendentes": pendentes})


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def remanejamento(request):
    """Remanejamento entre lojas — só compra_venda e admin EXECUTAM (não
    apenas solicitam), conforme o briefing. Mesmo número de lote e validade são
    preservados no destino para manter a rastreabilidade."""
    lojas = Loja.objects.all().order_by("nome")

    loja_origem_id = request.POST.get("loja_origem") or request.GET.get("loja_origem")
    loja_origem = Loja.objects.filter(pk=loja_origem_id).first() if loja_origem_id else None

    lotes_origem = (
        Lote.objects.filter(loja_atual=loja_origem, status="aprovado", quantidade__gt=0)
        .select_related("produto")
        .order_by("validade")
        if loja_origem else Lote.objects.none()
    )

    lote_id = request.POST.get("lote_id") or request.GET.get("lote_id")
    lote_selecionado = (
        Lote.objects.filter(pk=lote_id, status="aprovado", quantidade__gt=0).select_related("produto", "loja_atual").first()
        if lote_id else None
    )

    form = None
    if lote_selecionado:
        form = RemanejamentoForm(request.POST if request.method == "POST" else None)

        if request.method == "POST" and form.is_valid():
            quantidade = form.cleaned_data["quantidade"]
            loja_destino = form.cleaned_data["loja_destino"]

            if quantidade > lote_selecionado.quantidade:
                form.add_error("quantidade", "Quantidade maior que o saldo disponível no lote de origem.")
            elif loja_destino.pk == lote_selecionado.loja_atual_id:
                form.add_error("loja_destino", "A loja de destino precisa ser diferente da loja de origem.")
            else:
                with transaction.atomic():
                    lote_selecionado.quantidade -= quantidade
                    lote_selecionado.save(update_fields=["quantidade"])

                    _obter_ou_criar_lote(
                        lote_selecionado.produto, lote_selecionado.lote, lote_selecionado.validade,
                        loja_destino, quantidade, "aprovado", request.user,
                    )

                    Movimentacao.objects.create(
                        lote=lote_selecionado, tipo="remanejamento",
                        loja_origem=lote_selecionado.loja_atual, loja_destino=loja_destino,
                        quantidade=quantidade, data=timezone.now(),
                        responsavel=request.user.get_username(), responsavel_user=request.user,
                        observacoes=form.cleaned_data.get("observacoes", ""), status="aprovado",
                    )

                messages.success(request, f"Remanejamento de {quantidade} un. registrado com sucesso!")
                return redirect(f"/remanejamento/?loja_origem={loja_origem.id}")

    return render(request, "estoque/remanejamento.html", {
        "lojas": lojas,
        "loja_origem": loja_origem,
        "lotes_origem": lotes_origem,
        "lote_selecionado": lote_selecionado,
        "form": form,
    })


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def planilha(request):
    """Planilha Compartilhada: só estoque já aprovado, ordenado por validade
    (FEFO), com filtro por loja e por nome do produto."""
    lojas = Loja.objects.all().order_by("nome")
    qs = Lote.objects.filter(status="aprovado", quantidade__gt=0).select_related("produto", "loja_atual")

    loja_filtro = request.GET.get("loja", "")
    if loja_filtro:
        qs = qs.filter(loja_atual_id=loja_filtro)

    nome_filtro = request.GET.get("produto", "").strip()
    if nome_filtro:
        qs = qs.filter(produto__nome__icontains=nome_filtro)

    return render(request, "estoque/planilha.html", {
        "lotes": qs.order_by("validade"),
        "lojas": lojas,
        "loja_filtro": loja_filtro,
        "nome_filtro": nome_filtro,
    })


@perfil_required(GRUPO_ADMIN)
def usuarios(request):
    """Gestão de Usuários — só admin. Define perfil (grupo) e libera/bloqueia
    o acesso; a conta de login em si é criada via Django admin/createsuperuser."""
    if request.method == "POST":
        usuario_alvo = get_object_or_404(User, pk=request.POST.get("user_id"))
        form = UsuarioPerfilForm(request.POST)
        if form.is_valid():
            grupo = form.cleaned_data["grupo"]
            usuario_alvo.groups.set([grupo])
            usuario_alvo.is_active = form.cleaned_data["ativo"]
            # Mantém is_superuser/is_staff sincronizados com o perfil escolhido.
            # Sem isso, alguém que já foi superusuário (ex: bootstrap do primeiro
            # cadastro do sistema, ou criado via createsuperuser) continuava com
            # acesso total mesmo depois de reatribuído a outro perfil aqui — o
            # em_grupo() sempre libera quem tem is_superuser=True, então essa
            # flag precisa acompanhar o grupo, não só ficar registrada uma vez.
            eh_admin_agora = grupo.name == GRUPO_ADMIN
            usuario_alvo.is_superuser = eh_admin_agora
            usuario_alvo.is_staff = eh_admin_agora
            usuario_alvo.save(update_fields=["is_active", "is_superuser", "is_staff"])
            messages.success(request, f"Perfil de '{usuario_alvo.username}' atualizado!")
        return redirect("estoque:usuarios")

    return render(request, "estoque/usuarios.html", {
        "usuarios_lista": User.objects.all().order_by("date_joined").prefetch_related("groups"),
        "todos_grupos": Group.objects.all(),
    })
