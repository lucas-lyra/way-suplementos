from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    CabecalhoNFForm,
    CadastroForm,
    ItemNFForm,
    NovoProdutoItemForm,
    RemanejamentoForm,
    UsuarioPerfilForm,
)
from .models import ItemNotaFiscal, Lote, Loja, Movimentacao, NotaFiscal, Produto
from .permissions import GRUPO_ADMIN, GRUPO_CD, GRUPOS_ACESSO_TOTAL, em_grupo, perfil_required, tem_acesso_total

User = get_user_model()

CHAVE_SESSAO_NF = "nf_rascunho"


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


def _rascunho_nf_vazio(loja_padrao_id=None):
    return {
        "numero": "",
        "fornecedor": "",
        "loja_destino_id": loja_padrao_id,
        "itens": [],   # cada item: produto_id, produto_nome, marca, codigo_barras, novo_cadastro, validade (iso), quantidade, observacao
        "aberto_idx": None,
    }


def _obter_rascunho_nf(request, loja_padrao_id=None):
    rascunho = request.session.get(CHAVE_SESSAO_NF)
    if rascunho is None:
        rascunho = _rascunho_nf_vazio(loja_padrao_id)
        request.session[CHAVE_SESSAO_NF] = rascunho
    return rascunho


def _salvar_rascunho_nf(request, rascunho):
    request.session[CHAVE_SESSAO_NF] = rascunho
    request.session.modified = True


def _aprovar_item_nf(item, usuario):
    """Aprova um item de nota fiscal: cria/mescla o Lote correspondente (sem
    número de lote — agrupado só por produto+validade+loja, mesmo mecanismo já
    usado quando o campo de lote fica em branco) e registra a Movimentacao.
    Marca o item como aprovado e recalcula o status da NF inteira."""
    nf = item.nota_fiscal
    lote = _obter_ou_criar_lote(
        item.produto, None, item.validade, nf.loja_destino, item.quantidade,
        status="aprovado", usuario=nf.responsavel,
    )
    obs = f"NF {nf.numero}" + (f" — {item.observacao}" if item.observacao else "")
    Movimentacao.objects.create(
        lote=lote, tipo="entrada", loja_origem=None, loja_destino=nf.loja_destino,
        quantidade=item.quantidade, data=timezone.now(),
        responsavel=nf.responsavel.get_username(), responsavel_user=nf.responsavel,
        observacoes=obs, status="aprovado",
    )
    item.status = "aprovado"
    item.lote_gerado = lote
    item.save(update_fields=["status", "lote_gerado"])
    nf.recalcular_status()


def _rejeitar_item_nf(item, motivo):
    item.status = "rejeitado"
    item.motivo_rejeicao = motivo or "Não informado"
    item.save(update_fields=["status", "motivo_rejeicao"])
    item.nota_fiscal.recalcular_status()


@login_required
def recebimento(request):
    """Tela de Recebimento por Nota Fiscal (checklist de itens bipados, sem
    campo de lote). Perfil 'cd' (sem acesso total): a NF nasce pendente, item
    por item. Perfis com acesso total: a NF já nasce aprovada (cada item vira
    estoque disponível na hora), e a tela também mostra o estoque consolidado."""
    perfil_cd = em_grupo(request.user, GRUPO_CD) and not tem_acesso_total(request.user)
    status_entrada = "pendente" if perfil_cd else "aprovado"

    loja_padrao = Loja.objects.order_by("nome").first()
    rascunho = _obter_rascunho_nf(request, loja_padrao_id=loja_padrao.id if loja_padrao else None)

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "bipar":
            rascunho["numero"] = request.POST.get("numero", "").strip()
            rascunho["fornecedor"] = request.POST.get("fornecedor", "").strip()
            loja_id = request.POST.get("loja_destino") or None
            rascunho["loja_destino_id"] = int(loja_id) if loja_id else None
            codigo = request.POST.get("codigo_barras", "").strip()

            if not rascunho["numero"]:
                messages.error(request, "Preencha o Número da NF antes de bipar.")
            elif not rascunho["loja_destino_id"]:
                messages.error(request, "Selecione a Loja/CD de Destino antes de bipar.")
            elif codigo:
                produto = Produto.objects.filter(codigo_barras=codigo).first()
                rascunho["itens"].append({
                    "produto_id": produto.id if produto else None,
                    "produto_nome": produto.nome if produto else None,
                    "marca": (produto.marca if produto else "") or "",
                    "codigo_barras": codigo,
                    "novo_cadastro": produto is None,
                    "validade": date.today().isoformat(),
                    "quantidade": 1,
                    "observacao": "",
                })
                rascunho["aberto_idx"] = len(rascunho["itens"]) - 1
            _salvar_rascunho_nf(request, rascunho)
            return redirect("estoque:recebimento")

        if acao == "expandir":
            idx = int(request.POST.get("idx", -1))
            rascunho["aberto_idx"] = None if rascunho.get("aberto_idx") == idx else idx
            _salvar_rascunho_nf(request, rascunho)
            return redirect("estoque:recebimento")

        if acao == "atualizar_item":
            idx = int(request.POST.get("idx", -1))
            if 0 <= idx < len(rascunho["itens"]):
                item = rascunho["itens"][idx]
                if item["produto_id"] is None:
                    nome = request.POST.get("nome", "").strip()
                    if not nome:
                        messages.error(request, "Preencha o nome do produto para poder salvar o item.")
                        _salvar_rascunho_nf(request, rascunho)
                        return redirect("estoque:recebimento")
                    marca = request.POST.get("marca", "").strip()
                    novo_produto = Produto.objects.create(codigo_barras=item["codigo_barras"], nome=nome, marca=marca)
                    item["produto_id"] = novo_produto.id
                    item["produto_nome"] = novo_produto.nome
                    item["marca"] = marca

                validade_str = request.POST.get("validade", "").strip()
                if validade_str:
                    try:
                        item["validade"] = date.fromisoformat(validade_str).isoformat()
                    except ValueError:
                        messages.error(request, "Data de validade inválida.")
                try:
                    item["quantidade"] = max(1, int(request.POST.get("quantidade", item["quantidade"])))
                except (TypeError, ValueError):
                    pass
                item["observacao"] = request.POST.get("observacao", "").strip()
            _salvar_rascunho_nf(request, rascunho)
            return redirect("estoque:recebimento")

        if acao == "remover_item":
            idx = int(request.POST.get("idx", -1))
            if 0 <= idx < len(rascunho["itens"]):
                rascunho["itens"].pop(idx)
                rascunho["aberto_idx"] = None
            _salvar_rascunho_nf(request, rascunho)
            return redirect("estoque:recebimento")

        if acao == "cancelar":
            request.session.pop(CHAVE_SESSAO_NF, None)
            messages.info(request, "Recebimento cancelado — nenhum item foi salvo.")
            return redirect("estoque:recebimento")

        if acao == "enviar":
            erros = []
            if not rascunho["numero"]:
                erros.append("Preencha o Número da NF.")
            if not rascunho["loja_destino_id"]:
                erros.append("Selecione a Loja/CD de Destino.")
            if not rascunho["itens"]:
                erros.append("Bipe pelo menos um item antes de enviar.")
            for i, item in enumerate(rascunho["itens"], start=1):
                if item["produto_id"] is None:
                    erros.append(f"Finalize o cadastro do produto do item {i} (código {item['codigo_barras']}) antes de enviar.")
                if not item.get("validade"):
                    erros.append(f"Preencha a validade do item {i}.")
                if not item.get("quantidade") or item["quantidade"] < 1:
                    erros.append(f"Preencha a quantidade do item {i}.")

            if erros:
                for erro in erros:
                    messages.error(request, erro)
                return redirect("estoque:recebimento")

            with transaction.atomic():
                nf = NotaFiscal.objects.create(
                    numero=rascunho["numero"], fornecedor=rascunho["fornecedor"] or None,
                    loja_destino_id=rascunho["loja_destino_id"], responsavel=request.user,
                    status=status_entrada,
                )
                for item in rascunho["itens"]:
                    item_obj = ItemNotaFiscal.objects.create(
                        nota_fiscal=nf, produto_id=item["produto_id"],
                        validade=date.fromisoformat(item["validade"]), quantidade=item["quantidade"],
                        observacao=item.get("observacao") or None, status=status_entrada,
                    )
                    if status_entrada == "aprovado":
                        _aprovar_item_nf(item_obj, request.user)

                if status_entrada == "aprovado":
                    nf.aprovado_por = request.user
                    nf.aprovado_em = timezone.now()
                    nf.save(update_fields=["aprovado_por", "aprovado_em"])

            total_itens = len(rascunho["itens"])
            request.session.pop(CHAVE_SESSAO_NF, None)
            if status_entrada == "aprovado":
                messages.success(request, f"NF {nf.numero} confirmada — {total_itens} item(ns) já disponíveis no estoque!")
            else:
                messages.info(request, f"NF {nf.numero} enviada para aprovação com {total_itens} item(ns).")
            return redirect("estoque:recebimento")

    cabecalho_form = CabecalhoNFForm(initial={
        "numero": rascunho["numero"], "fornecedor": rascunho["fornecedor"],
        "loja_destino": rascunho["loja_destino_id"],
    })

    itens_exibicao = []
    for idx, item in enumerate(rascunho["itens"]):
        itens_exibicao.append({**item, "idx": idx, "aberto": rascunho.get("aberto_idx") == idx})

    contexto = {
        "perfil_cd": perfil_cd,
        "cabecalho_form": cabecalho_form,
        "itens": itens_exibicao,
        "total_itens": len(itens_exibicao),
    }

    if perfil_cd:
        contexto["minhas_notas"] = (
            NotaFiscal.objects.filter(responsavel=request.user)
            .select_related("loja_destino")
            .prefetch_related("itens__produto")
            .order_by("-data_recebimento")[:10]
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
    """Fila de aprovação de notas fiscais — só compra_venda e admin. Aprovar
    (por item ou a NF inteira) libera a quantidade para o estoque real;
    rejeitar exige motivo e não soma nada. Ver views.py:_aprovar_item_nf /
    _rejeitar_item_nf para a lógica compartilhada com o envio direto."""
    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "aprovar_item":
            item = get_object_or_404(ItemNotaFiscal, pk=request.POST.get("item_id"), status="pendente")
            _aprovar_item_nf(item, request.user)
            messages.success(request, f"Item '{item.produto.nome}' aprovado — já conta como estoque disponível.")

        elif acao == "rejeitar_item":
            item = get_object_or_404(ItemNotaFiscal, pk=request.POST.get("item_id"), status="pendente")
            motivo = (request.POST.get("motivo") or "").strip()
            _rejeitar_item_nf(item, motivo)
            messages.warning(request, f"Item '{item.produto.nome}' rejeitado.")

        elif acao == "aprovar_nf":
            nf = get_object_or_404(NotaFiscal, pk=request.POST.get("nf_id"))
            with transaction.atomic():
                for item in nf.itens.filter(status="pendente"):
                    _aprovar_item_nf(item, request.user)
                nf.aprovado_por = request.user
                nf.aprovado_em = timezone.now()
                nf.save(update_fields=["aprovado_por", "aprovado_em"])
            messages.success(request, f"NF {nf.numero} aprovada por inteiro — todos os itens já contam como estoque.")

        elif acao == "rejeitar_nf":
            nf = get_object_or_404(NotaFiscal, pk=request.POST.get("nf_id"))
            motivo = (request.POST.get("motivo") or "").strip()
            with transaction.atomic():
                for item in nf.itens.filter(status="pendente"):
                    _rejeitar_item_nf(item, motivo)
                nf.aprovado_por = request.user
                nf.aprovado_em = timezone.now()
                nf.save(update_fields=["aprovado_por", "aprovado_em"])
            messages.warning(request, f"NF {nf.numero} rejeitada por inteiro.")

        return redirect("estoque:pendencias")

    notas_pendentes = (
        NotaFiscal.objects.filter(status="pendente")
        .select_related("loja_destino", "responsavel")
        .prefetch_related("itens__produto")
        .order_by("data_recebimento")
    )
    return render(request, "estoque/pendencias.html", {"notas_pendentes": notas_pendentes})


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
