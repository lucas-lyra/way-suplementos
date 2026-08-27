from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    CabecalhoMovimentacaoForm,
    CabecalhoNFForm,
    CadastroForm,
    LojaForm,
    ParadaForm,
    RotaCabecalhoForm,
    UsuarioPerfilForm,
)
from .models import (
    ItemNotaFiscal,
    ItemParada,
    Lote,
    Loja,
    Movimentacao,
    NotaFiscal,
    NotaFiscalSaida,
    ParadaRota,
    Produto,
)
from .permissions import (
    GRUPO_ADMIN,
    GRUPO_CD,
    GRUPO_MOTORISTA,
    GRUPOS_ACESSO_TOTAL,
    em_grupo,
    eh_motorista,
    perfil_required,
    tem_acesso_total,
)

User = get_user_model()

CHAVE_SESSAO_NF = "nf_rascunho"
CHAVE_SESSAO_REMANEJO = "remanejo_rascunho"
CHAVE_SESSAO_ROTA = "rota_rascunho"


def _obter_sessao(request, chave, valor_padrao_factory):
    valor = request.session.get(chave)
    if valor is None:
        valor = valor_padrao_factory()
        request.session[chave] = valor
    return valor


def _salvar_sessao(request, chave, valor):
    request.session[chave] = valor
    request.session.modified = True


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


def _rascunho_remanejo_vazio():
    return {
        "loja_origem_id": None, "loja_destino_id": None,
        "itens": [],  # produto_id, produto_nome, lote_id, validade(iso), quantidade_disponivel, quantidade
        "aberto_idx": None,
        "escolha_pendente": None,  # {produto_id, produto_nome, opcoes: [{lote_id, validade, quantidade_disponivel}]}
    }


def _buscar_opcoes_validade(produto, loja_id):
    """Lotes aprovados com saldo desse produto numa loja — usado tanto pelo
    Remanejamento quanto pela bipagem dentro de uma Parada de Rota, já que os
    dois movimentam estoque já existente (ao contrário do Recebimento, que
    cria estoque novo)."""
    lotes = Lote.objects.filter(
        produto=produto, loja_atual_id=loja_id, status="aprovado", quantidade__gt=0
    ).order_by("validade")
    return [
        {"lote_id": l.id, "validade": l.validade.isoformat(), "quantidade_disponivel": l.quantidade}
        for l in lotes
    ]


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def remanejamento(request):
    """Remanejamento entre lojas — checklist de itens bipados, sem campo de
    lote (mesmo padrão visual do Recebimento). Ao bipar, mostra as validades
    disponíveis daquele produto na loja de origem para escolher qual mover."""
    rascunho = _obter_sessao(request, CHAVE_SESSAO_REMANEJO, _rascunho_remanejo_vazio)

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "bipar":
            origem_id = request.POST.get("loja_origem") or None
            destino_id = request.POST.get("loja_destino") or None
            rascunho["loja_origem_id"] = int(origem_id) if origem_id else None
            rascunho["loja_destino_id"] = int(destino_id) if destino_id else None
            codigo = request.POST.get("codigo_barras", "").strip()

            if not rascunho["loja_origem_id"] or not rascunho["loja_destino_id"]:
                messages.error(request, "Selecione a loja de origem e a de destino antes de bipar.")
            elif rascunho["loja_origem_id"] == rascunho["loja_destino_id"]:
                messages.error(request, "A loja de destino precisa ser diferente da loja de origem.")
            elif codigo:
                produto = Produto.objects.filter(codigo_barras=codigo).first()
                if not produto:
                    messages.error(request, f"Código '{codigo}' não encontrado no catálogo.")
                else:
                    opcoes = _buscar_opcoes_validade(produto, rascunho["loja_origem_id"])
                    if not opcoes:
                        messages.error(request, f"'{produto.nome}' sem estoque disponível na loja de origem.")
                    else:
                        rascunho["escolha_pendente"] = {
                            "produto_id": produto.id, "produto_nome": produto.nome, "opcoes": opcoes,
                        }
            _salvar_sessao(request, CHAVE_SESSAO_REMANEJO, rascunho)
            return redirect("estoque:remanejamento")

        if acao == "escolher_validade":
            escolha = rascunho.get("escolha_pendente")
            try:
                lote_id = int(request.POST.get("lote_id"))
                quantidade = int(request.POST.get("quantidade", 0))
            except (TypeError, ValueError):
                lote_id, quantidade = None, 0
            opcao = next((o for o in (escolha or {}).get("opcoes", []) if o["lote_id"] == lote_id), None)

            if not opcao:
                messages.error(request, "Selecione uma validade válida.")
            elif quantidade < 1 or quantidade > opcao["quantidade_disponivel"]:
                messages.error(request, "Quantidade inválida.")
            else:
                rascunho["itens"].append({
                    "produto_id": escolha["produto_id"], "produto_nome": escolha["produto_nome"],
                    "lote_id": lote_id, "validade": opcao["validade"],
                    "quantidade_disponivel": opcao["quantidade_disponivel"], "quantidade": quantidade,
                })
                rascunho["aberto_idx"] = len(rascunho["itens"]) - 1
                rascunho["escolha_pendente"] = None
            _salvar_sessao(request, CHAVE_SESSAO_REMANEJO, rascunho)
            return redirect("estoque:remanejamento")

        if acao == "cancelar_escolha":
            rascunho["escolha_pendente"] = None
            _salvar_sessao(request, CHAVE_SESSAO_REMANEJO, rascunho)
            return redirect("estoque:remanejamento")

        if acao == "expandir":
            idx = int(request.POST.get("idx", -1))
            rascunho["aberto_idx"] = None if rascunho.get("aberto_idx") == idx else idx
            _salvar_sessao(request, CHAVE_SESSAO_REMANEJO, rascunho)
            return redirect("estoque:remanejamento")

        if acao == "atualizar_item":
            idx = int(request.POST.get("idx", -1))
            if 0 <= idx < len(rascunho["itens"]):
                item = rascunho["itens"][idx]
                try:
                    nova_qtd = int(request.POST.get("quantidade", item["quantidade"]))
                    if 1 <= nova_qtd <= item["quantidade_disponivel"]:
                        item["quantidade"] = nova_qtd
                    else:
                        messages.error(request, "Quantidade precisa ser entre 1 e o saldo disponível do lote.")
                except (TypeError, ValueError):
                    pass
            _salvar_sessao(request, CHAVE_SESSAO_REMANEJO, rascunho)
            return redirect("estoque:remanejamento")

        if acao == "remover_item":
            idx = int(request.POST.get("idx", -1))
            if 0 <= idx < len(rascunho["itens"]):
                rascunho["itens"].pop(idx)
                rascunho["aberto_idx"] = None
            _salvar_sessao(request, CHAVE_SESSAO_REMANEJO, rascunho)
            return redirect("estoque:remanejamento")

        if acao == "cancelar":
            request.session.pop(CHAVE_SESSAO_REMANEJO, None)
            messages.info(request, "Remanejamento cancelado — nenhum item foi movido.")
            return redirect("estoque:remanejamento")

        if acao == "enviar":
            if not rascunho["itens"]:
                messages.error(request, "Adicione pelo menos um item antes de confirmar.")
                return redirect("estoque:remanejamento")

            loja_origem = get_object_or_404(Loja, pk=rascunho["loja_origem_id"])
            loja_destino = get_object_or_404(Loja, pk=rascunho["loja_destino_id"])
            total_movido = 0

            with transaction.atomic():
                for item in rascunho["itens"]:
                    lote_origem = Lote.objects.select_for_update().filter(pk=item["lote_id"]).first()
                    if not lote_origem or item["quantidade"] > lote_origem.quantidade:
                        messages.error(request, f"'{item['produto_nome']}': saldo insuficiente no momento da confirmação — item ignorado.")
                        continue

                    lote_origem.quantidade -= item["quantidade"]
                    lote_origem.save(update_fields=["quantidade"])

                    _obter_ou_criar_lote(
                        lote_origem.produto, None, lote_origem.validade, loja_destino,
                        item["quantidade"], "aprovado", request.user,
                    )
                    Movimentacao.objects.create(
                        lote=lote_origem, tipo="remanejamento", loja_origem=loja_origem, loja_destino=loja_destino,
                        quantidade=item["quantidade"], data=timezone.now(),
                        responsavel=request.user.get_username(), responsavel_user=request.user,
                        observacoes="", status="aprovado",
                    )
                    total_movido += 1

            request.session.pop(CHAVE_SESSAO_REMANEJO, None)
            if total_movido:
                messages.success(request, f"Remanejamento de {total_movido} item(ns) de {loja_origem.nome} para {loja_destino.nome} confirmado!")
            return redirect("estoque:remanejamento")

    cabecalho_form = CabecalhoMovimentacaoForm(initial={
        "loja_origem": rascunho.get("loja_origem_id"), "loja_destino": rascunho.get("loja_destino_id"),
    })
    itens_exibicao = [{**item, "idx": idx, "aberto": rascunho.get("aberto_idx") == idx} for idx, item in enumerate(rascunho["itens"])]

    return render(request, "estoque/remanejamento.html", {
        "cabecalho_form": cabecalho_form,
        "itens": itens_exibicao,
        "total_itens": len(itens_exibicao),
        "escolha_pendente": rascunho.get("escolha_pendente"),
    })


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def planilha(request):
    """Planilha Compartilhada: só estoque já aprovado, ordenado por validade
    (FEFO), com filtro por loja e por nome do produto."""
    lojas = Loja.objects.all().order_by("nome")
    qs = (
        Lote.objects.filter(status="aprovado", quantidade__gt=0)
        .select_related("produto", "loja_atual")
        .prefetch_related("itens_nota_fiscal_origem__nota_fiscal__responsavel")
    )

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


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def usuarios(request):
    """Gestão de Usuários — compra_venda e admin (mesmo nível de acesso hoje,
    ver permissions.py). Define perfil (grupo) e libera/bloqueia o acesso; a
    conta de login em si é criada via Django admin/createsuperuser."""
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


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def lojas(request):
    """Gestão de Lojas — criar/editar/excluir, restrito a compra_venda/admin."""
    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "criar":
            form = LojaForm(request.POST)
            if form.is_valid():
                Loja.objects.create(nome=form.cleaned_data["nome"], endereco=form.cleaned_data.get("endereco") or None)
                messages.success(request, "Loja criada!")
            else:
                for erros_campo in form.errors.values():
                    for erro in erros_campo:
                        messages.error(request, erro)

        elif acao == "editar":
            loja_obj = get_object_or_404(Loja, pk=request.POST.get("loja_id"))
            form = LojaForm(request.POST)
            if form.is_valid():
                loja_obj.nome = form.cleaned_data["nome"]
                loja_obj.endereco = form.cleaned_data.get("endereco") or None
                loja_obj.save(update_fields=["nome", "endereco"])
                messages.success(request, "Loja atualizada!")
            else:
                for erros_campo in form.errors.values():
                    for erro in erros_campo:
                        messages.error(request, erro)

        elif acao == "excluir":
            loja_obj = get_object_or_404(Loja, pk=request.POST.get("loja_id"))
            nome = loja_obj.nome
            try:
                with transaction.atomic():
                    loja_obj.delete()
                messages.success(request, f"Loja '{nome}' excluída.")
            except (ProtectedError, IntegrityError):
                messages.error(request, f"Não foi possível excluir '{nome}' — ela tem lotes, movimentações, notas fiscais ou rotas associadas.")

        return redirect("estoque:lojas")

    return render(request, "estoque/lojas.html", {
        "lojas_lista": Loja.objects.all().order_by("nome"),
        "form_novo": LojaForm(),
    })


def _rascunho_rota_vazio():
    return {
        "numero": "", "loja_origem_id": None, "motorista_id": None,
        "paradas": [],  # {loja_destino_id, prazo(iso ou None), itens: [...]}
        "parada_aberta_idx": None,
        "escolha_pendente": None,  # {parada_idx, produto_id, produto_nome, opcoes}
    }


@perfil_required(*GRUPOS_ACESSO_TOTAL)
def rotas_criar(request):
    """Monta uma rota de entrega com múltiplas paradas (lojas de destino),
    cada uma com seus itens bipados — a validade é escolhida entre as
    disponíveis na loja de origem, mesmo mecanismo do Remanejamento. O
    reordenar das paradas é por botões subir/descer (não drag-and-drop — ver
    nota no resumo da conversa). O estoque sai da origem assim que a rota é
    confirmada; cada parada só soma estoque no destino quando marcada como
    entregue (ver rotas_painel)."""
    rascunho = _obter_sessao(request, CHAVE_SESSAO_ROTA, _rascunho_rota_vazio)

    if request.method == "POST":
        acao = request.POST.get("acao")

        if acao == "salvar_cabecalho":
            rascunho["numero"] = request.POST.get("numero", "").strip()
            loja_origem_id = request.POST.get("loja_origem") or None
            rascunho["loja_origem_id"] = int(loja_origem_id) if loja_origem_id else None
            motorista_id = request.POST.get("motorista") or None
            rascunho["motorista_id"] = int(motorista_id) if motorista_id else None
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "adicionar_parada":
            if not rascunho["loja_origem_id"]:
                messages.error(request, "Preencha os dados da rota (número/loja de origem) antes de adicionar paradas.")
            else:
                loja_destino_id = request.POST.get("loja_destino") or None
                if not loja_destino_id:
                    messages.error(request, "Selecione a loja de destino da parada.")
                elif int(loja_destino_id) == rascunho["loja_origem_id"]:
                    messages.error(request, "A parada não pode ser na mesma loja de origem.")
                else:
                    prazo_str = request.POST.get("prazo", "").strip()
                    rascunho["paradas"].append({
                        "loja_destino_id": int(loja_destino_id), "prazo": prazo_str or None, "itens": [],
                    })
                    rascunho["parada_aberta_idx"] = len(rascunho["paradas"]) - 1
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "expandir_parada":
            idx = int(request.POST.get("idx", -1))
            rascunho["parada_aberta_idx"] = None if rascunho.get("parada_aberta_idx") == idx else idx
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "remover_parada":
            idx = int(request.POST.get("idx", -1))
            if 0 <= idx < len(rascunho["paradas"]):
                rascunho["paradas"].pop(idx)
                rascunho["parada_aberta_idx"] = None
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "mover_parada":
            idx = int(request.POST.get("idx", -1))
            direcao = request.POST.get("direcao")
            paradas = rascunho["paradas"]
            destino_idx = idx - 1 if direcao == "cima" else idx + 1
            if 0 <= idx < len(paradas) and 0 <= destino_idx < len(paradas):
                paradas[idx], paradas[destino_idx] = paradas[destino_idx], paradas[idx]
                rascunho["parada_aberta_idx"] = None
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "bipar_item":
            parada_idx = int(request.POST.get("parada_idx", -1))
            codigo = request.POST.get("codigo_barras", "").strip()
            if not (0 <= parada_idx < len(rascunho["paradas"])):
                messages.error(request, "Parada inválida.")
            elif codigo:
                produto = Produto.objects.filter(codigo_barras=codigo).first()
                if not produto:
                    messages.error(request, f"Código '{codigo}' não encontrado no catálogo.")
                else:
                    opcoes = _buscar_opcoes_validade(produto, rascunho["loja_origem_id"])
                    if not opcoes:
                        messages.error(request, f"'{produto.nome}' sem estoque disponível na loja de origem.")
                    else:
                        rascunho["escolha_pendente"] = {
                            "parada_idx": parada_idx, "produto_id": produto.id,
                            "produto_nome": produto.nome, "opcoes": opcoes,
                        }
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "escolher_validade_item":
            escolha = rascunho.get("escolha_pendente")
            try:
                lote_id = int(request.POST.get("lote_id"))
                quantidade = int(request.POST.get("quantidade", 0))
            except (TypeError, ValueError):
                lote_id, quantidade = None, 0
            opcao = next((o for o in (escolha or {}).get("opcoes", []) if o["lote_id"] == lote_id), None) if escolha else None

            if not opcao:
                messages.error(request, "Selecione uma validade válida.")
            elif quantidade < 1 or quantidade > opcao["quantidade_disponivel"]:
                messages.error(request, "Quantidade inválida.")
            else:
                parada_idx = escolha["parada_idx"]
                rascunho["paradas"][parada_idx]["itens"].append({
                    "produto_id": escolha["produto_id"], "produto_nome": escolha["produto_nome"],
                    "lote_id": lote_id, "validade": opcao["validade"],
                    "quantidade_disponivel": opcao["quantidade_disponivel"], "quantidade": quantidade,
                })
                rascunho["escolha_pendente"] = None
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "cancelar_escolha":
            rascunho["escolha_pendente"] = None
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "remover_item":
            parada_idx = int(request.POST.get("parada_idx", -1))
            item_idx = int(request.POST.get("item_idx", -1))
            if 0 <= parada_idx < len(rascunho["paradas"]):
                itens = rascunho["paradas"][parada_idx]["itens"]
                if 0 <= item_idx < len(itens):
                    itens.pop(item_idx)
            _salvar_sessao(request, CHAVE_SESSAO_ROTA, rascunho)
            return redirect("estoque:rotas_criar")

        if acao == "cancelar":
            request.session.pop(CHAVE_SESSAO_ROTA, None)
            messages.info(request, "Rota cancelada — nenhum item foi movido.")
            return redirect("estoque:rotas_criar")

        if acao == "enviar":
            erros = []
            if not rascunho["numero"]:
                erros.append("Preencha o número da NF de saída.")
            if not rascunho["loja_origem_id"]:
                erros.append("Selecione a loja de origem.")
            if not rascunho["paradas"]:
                erros.append("Adicione pelo menos uma parada.")
            for i, parada in enumerate(rascunho["paradas"], start=1):
                if not parada["itens"]:
                    erros.append(f"A parada {i} não tem nenhum item — adicione ao menos um.")

            if erros:
                for erro in erros:
                    messages.error(request, erro)
                return redirect("estoque:rotas_criar")

            loja_origem = get_object_or_404(Loja, pk=rascunho["loja_origem_id"])

            with transaction.atomic():
                rota = NotaFiscalSaida.objects.create(
                    numero=rascunho["numero"], loja_origem=loja_origem,
                    responsavel_envio=request.user,
                    motorista_id=rascunho["motorista_id"], status="em_rota",
                )
                for ordem, parada in enumerate(rascunho["paradas"], start=1):
                    prazo_dt = None
                    if parada.get("prazo"):
                        prazo_dt = datetime.fromisoformat(parada["prazo"])
                        if timezone.is_naive(prazo_dt):
                            prazo_dt = timezone.make_aware(prazo_dt)
                    parada_obj = ParadaRota.objects.create(
                        nota_fiscal_saida=rota, loja_destino_id=parada["loja_destino_id"],
                        ordem=ordem, status="aguardando", prazo=prazo_dt,
                    )
                    for item in parada["itens"]:
                        lote_origem = Lote.objects.select_for_update().filter(pk=item["lote_id"]).first()
                        if not lote_origem or item["quantidade"] > lote_origem.quantidade:
                            messages.error(request, f"'{item['produto_nome']}': saldo insuficiente — item ignorado na parada {ordem}.")
                            continue
                        lote_origem.quantidade -= item["quantidade"]
                        lote_origem.save(update_fields=["quantidade"])

                        ItemParada.objects.create(
                            parada=parada_obj, produto_id=item["produto_id"],
                            validade=date.fromisoformat(item["validade"]), quantidade=item["quantidade"],
                        )
                        Movimentacao.objects.create(
                            lote=lote_origem, tipo="remanejamento", loja_origem=loja_origem,
                            loja_destino_id=parada["loja_destino_id"], quantidade=item["quantidade"],
                            data=timezone.now(), responsavel=request.user.get_username(),
                            responsavel_user=request.user,
                            observacoes=f"Rota {rota.numero} — parada {ordem}", status="aprovado",
                        )

            request.session.pop(CHAVE_SESSAO_ROTA, None)
            messages.success(request, f"Rota {rota.numero} criada com {len(rascunho['paradas'])} parada(s)!")
            return redirect("estoque:rotas_painel")

    lojas_qs = Loja.objects.all().order_by("nome")
    cabecalho_form = RotaCabecalhoForm(initial={
        "numero": rascunho["numero"], "loja_origem": rascunho.get("loja_origem_id"),
        "motorista": rascunho.get("motorista_id"),
    })
    parada_form = ParadaForm()

    paradas_exibicao = []
    for idx, parada in enumerate(rascunho["paradas"]):
        loja_destino = Loja.objects.filter(pk=parada["loja_destino_id"]).first()
        paradas_exibicao.append({
            **parada, "idx": idx, "aberto": rascunho.get("parada_aberta_idx") == idx,
            "loja_destino_nome": loja_destino.nome if loja_destino else "?",
            "total_itens": len(parada["itens"]),
        })

    return render(request, "estoque/rotas_criar.html", {
        "lojas": lojas_qs,
        "cabecalho_form": cabecalho_form,
        "parada_form": parada_form,
        "paradas": paradas_exibicao,
        "escolha_pendente": rascunho.get("escolha_pendente"),
        "loja_origem_definida": bool(rascunho.get("loja_origem_id")),
    })


@perfil_required(*GRUPOS_ACESSO_TOTAL, GRUPO_MOTORISTA)
def rotas_painel(request):
    """Painel de acompanhamento das rotas de entrega. Motorista só vê e atua
    nas rotas atribuídas a ele; compra_venda/admin veem e atuam em todas."""
    if request.method == "POST" and request.POST.get("acao") == "marcar_entregue":
        parada = get_object_or_404(ParadaRota, pk=request.POST.get("parada_id"))
        pode_marcar = tem_acesso_total(request.user) or parada.nota_fiscal_saida.motorista_id == request.user.id

        if not pode_marcar:
            messages.error(request, "Você não tem permissão para confirmar esta entrega.")
        elif parada.status == "recebido":
            messages.info(request, "Esta parada já estava marcada como recebida.")
        else:
            with transaction.atomic():
                for item in parada.itens.select_related("produto"):
                    _obter_ou_criar_lote(
                        item.produto, None, item.validade, parada.loja_destino,
                        item.quantidade, "aprovado", request.user,
                    )
                parada.status = "recebido"
                parada.data_recebimento = timezone.now()
                parada.recebido_por = request.user
                parada.save(update_fields=["status", "data_recebimento", "recebido_por"])
            messages.success(request, f"Parada em '{parada.loja_destino.nome}' marcada como entregue — estoque já disponível lá.")
        return redirect("estoque:rotas_painel")

    rotas_qs = (
        NotaFiscalSaida.objects.select_related("loja_origem", "responsavel_envio", "motorista")
        .prefetch_related("paradas__loja_destino", "paradas__itens__produto", "paradas__recebido_por")
        .order_by("-data_envio")
    )
    if eh_motorista(request.user) and not tem_acesso_total(request.user):
        rotas_qs = rotas_qs.filter(motorista=request.user)

    hoje = date.today()
    rotas = []
    total_ativas = 0
    total_hoje = 0
    total_atrasadas = 0
    for rota in rotas_qs:
        status_calc = rota.status_calculado
        if status_calc != "concluida":
            total_ativas += 1
        for parada in rota.paradas.all():
            if parada.status != "recebido" and parada.prazo and parada.prazo.date() == hoje:
                total_hoje += 1
            if parada.status_calculado == "atrasado":
                total_atrasadas += 1
        rotas.append({
            "rota": rota, "status_calculado": status_calc,
            "pode_marcar": tem_acesso_total(request.user) or rota.motorista_id == request.user.id,
        })

    return render(request, "estoque/rotas_painel.html", {
        "rotas": rotas, "total_ativas": total_ativas,
        "total_hoje": total_hoje, "total_atrasadas": total_atrasadas,
    })
