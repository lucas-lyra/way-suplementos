import pandas as pd
import streamlit as st
from supabase import create_client, Client
from datetime import datetime, date, timezone
import urllib.parse

# --- 1. CADASTRE A EQUIPE DE COMPRAS E VENDAS AQUI ---
CONTATOS_COMPRA_VENDA = {
    "João (Compras e Vendas)": "5521988918455",
    "Maria (Gestão)": "5521988888888",
    "Equipe CD": "552133334444"
}

# --- 2. CONFIGURAÇÃO E CONEXÃO SEGURA ---
st.set_page_config(page_title="Way Suplementos", layout="wide", page_icon=":material/nutrition:")

try:
    URL = st.secrets["SUPABASE_URL"]
    KEY = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(URL, KEY)
except Exception as e:
    st.error("Erro crítico: Não foi possível conectar ao banco de dados.")
    st.stop()

# --- 3. CONTROLE DE LOGIN, PERFIL E PERMISSÕES (SESSÃO) ---
# Perfis de acesso:
#   cd            -> só lança recebimento (entrada); toda entrada fica pendente
#                    de aprovação, não conta como estoque disponível ainda.
#   compra_venda  -> acesso total (recebimento direto, remanejamento, retirada) +
#                    aprova/rejeita as entradas lançadas pelo cd.
#   admin         -> mesmas permissões da compra_venda + gestão de usuários.
PERFIS_LABEL = {"cd": "CD", "compra_venda": "Compra e Venda", "admin": "Coordenador/Admin"}

if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
if 'usuario' not in st.session_state:
    st.session_state['usuario'] = "Operador"
if 'usuario_id' not in st.session_state:
    st.session_state['usuario_id'] = None
if 'perfil' not in st.session_state:
    st.session_state['perfil'] = None
if 'conta_ativa' not in st.session_state:
    st.session_state['conta_ativa'] = False
if 'bip_entrada_key_idx' not in st.session_state:
    st.session_state['bip_entrada_key_idx'] = 0
if 'bip_saida_key_idx' not in st.session_state:
    st.session_state['bip_saida_key_idx'] = 0

def obter_ou_criar_perfil_usuario(auth_uid, apelido):
    """Busca o perfil/permissões do usuário logado na tabela `usuarios`. Se for o
    primeiro login do sistema (tabela ainda vazia), provisiona automaticamente como
    admin ativo — bootstrap necessário porque, sem isso, ninguém teria perfil
    'admin' para liberar os próximos usuários. Qualquer login seguinte sem
    registro entra travado (perfil nulo, ativo=False) até um admin liberar em
    Gestão de Usuários."""
    res = supabase.table("usuarios").select("*").eq("id", auth_uid).execute()
    if res.data:
        return res.data[0]

    ja_existe_algum_usuario = bool(supabase.table("usuarios").select("id").limit(1).execute().data)
    eh_primeiro_login = not ja_existe_algum_usuario

    novo = supabase.table("usuarios").insert({
        "id": auth_uid,
        "apelido": apelido,
        "perfil": "admin" if eh_primeiro_login else None,
        "ativo": eh_primeiro_login
    }).execute()
    return novo.data[0]

def realizar_login(apelido, senha):
    try:
        email_formatado = f"{apelido.lower().strip()}@way.com"
        auth_resp = supabase.auth.sign_in_with_password({"email": email_formatado, "password": senha})
        auth_uid = auth_resp.user.id
        perfil_row = obter_ou_criar_perfil_usuario(auth_uid, apelido.strip())

        st.session_state['autenticado'] = True
        st.session_state['usuario'] = apelido.strip()
        st.session_state['usuario_id'] = auth_uid
        st.session_state['perfil'] = perfil_row.get('perfil')
        st.session_state['conta_ativa'] = bool(perfil_row.get('ativo'))
        st.rerun()
    except Exception as e:
        st.error("Apelido ou senha incorretos! Tente novamente.", icon=":material/error:")

def fazer_logout():
    supabase.auth.sign_out()
    st.session_state['autenticado'] = False
    st.session_state['usuario'] = "Operador"
    st.session_state['usuario_id'] = None
    st.session_state['perfil'] = None
    st.session_state['conta_ativa'] = False
    st.rerun()

# --- 4. VARIÁVEIS GERAIS ---
# Colunas exibidas na Planilha Compartilhada (visão tipo Excel, uso da equipe de compra/venda)
COLUNAS_PLANILHA = ["Status", "Código de Barras", "Produto", "Marca", "Lote", "Validade", "Dias para Vencer", "Quantidade", "Loja/CD Atual"]
# Colunas do estoque consolidado por produto (visão operacional do CD, na tela de Recebimento)
COLUNAS_ESTOQUE_CD = ["Status Geral", "Nível de Estoque", "Código de Barras", "Produto", "Marca", "Quantidade Total"]

# --- 5. FUNÇÕES AUXILIARES GENÉRICAS ---
def buscar_todas_linhas(query_factory, tamanho_pagina=1000):
    """Executa uma query Supabase paginando em blocos de `tamanho_pagina` linhas.
    O PostgREST (usado pelo Supabase) limita cada resposta a 1000 linhas por padrão;
    sem paginação, tabelas que crescem além disso perdem dados silenciosamente
    nas telas e nos relatórios CSV. `query_factory` deve ser uma função que retorna
    uma query nova (sem .execute()) a cada chamada, para poder aplicar `.range()`."""
    todas_as_linhas = []
    offset = 0
    while True:
        res = query_factory().range(offset, offset + tamanho_pagina - 1).execute()
        lote = res.data or []
        todas_as_linhas.extend(lote)
        if len(lote) < tamanho_pagina:
            break
        offset += tamanho_pagina
    return todas_as_linhas

def formatar_data_hora(iso_str):
    """Converte um timestamp ISO (como vem do Supabase) para 'dd/mm/aaaa hh:mm'."""
    if not iso_str:
        return "-"
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(iso_str)[:16]

def obter_lojas_destino(lista_lojas, unidade):
    """Lista de possíveis lojas de destino para remanejamento, excluindo a loja atual,
    com um fallback padrão caso ainda não haja outras lojas cadastradas."""
    destinos = [f for f in lista_lojas if f != unidade]
    if not destinos:
        destinos = ["Loja Centro", "Loja Barra", "Loja Copacabana", "Loja Niterói", "Outra Loja"]
    return destinos

def classificar_validade(dias):
    """Classificação de validade usada nos alertas estratégicos do CD (janela de 6/9
    meses, pensada para dar tempo de planejar remanejamento com a equipe de compras)."""
    if dias is None or pd.isna(dias): return "⚠️ Validade Inválida"
    if dias < 0: return "❌ Vencido"
    elif dias <= 180: return "🔴 Crítico (<=6 meses)"
    elif dias <= 270: return "🟡 Atenção (<=9 meses)"
    else: return "🟢 OK"

def classificar_status_planilha(dias):
    """Classificação de validade usada na Planilha Compartilhada (janela curta de
    30/90 dias, pensada para ação imediata da equipe de compra e venda)."""
    if dias is None or pd.isna(dias): return "⚠️ Validade Inválida"
    if dias < 0: return "❌ Vencido"
    elif dias <= 30: return "🔴 Crítico (<=30 dias)"
    elif dias <= 90: return "🟡 Atenção (<=90 dias)"
    else: return "🟢 OK"

def classificar_nivel_estoque(qtd_total, limite_minimo):
    if qtd_total <= 3:
        return "🔴 Ruptura Crítica"
    elif qtd_total <= limite_minimo:
        return "🟡 Baixo Estoque"
    else:
        return "🟢 Normal"

def classificar_status_lote(status):
    return {
        "aprovado": "✅ Aprovado",
        "aguardando_aprovacao": "🕓 Aguardando Aprovação",
        "rejeitado": "❌ Rejeitado"
    }.get(status, status or "-")

def identificar_categoria_suplemento(nome):
    nome_lower = str(nome).lower()
    if any(k in nome_lower for k in ["whey", "isolad", "concentrad", "hidrolisad", "albumina", "proteina", "proteína", "blend", "casein", "beef"]):
        return "Proteínas / Whey"
    elif any(k in nome_lower for k in ["creatina", "creatine", "creapure"]):
        return "Creatinas"
    elif any(k in nome_lower for k in ["pre-treino", "pré-treino", "pre treino", "pré treino", "cafeina", "cafeína", "termogenico", "termogênico", "pump", "booster", "panic", "horus"]):
        return "Pré-Treino / Energia"
    elif any(k in nome_lower for k in ["bcaa", "amino", "eaa"]):
        return "Aminoácidos / BCAA"
    elif any(k in nome_lower for k in ["glutamina", "glutamine"]):
        return "Glutamina"
    elif any(k in nome_lower for k in ["hipercalorico", "hipercalórico", "mass", "massa"]):
        return "Hipercalóricos"
    elif any(k in nome_lower for k in ["barra", "barrinha", "snack", "pasta de amendoim", "biscoito", "wafer", "alfajor"]):
        return "Barras / Snacks"
    elif any(k in nome_lower for k in ["vitamina", "multivitaminico", "multivitamínico", "omega", "ômega", "zinco", "magnesio", "magnésio", "coenzima", "melatonina", "colageno", "colágeno"]):
        return "Vitaminas / Saúde"
    else:
        return "Outros Suplementos"

# --- 6. FUNÇÕES DE BANCO DE DADOS (schema: produtos / lojas / lotes / movimentacoes / perdas / usuarios) ---
def obter_ou_criar_loja(nome):
    res = supabase.table("lojas").select("id").eq("nome", nome).execute()
    if res.data:
        return res.data[0]["id"]
    novo = supabase.table("lojas").insert({"nome": nome}).execute()
    return novo.data[0]["id"]

def buscar_produto_por_codigo(cod):
    res = supabase.table("produtos").select("id, nome, marca").eq("codigo_barras", cod).limit(1).execute()
    return res.data[0] if res.data else None

def obter_ou_criar_produto(cod, nome=None, marca=None):
    existente = buscar_produto_por_codigo(cod)
    if existente:
        return existente["id"]
    novo = supabase.table("produtos").insert({"codigo_barras": cod, "nome": nome, "marca": marca}).execute()
    return novo.data[0]["id"]

def obter_ou_criar_lote(produto_id, lote_txt, validade_iso, loja_id, quantidade_incremento, status="aprovado", criado_por=None):
    """Busca um lote já existente com o mesmo produto+lote+validade+loja+status e
    soma a quantidade a ele; se não existir, cria um novo registro. Retorna o id
    do lote. O filtro por `status` é essencial: uma entrada pendente de aprovação
    NUNCA deve se juntar a um lote já aprovado (e vice-versa), senão quantidade
    não aprovada passaria a contar como estoque real."""
    query = supabase.table("lotes").select("id, quantidade").eq("produto_id", produto_id).eq("validade", validade_iso).eq("loja_atual_id", loja_id).eq("status", status)
    query = query.eq("lote", lote_txt) if lote_txt else query.is_("lote", "null")
    res = query.limit(1).execute()

    if res.data:
        lote_id = res.data[0]["id"]
        nova_qtd = res.data[0]["quantidade"] + quantidade_incremento
        supabase.table("lotes").update({"quantidade": nova_qtd}).eq("id", lote_id).execute()
        return lote_id

    dados_novo_lote = {
        "produto_id": produto_id,
        "lote": lote_txt,
        "validade": validade_iso,
        "loja_atual_id": loja_id,
        "quantidade": quantidade_incremento,
        "status": status
    }
    if criado_por:
        dados_novo_lote["criado_por"] = criado_por
    novo = supabase.table("lotes").insert(dados_novo_lote).execute()
    return novo.data[0]["id"]

def registrar_entrada(cod, nome, marca, lote_txt, validade, quantidade, loja_id, usuario, observacoes="", status="aprovado"):
    try:
        produto_id = obter_ou_criar_produto(cod, nome, marca)
        validade_iso = str(validade)
        quantidade = int(quantidade)
        lote_id = obter_ou_criar_lote(produto_id, (lote_txt or "").strip() or None, validade_iso, loja_id, quantidade, status=status, criado_por=usuario)

        # A movimentação (ledger de estoque real) só é registrada quando a entrada
        # já nasce aprovada. Uma entrada pendente vira movimentação só no momento
        # em que for aprovada (ver aprovar_entrada_pendente).
        if status == "aprovado":
            supabase.table("movimentacoes").insert({
                "lote_id": lote_id,
                "tipo": "entrada",
                "loja_origem_id": None,
                "loja_destino_id": loja_id,
                "quantidade": quantidade,
                "responsavel": usuario,
                "observacoes": observacoes
            }).execute()
            st.success(f"Entrada de {quantidade} un. de '{nome}' registrada com sucesso!", icon=":material/check_circle:")
        else:
            st.success(f"Entrada de {quantidade} un. de '{nome}' registrada como **pendente** — aguardando aprovação da Compra e Venda ou do Coordenador.", icon=":material/pending_actions:")
        return True
    except Exception as e:
        st.error(f"Erro ao registrar entrada: {e}")
        return False

def carregar_lotes_pendentes():
    """Entradas lançadas pelo perfil 'cd' aguardando aprovação — ainda não contam
    como estoque disponível na Planilha Compartilhada."""
    try:
        dados = buscar_todas_linhas(lambda: supabase.table("lotes").select(
            "*, produtos(codigo_barras, nome, marca), lojas(nome)"
        ).eq("status", "aguardando_aprovacao"))
        if not dados:
            return pd.DataFrame()

        linhas = []
        for l in dados:
            produto = l.get("produtos") or {}
            loja = l.get("lojas") or {}
            try:
                validade_fmt = datetime.strptime(l["validade"], "%Y-%m-%d").date().strftime("%d/%m/%Y")
            except (TypeError, ValueError):
                validade_fmt = "⚠️ Data Inválida"

            linhas.append({
                "lote_id": l.get("id"),
                "Código de Barras": produto.get("codigo_barras", ""),
                "Produto": produto.get("nome", ""),
                "Marca": produto.get("marca", ""),
                "Lote": l.get("lote") or "-",
                "Validade": validade_fmt,
                "Quantidade": l.get("quantidade", 0),
                "Loja/CD Destino": loja.get("nome", "N/A"),
                "Lançado Por": l.get("criado_por", ""),
                "Lançado Em": formatar_data_hora(l.get("criado_em")),
            })
        return pd.DataFrame(linhas).sort_values(by="Lançado Em")
    except Exception as e:
        st.error(f"Erro ao carregar pendências: {e}")
        return pd.DataFrame()

def carregar_minhas_entradas_pendentes(usuario):
    """Entradas lançadas por este usuário que ainda estão pendentes ou que foram
    rejeitadas — para o perfil 'cd' acompanhar o status do que já bipou."""
    try:
        dados = buscar_todas_linhas(lambda: supabase.table("lotes").select(
            "*, produtos(codigo_barras, nome, marca), lojas(nome)"
        ).eq("criado_por", usuario).in_("status", ["aguardando_aprovacao", "rejeitado"]))
        if not dados:
            return pd.DataFrame()

        linhas = []
        for l in dados:
            produto = l.get("produtos") or {}
            loja = l.get("lojas") or {}
            linhas.append({
                "Situação": classificar_status_lote(l.get("status")),
                "Produto": produto.get("nome", ""),
                "Lote": l.get("lote") or "-",
                "Quantidade": l.get("quantidade", 0),
                "Loja/CD": loja.get("nome", "N/A"),
                "Lançado Em": formatar_data_hora(l.get("criado_em")),
                "Motivo da Rejeição": l.get("motivo_rejeicao") or "-",
            })
        return pd.DataFrame(linhas).sort_values(by="Lançado Em", ascending=False)
    except Exception as e:
        st.error(f"Erro ao carregar suas entradas: {e}")
        return pd.DataFrame()

def aprovar_entrada_pendente(lote_id, usuario):
    try:
        res = supabase.table("lotes").select("*, produtos(nome)").eq("id", lote_id).execute()
        if not res.data:
            st.error("Lote não encontrado.")
            return False
        lote = res.data[0]

        supabase.table("lotes").update({
            "status": "aprovado",
            "aprovado_por": usuario,
            "aprovado_em": datetime.now(timezone.utc).isoformat()
        }).eq("id", lote_id).execute()

        supabase.table("movimentacoes").insert({
            "lote_id": lote_id,
            "tipo": "entrada",
            "loja_origem_id": None,
            "loja_destino_id": lote["loja_atual_id"],
            "quantidade": lote["quantidade"],
            "responsavel": lote.get("criado_por") or usuario,
            "observacoes": f"Aprovado por {usuario}"
        }).execute()

        nome_produto = (lote.get("produtos") or {}).get("nome", "produto")
        st.success(f"Entrada de '{nome_produto}' aprovada e já conta como estoque disponível!", icon=":material/check_circle:")
        return True
    except Exception as e:
        st.error(f"Erro ao aprovar entrada: {e}")
        return False

def rejeitar_entrada_pendente(lote_id, motivo, usuario):
    try:
        supabase.table("lotes").update({
            "status": "rejeitado",
            "aprovado_por": usuario,
            "aprovado_em": datetime.now(timezone.utc).isoformat(),
            "motivo_rejeicao": motivo or "Não informado"
        }).eq("id", lote_id).execute()
        st.warning("Entrada rejeitada. O responsável pelo lançamento pode ver o motivo e corrigir.", icon=":material/cancel:")
        return True
    except Exception as e:
        st.error(f"Erro ao rejeitar entrada: {e}")
        return False

def registrar_remanejamento(lote_id, produto_id, lote_txt, validade_iso, loja_origem_id, loja_destino_id, quantidade, usuario, observacoes=""):
    try:
        quantidade = int(quantidade)

        res_lote = supabase.table("lotes").select("quantidade").eq("id", lote_id).execute()
        if not res_lote.data:
            st.error("Lote de origem não encontrado.")
            return False
        qtd_atual = res_lote.data[0]["quantidade"]
        if quantidade > qtd_atual:
            st.error("Quantidade maior que o saldo disponível no lote de origem.")
            return False

        # 1. Baixa na origem primeiro. O lote NUNCA é deletado (mesmo zerando a
        # quantidade), para que o histórico de movimentações continue rastreável
        # mesmo depois que o saldo daquele lote acabar.
        supabase.table("lotes").update({"quantidade": qtd_atual - quantidade}).eq("id", lote_id).execute()

        # 2. Soma (ou cria) o lote equivalente na loja de destino (sempre aprovado —
        # remanejamento só é feito por quem já tem acesso total).
        obter_ou_criar_lote(produto_id, lote_txt, validade_iso, loja_destino_id, quantidade, status="aprovado")

        # 3. Registra a movimentação
        supabase.table("movimentacoes").insert({
            "lote_id": lote_id,
            "tipo": "remanejamento",
            "loja_origem_id": loja_origem_id,
            "loja_destino_id": loja_destino_id,
            "quantidade": quantidade,
            "responsavel": usuario,
            "observacoes": observacoes
        }).execute()

        st.success(f"Remanejamento de {quantidade} un. registrado com sucesso!", icon=":material/check_circle:")
        return True
    except Exception as e:
        st.error(f"Erro ao processar remanejamento: {e}")
        return False

def dar_baixa_perda(lote_id, loja_id, quantidade, custo_unit, motivo, observacoes, usuario):
    try:
        quantidade = int(quantidade)
        res_lote = supabase.table("lotes").select("*, produtos(codigo_barras, nome, marca)").eq("id", lote_id).execute()
        if not res_lote.data:
            st.error("Lote não encontrado.")
            return False

        lote = res_lote.data[0]
        if quantidade > lote["quantidade"]:
            st.error("Quantidade maior que o saldo disponível no lote.")
            return False

        prejuizo_total = round(float(quantidade) * float(custo_unit), 2)

        # 1. Baixa no lote PRIMEIRO (nunca deleta, só zera — mesma lógica do
        # remanejamento). Se isso falhar, nenhuma perda é registrada.
        supabase.table("lotes").update({"quantidade": lote["quantidade"] - quantidade}).eq("id", lote_id).execute()

        # 2. Registra na tabela de perdas (mantida como já existia, com um snapshot
        # dos dados do produto/lote no momento da baixa — assim o histórico continua
        # legível mesmo que o lote seja totalmente consumido depois).
        produto_info = lote.get("produtos") or {}
        dado_perda = {
            "loja_id": loja_id,
            "lote_id": lote_id,
            "codigo_barras": produto_info.get("codigo_barras", ""),
            "nome": produto_info.get("nome", ""),
            "marca": produto_info.get("marca", ""),
            "validade": lote.get("validade", ""),
            "quantidade": quantidade,
            "custo_unitario": float(custo_unit),
            "prejuizo_total": prejuizo_total,
            "motivo": motivo,
            "observacoes": observacoes,
            "usuario": usuario
        }
        try:
            supabase.table("perdas").insert(dado_perda).execute()
        except Exception as e_perda:
            st.warning(f"Nota: Tabela 'perdas' não foi gravada ({e_perda}). Verifique se o schema da tabela 'perdas' foi atualizado no Supabase (veja o SQL no expander abaixo).", icon=":material/warning:")

        st.success(f"Baixa de {quantidade} un. de '{produto_info.get('nome','produto')}' concluída! Prejuízo registrado: R$ {prejuizo_total:.2f}", icon=":material/check_circle:")
        return True
    except Exception as e:
        st.error(f"Erro ao processar baixa por perda: {e}")
        return False

def carregar_estoque_atual():
    """Carrega os lotes com saldo (quantidade > 0) e já APROVADOS, com dias para
    vencer e status calculados, ordenados por validade (FEFO). Base tanto da
    Planilha Compartilhada quanto das telas de Recebimento e Remanejamento — uma
    entrada ainda pendente de aprovação nunca aparece aqui."""
    try:
        dados = buscar_todas_linhas(lambda: supabase.table("lotes").select("*, produtos(codigo_barras, nome, marca), lojas(nome)").eq("status", "aprovado").gt("quantidade", 0))
        if not dados:
            return pd.DataFrame()

        hoje = date.today()
        linhas = []
        for l in dados:
            produto = l.get("produtos") or {}
            loja = l.get("lojas") or {}
            try:
                dt_validade = datetime.strptime(l["validade"], "%Y-%m-%d").date()
                dias = (dt_validade - hoje).days
                validade_fmt = dt_validade.strftime("%d/%m/%Y")
            except (TypeError, ValueError):
                dias = None
                validade_fmt = "⚠️ Data Inválida"

            linhas.append({
                "lote_id": l.get("id"),
                "produto_id": l.get("produto_id"),
                "loja_id": l.get("loja_atual_id"),
                "lote_raw": l.get("lote"),
                "validade_iso": l.get("validade"),
                "Código de Barras": produto.get("codigo_barras", ""),
                "Produto": produto.get("nome", ""),
                "Marca": produto.get("marca", ""),
                "Lote": l.get("lote") or "-",
                "Validade": validade_fmt,
                "Dias para Vencer": dias,
                "Status": classificar_status_planilha(dias),
                "Status CD": classificar_validade(dias),
                "Quantidade": l.get("quantidade", 0),
                "Loja/CD Atual": loja.get("nome", "N/A"),
            })

        df = pd.DataFrame(linhas)
        # FEFO: o que vence primeiro aparece primeiro; lotes com validade inválida vão para o fim.
        return df.sort_values(by="Dias para Vencer", na_position="last")
    except Exception as e:
        st.error(f"Erro ao carregar estoque: {e}")
        return pd.DataFrame()

def agrupar_por_produto(df_lotes):
    """Agrega o estoque (já filtrado por loja) por produto: soma quantidade e
    calcula o status a partir do lote mais próximo do vencimento (FEFO)."""
    if df_lotes.empty:
        return pd.DataFrame()

    grupos = []
    for cod, grupo in df_lotes.groupby("Código de Barras"):
        tem_invalido = grupo["Dias para Vencer"].isna().any()
        dias_validos = grupo["Dias para Vencer"].dropna()
        menor_dias = dias_validos.min() if not dias_validos.empty else None
        grupos.append({
            "Código de Barras": cod,
            "Produto": grupo.iloc[0]["Produto"],
            "Marca": grupo.iloc[0]["Marca"],
            "Quantidade Total": grupo["Quantidade"].sum(),
            "Menor Dias": menor_dias,
            "Status Geral": "⚠️ Validade Inválida (verificar lote)" if tem_invalido else classificar_validade(menor_dias),
        })
    return pd.DataFrame(grupos).sort_values(by="Menor Dias", na_position="last")

def carregar_todos_lotes():
    """Todos os lotes já cadastrados, de QUALQUER status (aprovado, pendente ou
    rejeitado) e mesmo com saldo zerado — diferente de carregar_estoque_atual(),
    que só traz estoque aprovado com saldo > 0. Usado na Linha do Tempo do Lote,
    onde é preciso encontrar um lote mesmo depois de totalmente consumido, ou
    ainda sem aprovação, para fins de auditoria."""
    try:
        dados = buscar_todas_linhas(lambda: supabase.table("lotes").select("*, produtos(codigo_barras, nome, marca), lojas(nome)"))
        if not dados:
            return pd.DataFrame()

        linhas = []
        for l in dados:
            produto = l.get("produtos") or {}
            loja = l.get("lojas") or {}
            try:
                validade_fmt = datetime.strptime(l["validade"], "%Y-%m-%d").date().strftime("%d/%m/%Y")
            except (TypeError, ValueError):
                validade_fmt = "⚠️ Data Inválida"

            linhas.append({
                "lote_id": l.get("id"),
                "produto_id": l.get("produto_id"),
                "lote_raw": l.get("lote"),
                "validade_iso": l.get("validade"),
                "Código de Barras": produto.get("codigo_barras", ""),
                "Produto": produto.get("nome", ""),
                "Marca": produto.get("marca", ""),
                "Lote": l.get("lote") or "-",
                "Validade": validade_fmt,
                "Situação": classificar_status_lote(l.get("status")),
                "Quantidade Atual": l.get("quantidade", 0),
                "Loja/CD Atual": loja.get("nome", "N/A"),
            })
        return pd.DataFrame(linhas)
    except Exception as e:
        st.error(f"Erro ao carregar lotes: {e}")
        return pd.DataFrame()

def buscar_lote_ids_relacionados(produto_id, lote_txt, validade_iso):
    """IDs de todas as linhas de `lotes` que representam o mesmo lote físico (mesmo
    produto + código de lote + validade) em qualquer loja onde ele já esteve —
    já que cada loja tem sua própria linha na tabela `lotes`. Base para montar a
    linha do tempo completa de um lote."""
    query = supabase.table("lotes").select("id").eq("produto_id", produto_id).eq("validade", validade_iso)
    query = query.eq("lote", lote_txt) if lote_txt else query.is_("lote", "null")
    res = query.execute()
    return [row["id"] for row in (res.data or [])]

def carregar_timeline_lote(produto_id, lote_txt, validade_iso):
    """Linha do tempo completa de um lote físico: toda entrada, remanejamento e
    baixa por perda que ele já passou, em qualquer loja, mesmo que hoje o saldo
    esteja zerado ou espalhado em lojas diferentes. Essencial para rastreabilidade/auditoria."""
    try:
        lote_ids = buscar_lote_ids_relacionados(produto_id, lote_txt, validade_iso)
        if not lote_ids:
            return pd.DataFrame()

        eventos = []

        dados_mov = buscar_todas_linhas(lambda: supabase.table("movimentacoes").select(
            "*, origem:lojas!loja_origem_id(nome), destino:lojas!loja_destino_id(nome)"
        ).in_("lote_id", lote_ids))
        for m in dados_mov:
            eventos.append({
                "_data_raw": m.get("data"),
                "Data/Hora": formatar_data_hora(m.get("data")),
                "Tipo": "📥 Entrada" if m.get("tipo") == "entrada" else "🔁 Remanejamento",
                "Origem": (m.get("origem") or {}).get("nome", "-"),
                "Destino": (m.get("destino") or {}).get("nome", "-"),
                "Quantidade": m.get("quantidade", 0),
                "Responsável": m.get("responsavel", ""),
                "Observações": m.get("observacoes") or ""
            })

        dados_perdas = buscar_todas_linhas(lambda: supabase.table("perdas").select("*, lojas(nome)").in_("lote_id", lote_ids))
        for p in dados_perdas:
            eventos.append({
                "_data_raw": p.get("created_at"),
                "Data/Hora": formatar_data_hora(p.get("created_at")),
                "Tipo": "🗑️ Baixa por Perda",
                "Origem": (p.get("lojas") or {}).get("nome", "-"),
                "Destino": "-",
                "Quantidade": p.get("quantidade", 0),
                "Responsável": p.get("usuario", ""),
                "Observações": f"Motivo: {p.get('motivo', '')}. {p.get('observacoes') or ''}".strip()
            })

        if not eventos:
            return pd.DataFrame()

        df = pd.DataFrame(eventos)
        return df.sort_values(by="_data_raw", na_position="last").drop(columns=["_data_raw"])
    except Exception as e:
        st.error(f"Erro ao carregar linha do tempo do lote: {e}")
        return pd.DataFrame()

def carregar_historico_movimentacoes(tipo_filtro=None, loja_filtro=None):
    """Histórico de entradas e remanejamentos (tabela `movimentacoes`)."""
    try:
        dados = buscar_todas_linhas(lambda: supabase.table("movimentacoes").select(
            "*, lotes(lote, validade, produtos(codigo_barras, nome, marca)), origem:lojas!loja_origem_id(nome), destino:lojas!loja_destino_id(nome)"
        ).order("data", desc=True))
        if not dados:
            return pd.DataFrame()

        linhas = []
        for m in dados:
            lote = m.get("lotes") or {}
            produto = lote.get("produtos") or {}
            loja_origem = (m.get("origem") or {}).get("nome", "-")
            loja_destino = (m.get("destino") or {}).get("nome", "-")

            if tipo_filtro and m.get("tipo") != tipo_filtro:
                continue
            if loja_filtro and loja_filtro != "Todas" and loja_filtro not in (loja_origem, loja_destino):
                continue

            linhas.append({
                "Data/Hora": formatar_data_hora(m.get("data")),
                "Tipo": "📥 Entrada" if m.get("tipo") == "entrada" else "🔁 Remanejamento",
                "Origem": loja_origem,
                "Destino": loja_destino,
                "Código de Barras": produto.get("codigo_barras", ""),
                "Produto": produto.get("nome", ""),
                "Marca": produto.get("marca", ""),
                "Lote": lote.get("lote") or "-",
                "Validade": lote.get("validade", ""),
                "Quantidade": m.get("quantidade", 0),
                "Responsável": m.get("responsavel", ""),
                "Observações": m.get("observacoes", "")
            })
        return pd.DataFrame(linhas)
    except Exception as e:
        st.error(f"Erro ao carregar histórico de movimentações: {e}")
        return pd.DataFrame()

def carregar_historico_perdas(loja_filtro=None):
    try:
        dados = buscar_todas_linhas(lambda: supabase.table("perdas").select("*, lojas(nome)").order("created_at", desc=True))
        if not dados:
            return pd.DataFrame()

        dados_perdas = []
        for item in dados:
            loja_nome = item["lojas"]["nome"] if item.get("lojas") else "N/A"
            if loja_filtro and loja_filtro != "Todas" and loja_nome != loja_filtro:
                continue

            dados_perdas.append({
                "Data/Hora": formatar_data_hora(item.get("created_at", "")),
                "Loja/CD": loja_nome,
                "Código de Barras": item.get("codigo_barras", ""),
                "Nome": item.get("nome", ""),
                "Categoria": identificar_categoria_suplemento(item.get("nome", "")),
                "Marca": item.get("marca", ""),
                "Validade": item.get("validade", ""),
                "Qtd Descartada": item.get("quantidade", 0),
                "Custo Unit. (R$)": f"R$ {float(item.get('custo_unitario', 0)):.2f}",
                "Prejuízo (R$)": float(item.get("prejuizo_total", 0.0)),
                "Motivo": item.get("motivo", ""),
                "Responsável": item.get("usuario", ""),
                "Observações": item.get("observacoes", "")
            })
        return pd.DataFrame(dados_perdas)
    except Exception:
        return pd.DataFrame()

def carregar_usuarios():
    """Lista de contas com perfil já provisionado (tabela `usuarios`), para a
    tela de Gestão de Usuários (perfil admin)."""
    try:
        dados = buscar_todas_linhas(lambda: supabase.table("usuarios").select("*").order("created_at"))
        if not dados:
            return pd.DataFrame()

        linhas = []
        for u in dados:
            linhas.append({
                "id": u.get("id"),
                "Apelido": u.get("apelido", ""),
                "Perfil": u.get("perfil") or "(sem perfil)",
                "Ativo": "✅ Sim" if u.get("ativo") else "❌ Não",
                "Criado Em": formatar_data_hora(u.get("created_at")),
            })
        return pd.DataFrame(linhas)
    except Exception as e:
        st.error(f"Erro ao carregar usuários: {e}")
        return pd.DataFrame()

def atualizar_perfil_usuario(usuario_id, perfil, ativo):
    try:
        supabase.table("usuarios").update({"perfil": perfil, "ativo": ativo}).eq("id", usuario_id).execute()
        st.success("Perfil atualizado com sucesso!", icon=":material/check_circle:")
        return True
    except Exception as e:
        st.error(f"Erro ao atualizar perfil: {e}")
        return False

# --- 7. TELAS DO APLICATIVO ---
def tela_login():
    st.title(":material/lock: Way Suplementos - Acesso Restrito")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("form_login"):
            apelido = st.text_input("Apelido")
            senha = st.text_input("Senha", type="password")
            btn_login = st.form_submit_button("Entrar no Sistema")
            if btn_login: realizar_login(apelido, senha)

def tela_principal():
    perfil = st.session_state.get('perfil')
    conta_ativa = st.session_state.get('conta_ativa', False)

    st.title(":material/inventory_2: Way Suplementos - Controle de Lote/Validade & Remanejamento")

    with st.sidebar:
        st.header(":material/settings: Configurações")
        st.caption(f":material/person: Usuário: **{st.session_state.get('usuario', 'Operador')}**")
        if perfil:
            st.caption(f":material/badge: Perfil: **{PERFIS_LABEL.get(perfil, perfil)}**")
        st.button("Sair (Logout)", on_click=fazer_logout, type="primary")

    if not conta_ativa or not perfil:
        st.warning(
            "Sua conta ainda não foi liberada por um administrador. Peça para alguém com perfil "
            "**Compra e Venda** ou **Coordenador/Admin** liberar seu acesso na tela de Gestão de Usuários.",
            icon=":material/lock_clock:"
        )
        return

    try:
        res_l = supabase.table("lojas").select("nome").execute()
        lista_lojas = [f['nome'] for f in res_l.data] if res_l.data else ["Centro de Distribuição"]
    except:
        lista_lojas = ["Centro de Distribuição"]

    with st.sidebar:
        st.divider()
        unidade = st.selectbox("Loja/CD Atual", options=lista_lojas)
        st.divider()
        st.subheader(":material/inventory_2: Parâmetros de Estoque")
        limite_minimo = st.number_input("Estoque Mínimo de Segurança (un):", min_value=1, max_value=500, value=10, step=1)

    usuario_atual = st.session_state.get('usuario', 'Operador')

    # Monta a lista de abas de acordo com o perfil: 'cd' só lança recebimento
    # (pendente de aprovação); 'compra_venda' e 'admin' têm acesso total, e só
    # 'admin' também vê Gestão de Usuários.
    definicoes_abas = [("recebimento", ":material/move_to_inbox: Recebimento")]
    if perfil in ("compra_venda", "admin"):
        definicoes_abas += [
            ("remanejamento", ":material/sync_alt: Remanejamento"),
            ("planilha", ":material/table_chart: Planilha Compartilhada"),
            ("pendencias", ":material/pending_actions: Pendências de Aprovação"),
            ("movimentacoes", ":material/history: Histórico de Movimentações"),
            ("timeline", ":material/timeline: Linha do Tempo do Lote"),
            ("perdas", ":material/delete: Histórico de Baixas & Perdas"),
        ]
    if perfil == "admin":
        definicoes_abas.append(("usuarios", ":material/manage_accounts: Gestão de Usuários"))

    tabs_criadas = st.tabs([label for _, label in definicoes_abas])
    abas = dict(zip((chave for chave, _ in definicoes_abas), tabs_criadas))

    # ==================================================================
    # ABA: RECEBIMENTO (uso do CD e, com acesso total, compra_venda/admin)
    # ==================================================================
    with abas["recebimento"]:
        status_entrada = "aguardando_aprovacao" if perfil == "cd" else "aprovado"

        if perfil != "cd":
            df_estoque = carregar_estoque_atual()
            df_unidade = df_estoque[df_estoque["Loja/CD Atual"] == unidade].copy() if not df_estoque.empty else pd.DataFrame()
            df_produtos_unidade = agrupar_por_produto(df_unidade)
            if not df_produtos_unidade.empty:
                df_produtos_unidade["Nível de Estoque"] = df_produtos_unidade["Quantidade Total"].apply(lambda q: classificar_nivel_estoque(q, limite_minimo))

            st.subheader(":material/notifications_active: Painel de Alertas")
            if not df_produtos_unidade.empty:
                df_criticos = df_produtos_unidade[df_produtos_unidade["Menor Dias"] <= 180]
                qtd_critico = len(df_criticos)
                qtd_atencao = len(df_produtos_unidade[(df_produtos_unidade["Menor Dias"] > 180) & (df_produtos_unidade["Menor Dias"] <= 270)])
                df_ruptura = df_produtos_unidade[df_produtos_unidade["Nível de Estoque"] != "🟢 Normal"]
                qtd_ruptura = len(df_ruptura)

                col1, col2, col3, col4 = st.columns(4)
                col1.metric(":material/priority_high: Validade Crítica (<=6m)", qtd_critico)
                col2.metric(":material/warning: Validade Atenção (<=9m)", qtd_atencao)
                col3.metric(":material/inventory: Risco de Ruptura", qtd_ruptura)
                col4.metric(":material/category: Total de Produtos", len(df_produtos_unidade))

                if qtd_critico > 0:
                    st.warning(f"Há {qtd_critico} produto(s) com lotes críticos vencendo em menos de 6 meses em {unidade}!", icon=":material/warning:")

                    with st.expander("Avisar Compras/Vendas para Envio às Lojas (Validade Curta)", expanded=False, icon=":material/campaign:"):
                        c_vend, c_prod = st.columns(2)
                        contato_selecionado = c_vend.selectbox("Para quem deseja enviar o aviso?", list(CONTATOS_COMPRA_VENDA.keys()), key="contato_validade")

                        opcoes_produtos = []
                        for _, row in df_criticos.iterrows():
                            lotes_criticos = df_unidade[(df_unidade["Código de Barras"] == row["Código de Barras"]) & (df_unidade["Dias para Vencer"] <= 180)]
                            qtd_critica_unidades = int(lotes_criticos["Quantidade"].sum())
                            opcoes_produtos.append(f"{row['Produto']} - {row['Marca']} (Vencendo: {qtd_critica_unidades} un)")

                        produto_selecionado = c_prod.selectbox("Qual produto precisa ser enviado às lojas?", opcoes_produtos, key="prod_validade_sel")

                        numero_whats = CONTATOS_COMPRA_VENDA[contato_selecionado]
                        texto_msg = f"🚨 *ALERTA DO CD - VALIDADE CURTA*\nOlá!\n\nTemos o seguinte produto com lotes críticos aqui em {unidade}:\n📦 *{produto_selecionado}*\n\nPor favor, analisem para qual loja podemos remanejar esses lotes para acelerar a venda!"
                        link_whatsapp = f"https://wa.me/{numero_whats}?text={urllib.parse.quote(texto_msg)}"
                        st.link_button(f"Enviar WhatsApp para {contato_selecionado}", link_whatsapp, type="primary", icon=":material/send:")

                if qtd_ruptura > 0:
                    st.error(f"Atenção: Há {qtd_ruptura} produto(s) com estoque baixo/risco de ruptura em {unidade} (<= {limite_minimo} un)!", icon=":material/error:")

                    with st.expander("Avisar Compras para Reposição com Fornecedor (WhatsApp)", expanded=False, icon=":material/shopping_cart:"):
                        c_vend_r, c_prod_r = st.columns(2)
                        contato_compras = c_vend_r.selectbox("Contato do Setor de Compras:", list(CONTATOS_COMPRA_VENDA.keys()), key="contato_ruptura")

                        opcoes_ruptura = [f"{row['Produto']} - {row['Marca']} (Estoque Atual: {row['Quantidade Total']} un)" for _, row in df_ruptura.iterrows()]
                        prod_ruptura_selecionado = c_prod_r.selectbox("Produto com Risco de Ruptura:", opcoes_ruptura, key="prod_ruptura_sel")

                        num_whats_compras = CONTATOS_COMPRA_VENDA[contato_compras]
                        msg_ruptura = f"🚨 *ALERTA DE RUPTURA / ESTOQUE BAIXO*\nOlá!\n\nIdentificamos que o seguinte produto está com estoque crítico em {unidade}:\n📦 *{prod_ruptura_selecionado}*\n⚙️ *Estoque Mínimo Definido:* {limite_minimo} un\n\nPor favor, providenciem um novo pedido junto ao fornecedor!"
                        link_whats_ruptura = f"https://wa.me/{num_whats_compras}?text={urllib.parse.quote(msg_ruptura)}"
                        st.link_button(f"Solicitar Compra via WhatsApp para {contato_compras}", link_whats_ruptura, type="primary", icon=":material/send:")

            st.divider()

        st.subheader(":material/move_to_inbox: Recebimento de Mercadoria")
        if perfil == "cd":
            st.info("Sua entrada será registrada como **pendente** até ser aprovada pela Compra e Venda ou pelo Coordenador — só depois de aprovada ela conta como estoque disponível.", icon=":material/pending_actions:")
        st.info("**Como usar o leitor:** Clique no campo abaixo, aponte o leitor de código de barras para o produto e bipe (ou digite o código manualmente). O leitor digitará o código e dará Enter automaticamente.", icon=":material/info:")

        codigo_bipado = st.text_input(
            "Bipe ou digite o Código de Barras:",
            key=f"leitor_recebimento_{st.session_state['bip_entrada_key_idx']}",
            placeholder="Passe o leitor de código de barras..."
        )

        if codigo_bipado:
            cod_limpo = codigo_bipado.strip()
            try:
                produto_existente = buscar_produto_por_codigo(cod_limpo)
            except Exception as e_busca:
                produto_existente = None
                st.error(f"Erro ao consultar banco: {e_busca}")

            if produto_existente:
                st.success(f"**Produto Reconhecido:** {produto_existente['nome']} | **Marca:** {produto_existente.get('marca', '')} *(Código: {cod_limpo})*", icon=":material/check_circle:")

                with st.form("form_entrada_reconhecida", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    lote_txt = c1.text_input("Lote (opcional)", placeholder="Ex: L2408A")
                    val_chegou = c2.date_input("Data de Validade do Lote", value=date.today(), format="DD/MM/YYYY")

                    c3, c4 = st.columns(2)
                    qtd_chegou = c3.number_input("Quantidade que Chegou (Unidades)", min_value=1, value=1, step=1)
                    loja_idx = lista_lojas.index(unidade) if unidade in lista_lojas else 0
                    loja_destino_nome = c4.selectbox("Loja/CD de Destino", options=lista_lojas, index=loja_idx)

                    obs_chegou = st.text_input("Observações / NF (Opcional)", placeholder="Ex: NF 12345")

                    if st.form_submit_button("Confirmar Entrada no Estoque", type="primary", icon=":material/move_to_inbox:"):
                        loja_id = obter_ou_criar_loja(loja_destino_nome)
                        if registrar_entrada(cod_limpo, produto_existente["nome"], produto_existente.get("marca", ""), lote_txt, val_chegou, qtd_chegou, loja_id, usuario_atual, obs_chegou, status=status_entrada):
                            st.session_state['bip_entrada_key_idx'] += 1
                            st.rerun()
            else:
                st.warning(f"**Código de barras '{cod_limpo}' não encontrado no catálogo!** Preencha a ficha abaixo para o primeiro cadastro deste produto.", icon=":material/warning:")

                with st.form("form_novo_produto", clear_on_submit=True):
                    st.markdown("#### :material/inventory_2: Ficha do Produto (1º Cadastro)")
                    col_f1, col_f2 = st.columns(2)
                    with col_f1:
                        nome_ficha = st.text_input("Nome Completo do Produto", placeholder="Ex: 100% Whey Gold Standard 900g Baunilha")
                        lote_ficha = st.text_input("Lote (opcional)", placeholder="Ex: L2408A")
                    with col_f2:
                        marca_ficha = st.text_input("Marca / Fabricante", placeholder="Ex: Optimum Nutrition, Max Titanium...")
                        val_ficha = st.date_input("Validade do Primeiro Lote", value=date.today(), format="DD/MM/YYYY")

                    col_f3, col_f4 = st.columns(2)
                    with col_f3:
                        qtd_ficha = st.number_input("Quantidade Inicial (Unidades)", min_value=1, value=1, step=1)
                    with col_f4:
                        loja_idx = lista_lojas.index(unidade) if unidade in lista_lojas else 0
                        loja_destino_ficha = st.selectbox("Loja/CD de Destino", options=lista_lojas, index=loja_idx)

                    obs_ficha = st.text_input("Observações / NF (Opcional)", placeholder="Ex: 1º Cadastro / NF 9876")

                    if st.form_submit_button("Salvar Ficha e Dar Entrada no Estoque", type="primary", icon=":material/save:"):
                        if nome_ficha:
                            loja_id = obter_ou_criar_loja(loja_destino_ficha)
                            if registrar_entrada(cod_limpo, nome_ficha.strip(), marca_ficha.strip(), lote_ficha, val_ficha, qtd_ficha, loja_id, usuario_atual, obs_ficha, status=status_entrada):
                                st.session_state['bip_entrada_key_idx'] += 1
                                st.rerun()
                        else:
                            st.warning("Preencha pelo menos o **Nome do Produto**.", icon=":material/warning:")

        st.divider()
        if perfil == "cd":
            st.subheader(":material/pending_actions: Minhas Entradas Recentes")
            df_minhas = carregar_minhas_entradas_pendentes(usuario_atual)
            if df_minhas.empty:
                st.info("Nenhuma entrada pendente ou rejeitada no momento — tudo que você lançou já foi aprovado.")
            else:
                st.dataframe(df_minhas, use_container_width=True, hide_index=True)
        else:
            st.subheader(f":material/list_alt: Estoque Consolidado: {unidade}")
            if not df_produtos_unidade.empty:
                st.dataframe(df_produtos_unidade[COLUNAS_ESTOQUE_CD], use_container_width=True, hide_index=True)
            else:
                st.info("Nenhum produto encontrado nesta loja/CD.")

    # ==================================================================
    # ABA: REMANEJAMENTO (loja X -> loja Y) — compra_venda e admin
    # ==================================================================
    if "remanejamento" in abas:
        with abas["remanejamento"]:
            st.subheader(f":material/sync_alt: Remanejamento entre Lojas — Origem: {unidade}")
            st.markdown("Selecione o produto (bipando ou pela lista) e o sistema já sugere o lote pela lógica **FEFO** (*First-Expired, First-Out*): o que vence primeiro deve sair primeiro.")

            df_estoque_rem = carregar_estoque_atual()
            df_origem = df_estoque_rem[df_estoque_rem["Loja/CD Atual"] == unidade].copy() if not df_estoque_rem.empty else pd.DataFrame()

            if df_origem.empty:
                st.info(f"Nenhum produto em estoque em **{unidade}** para remanejar.")
            else:
                col_bip_s, col_sel_s = st.columns(2)
                with col_bip_s:
                    cod_bip_saida = st.text_input(
                        ":material/qr_code_scanner: Bipar ou digitar Código de Barras:",
                        key=f"leitor_remanejo_{st.session_state['bip_saida_key_idx']}",
                        placeholder="Passe o leitor aqui..."
                    )

                produtos_unicos = df_origem.groupby(["Código de Barras", "Produto", "Marca"])["Quantidade"].sum().reset_index()
                opcoes_selecao = ["Selecione um produto..."] + [
                    f"{r['Produto']} - {r['Marca']} | Saldo: {r['Quantidade']} un (Cód: {r['Código de Barras']})"
                    for _, r in produtos_unicos.iterrows()
                ]
                with col_sel_s:
                    prod_manual = st.selectbox("Ou selecione da lista:", options=opcoes_selecao, key="remanejo_sel_manual")

                cod_alvo = None
                if cod_bip_saida:
                    cod_alvo = cod_bip_saida.strip()
                elif prod_manual != "Selecione um produto...":
                    cod_alvo = prod_manual.split("(Cód: ")[-1].replace(")", "").strip()

                if cod_alvo:
                    lotes_produto = df_origem[df_origem["Código de Barras"] == cod_alvo].sort_values(by="Dias para Vencer", na_position="last")

                    if lotes_produto.empty:
                        st.error(f"Não há saldo disponível para o código **{cod_alvo}** em **{unidade}**.", icon=":material/error:")
                    else:
                        primeiro_lote = lotes_produto.iloc[0]
                        st.divider()
                        st.markdown(f"### :material/inventory_2: Produto: **{primeiro_lote['Produto']}** ({primeiro_lote['Marca']})")
                        st.caption(f"Código de Barras: `{cod_alvo}` | Saldo Total em {unidade}: **{lotes_produto['Quantidade'].sum()} un**")

                        st.success(
                            f"**Sugestão FEFO:** despachar o lote com validade em **{primeiro_lote['Validade']}** "
                            f"({primeiro_lote['Dias para Vencer']} dias | {primeiro_lote['Status']} | saldo: **{primeiro_lote['Quantidade']} un**)",
                            icon=":material/track_changes:"
                        )

                        st.markdown("##### :material/list_alt: Lotes Disponíveis (Ordenados por Vencimento):")
                        st.dataframe(lotes_produto[["Status", "Lote", "Validade", "Dias para Vencer", "Quantidade"]], use_container_width=True, hide_index=True)

                        opcoes_lotes = {
                            f"Lote {r['Lote']} | Validade {r['Validade']} ({r['Dias para Vencer']} dias) — {r['Quantidade']} un": r
                            for _, r in lotes_produto.iterrows()
                        }
                        lote_rotulo = st.selectbox("Lote selecionado (pré-escolhido pelo FEFO):", list(opcoes_lotes.keys()), index=0, key="remanejo_lote_sel")
                        lote_sel = opcoes_lotes[lote_rotulo]

                        sub_remanejo, sub_perda = st.tabs([":material/sync_alt: Remanejar para Outra Loja", ":material/delete: Baixa por Perda (Descarte)"])

                        with sub_remanejo:
                            with st.form("form_remanejamento", clear_on_submit=True):
                                c_dest, c_qtd = st.columns(2)
                                lojas_destino_opcoes = obter_lojas_destino(lista_lojas, unidade)
                                with c_dest:
                                    loja_destino_sel = st.selectbox("Loja de Destino:", lojas_destino_opcoes)
                                with c_qtd:
                                    qtd_remanejar = st.number_input("Quantidade a Remanejar:", min_value=1, max_value=int(lote_sel["Quantidade"]), value=int(lote_sel["Quantidade"]))

                                obs_remanejo = st.text_input("Observações / Guia de Transferência", placeholder="Ex: NF 9988 - Transferência para reposição de vitrine")

                                if st.form_submit_button("Confirmar Remanejamento", type="primary", icon=":material/sync_alt:"):
                                    loja_destino_id = obter_ou_criar_loja(loja_destino_sel)
                                    if registrar_remanejamento(
                                        lote_id=lote_sel["lote_id"],
                                        produto_id=lote_sel["produto_id"],
                                        lote_txt=lote_sel["lote_raw"],
                                        validade_iso=lote_sel["validade_iso"],
                                        loja_origem_id=lote_sel["loja_id"],
                                        loja_destino_id=loja_destino_id,
                                        quantidade=qtd_remanejar,
                                        usuario=usuario_atual,
                                        observacoes=obs_remanejo
                                    ):
                                        st.session_state['bip_saida_key_idx'] += 1
                                        st.rerun()

                        with sub_perda:
                            with st.form("form_baixa_perda", clear_on_submit=True):
                                c_b1, c_b2, c_b3 = st.columns(3)
                                qtd_baixa = c_b1.number_input("Qtd a Descartar", min_value=1, max_value=int(lote_sel["Quantidade"]), value=int(lote_sel["Quantidade"]))
                                custo_unit = c_b2.number_input("Custo Unitário (R$)", min_value=0.0, value=0.0, step=0.50, format="%.2f")
                                motivo = c_b3.selectbox("Motivo do Descarte", ["Vencimento", "Avaria no Transporte / Estoque", "Embalagem Danificada", "Contaminação / Mofo", "Outros"])

                                obs_baixa = st.text_input("Observações do Descarte", placeholder="Ex: Produto venceu na prateleira sem giro.")

                                prejuizo_previsto = round(float(qtd_baixa) * float(custo_unit), 2)
                                if prejuizo_previsto > 0:
                                    st.info(f"**Prejuízo Financeiro Estimado:** R$ {prejuizo_previsto:.2f}", icon=":material/payments:")

                                if st.form_submit_button("Confirmar Baixa por Perda", type="primary", icon=":material/delete:"):
                                    if dar_baixa_perda(
                                        lote_id=lote_sel["lote_id"],
                                        loja_id=lote_sel["loja_id"],
                                        quantidade=qtd_baixa,
                                        custo_unit=custo_unit,
                                        motivo=motivo,
                                        observacoes=obs_baixa,
                                        usuario=usuario_atual
                                    ):
                                        st.rerun()

    # ==================================================================
    # ABA: PLANILHA COMPARTILHADA — compra_venda e admin
    # ==================================================================
    if "planilha" in abas:
        with abas["planilha"]:
            st.subheader(":material/table_chart: Planilha Compartilhada de Estoque")
            st.caption("Visão consolidada de todos os lotes já aprovados, ordenada por validade (FEFO). Atualiza a cada interação na página — use o botão abaixo para forçar uma releitura do Supabase.")

            if st.button("Atualizar Agora", icon=":material/refresh:"):
                st.rerun()

            df_planilha = carregar_estoque_atual()

            if df_planilha.empty:
                st.info("Nenhum lote aprovado em estoque no momento.")
            else:
                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    loja_filtro_planilha = st.selectbox("Filtrar por Loja/CD:", options=["Todas"] + lista_lojas, key="planilha_filtro_loja")
                with col_f2:
                    nome_filtro_planilha = st.text_input("Filtrar por Nome do Produto:", placeholder="Digite parte do nome...", key="planilha_filtro_nome")

                df_exibir = df_planilha.copy()
                if loja_filtro_planilha != "Todas":
                    df_exibir = df_exibir[df_exibir["Loja/CD Atual"] == loja_filtro_planilha]
                if nome_filtro_planilha:
                    df_exibir = df_exibir[df_exibir["Produto"].str.contains(nome_filtro_planilha, case=False, na=False)]

                c_m1, c_m2, c_m3 = st.columns(3)
                c_m1.metric(":material/cancel: Vencidos", int((df_exibir["Status"] == "❌ Vencido").sum()))
                c_m2.metric(":material/priority_high: Críticos (<=30 dias)", int(df_exibir["Status"].str.startswith("🔴").sum()))
                c_m3.metric(":material/inventory_2: Total de Lotes", len(df_exibir))

                st.dataframe(df_exibir[COLUNAS_PLANILHA], use_container_width=True, hide_index=True)

    # ==================================================================
    # ABA: PENDÊNCIAS DE APROVAÇÃO — compra_venda e admin
    # ==================================================================
    if "pendencias" in abas:
        with abas["pendencias"]:
            st.subheader(":material/pending_actions: Pendências de Aprovação")
            st.caption("Entradas lançadas pelo perfil CD aguardando aprovação. Só depois de aprovadas elas contam como estoque disponível na Planilha Compartilhada.")

            df_pendentes = carregar_lotes_pendentes()
            if df_pendentes.empty:
                st.info("Nenhuma entrada pendente de aprovação no momento.")
            else:
                for _, item in df_pendentes.iterrows():
                    with st.container(border=True):
                        st.markdown(f"**{item['Produto']}** ({item['Marca']}) — Lote: {item['Lote']} | Validade: {item['Validade']}")
                        st.caption(f"Quantidade: **{item['Quantidade']} un** | Destino: **{item['Loja/CD Destino']}** | Lançado por **{item['Lançado Por']}** em {item['Lançado Em']}")

                        c_aprovar, c_rejeitar = st.columns(2)
                        with c_aprovar:
                            if st.button("Aprovar", key=f"aprovar_{item['lote_id']}", type="primary", icon=":material/check_circle:"):
                                if aprovar_entrada_pendente(item['lote_id'], usuario_atual):
                                    st.rerun()
                        with c_rejeitar:
                            with st.popover("Rejeitar", icon=":material/cancel:"):
                                motivo_rejeicao = st.text_input("Motivo da rejeição:", key=f"motivo_{item['lote_id']}", placeholder="Ex: quantidade errada, validade ilegível...")
                                if st.button("Confirmar Rejeição", key=f"confirmar_rejeitar_{item['lote_id']}", type="primary", icon=":material/cancel:"):
                                    if rejeitar_entrada_pendente(item['lote_id'], motivo_rejeicao, usuario_atual):
                                        st.rerun()

    # ==================================================================
    # ABA: HISTÓRICO DE MOVIMENTAÇÕES (entradas + remanejamentos) — compra_venda e admin
    # ==================================================================
    if "movimentacoes" in abas:
        with abas["movimentacoes"]:
            st.subheader(":material/history: Histórico de Movimentações (Entradas & Remanejamentos)")

            c_t1, c_t2 = st.columns(2)
            with c_t1:
                tipo_filtro_label = st.selectbox("Tipo de Movimentação:", options=["Todas", "Entrada", "Remanejamento"], key="mov_filtro_tipo")
            with c_t2:
                loja_filtro_mov = st.selectbox("Filtrar por Loja/CD (origem ou destino):", options=["Todas"] + lista_lojas, key="mov_filtro_loja")

            tipo_filtro = {"Todas": None, "Entrada": "entrada", "Remanejamento": "remanejamento"}[tipo_filtro_label]
            df_mov = carregar_historico_movimentacoes(tipo_filtro=tipo_filtro, loja_filtro=loja_filtro_mov)

            if not df_mov.empty:
                c_e1, c_e2 = st.columns(2)
                c_e1.metric(":material/inventory_2: Unidades Movimentadas", int(df_mov["Quantidade"].sum()))
                c_e2.metric(":material/list_alt: Total de Registros", len(df_mov))

                st.divider()
                st.dataframe(df_mov, use_container_width=True, hide_index=True)

                csv_mov = df_mov.to_csv(index=False).encode('utf-8')
                st.download_button("Baixar Histórico (CSV)", data=csv_mov, file_name="historico_movimentacoes.csv", mime="text/csv", icon=":material/download:")
            else:
                st.info("Nenhuma movimentação registrada com esses filtros.")

    # ==================================================================
    # ABA: LINHA DO TEMPO DO LOTE (rastreabilidade / auditoria) — compra_venda e admin
    # ==================================================================
    if "timeline" in abas:
        with abas["timeline"]:
            st.subheader(":material/timeline: Linha do Tempo do Lote")
            st.caption("Selecione um lote — mesmo já totalmente remanejado, baixado ou ainda pendente — para ver todo o histórico: quando entrou, para quais lojas foi remanejado e as quantidades em cada etapa.")

            df_todos_lotes = carregar_todos_lotes()

            if df_todos_lotes.empty:
                st.info("Nenhum lote cadastrado ainda.")
            else:
                filtro_nome_timeline = st.text_input("Buscar por nome do produto:", placeholder="Digite parte do nome...", key="timeline_filtro_nome")

                df_opcoes_timeline = df_todos_lotes
                if filtro_nome_timeline:
                    df_opcoes_timeline = df_opcoes_timeline[df_opcoes_timeline["Produto"].str.contains(filtro_nome_timeline, case=False, na=False)]

                if df_opcoes_timeline.empty:
                    st.warning("Nenhum lote encontrado com esse filtro.", icon=":material/warning:")
                else:
                    df_opcoes_timeline = df_opcoes_timeline.sort_values(by=["Produto", "Validade"])
                    opcoes_lote = {
                        f"{r['Produto']} - {r['Marca']} | Lote {r['Lote']} | Validade {r['Validade']} | {r['Situação']} | Atualmente: {r['Quantidade Atual']} un em {r['Loja/CD Atual']}": r
                        for _, r in df_opcoes_timeline.iterrows()
                    }
                    rotulo_escolhido = st.selectbox("Selecione o lote:", list(opcoes_lote.keys()), key="timeline_lote_sel")
                    lote_escolhido = opcoes_lote[rotulo_escolhido]

                    st.divider()
                    st.markdown(f"### :material/inventory_2: {lote_escolhido['Produto']} ({lote_escolhido['Marca']})")
                    st.caption(
                        f"Código: `{lote_escolhido['Código de Barras']}` | Lote: **{lote_escolhido['Lote']}** | "
                        f"Validade: **{lote_escolhido['Validade']}** | Situação: **{lote_escolhido['Situação']}** | "
                        f"Saldo atual: **{lote_escolhido['Quantidade Atual']} un** em **{lote_escolhido['Loja/CD Atual']}**"
                    )

                    df_timeline = carregar_timeline_lote(lote_escolhido["produto_id"], lote_escolhido["lote_raw"], lote_escolhido["validade_iso"])

                    if df_timeline.empty:
                        st.info("Nenhuma movimentação registrada para este lote ainda (pode estar pendente de aprovação).")
                    else:
                        st.markdown("##### :material/history: Linha do Tempo")
                        for _, evento in df_timeline.iterrows():
                            obs_sufixo = f" — _{evento['Observações']}_" if evento["Observações"] else ""
                            responsavel = evento["Responsável"] or "-"
                            if evento["Tipo"].endswith("Entrada"):
                                st.markdown(f"- :material/move_to_inbox: **{evento['Data/Hora']}** — Entrada de **{evento['Quantidade']} un** em **{evento['Destino']}** (responsável: {responsavel}){obs_sufixo}")
                            elif evento["Tipo"].endswith("Remanejamento"):
                                st.markdown(f"- :material/sync_alt: **{evento['Data/Hora']}** — Remanejamento de **{evento['Origem']}** para **{evento['Destino']}**: **{evento['Quantidade']} un** (responsável: {responsavel}){obs_sufixo}")
                            else:
                                st.markdown(f"- :material/delete: **{evento['Data/Hora']}** — Baixa por perda em **{evento['Origem']}**: **{evento['Quantidade']} un** (responsável: {responsavel}){obs_sufixo}")

                        st.divider()
                        st.markdown("##### :material/table_chart: Tabela Detalhada")
                        st.dataframe(df_timeline, use_container_width=True, hide_index=True)

                        csv_timeline = df_timeline.to_csv(index=False).encode('utf-8')
                        st.download_button("Baixar Linha do Tempo (CSV)", data=csv_timeline, file_name="linha_tempo_lote.csv", mime="text/csv", icon=":material/download:")

    # ==================================================================
    # ABA: HISTÓRICO DE BAIXAS & PERDAS — compra_venda e admin
    # ==================================================================
    if "perdas" in abas:
        with abas["perdas"]:
            st.subheader(f":material/delete: Histórico de Baixas & Perdas Financeiras: {unidade}")
            df_perdas = carregar_historico_perdas(unidade)

            if not df_perdas.empty:
                total_itens_perdidos = df_perdas["Qtd Descartada"].sum()
                total_prejuizo = df_perdas["Prejuízo (R$)"].sum()
                total_ocorrencias = len(df_perdas)

                c_p1, c_p2, c_p3 = st.columns(3)
                c_p1.metric(":material/inventory_2: Itens Descartados", f"{total_itens_perdidos} un")
                c_p2.metric(":material/payments: Prejuízo Acumulado", f"R$ {total_prejuizo:,.2f}")
                c_p3.metric(":material/list_alt: Total de Baixas", total_ocorrencias)

                st.divider()

                with st.expander("Dashboard & Análise Visual de Perdas (Para Negociação com Fornecedores)", expanded=True, icon=":material/bar_chart:"):
                    st.markdown("#### :material/trending_down: Indicadores Gráficos de Perdas")

                    gp1, gp2 = st.columns(2)
                    with gp1:
                        st.markdown("**:material/payments: Prejuízo Total (R$) por Marca / Fornecedor**")
                        prejuizo_marca = df_perdas.groupby("Marca")["Prejuízo (R$)"].sum().sort_values(ascending=False).head(10)
                        st.bar_chart(prejuizo_marca, use_container_width=True)

                    with gp2:
                        st.markdown("**:material/inventory_2: Quantidade de Itens Descartados por Marca**")
                        qtd_marca = df_perdas.groupby("Marca")["Qtd Descartada"].sum().sort_values(ascending=False).head(10)
                        st.bar_chart(qtd_marca, use_container_width=True)

                    st.markdown("---")
                    gp3, gp4 = st.columns(2)
                    with gp3:
                        st.markdown("**:material/sell: Prejuízo (R$) por Categoria de Suplemento**")
                        prejuizo_cat = df_perdas.groupby("Categoria")["Prejuízo (R$)"].sum().sort_values(ascending=False)
                        st.bar_chart(prejuizo_cat, use_container_width=True)

                    with gp4:
                        st.markdown("**:material/warning: Motivo dos Descartes**")
                        contagem_motivo = df_perdas["Motivo"].value_counts()
                        st.bar_chart(contagem_motivo, use_container_width=True)

                st.divider()
                st.subheader(":material/list_alt: Detalhamento dos Registros de Perdas")
                st.dataframe(df_perdas, use_container_width=True, hide_index=True)

                csv_dados = df_perdas.to_csv(index=False).encode('utf-8')
                st.download_button(
                    "Baixar Relatório de Perdas (CSV)",
                    data=csv_dados,
                    file_name=f"relatorio_perdas_{unidade.replace(' ', '_')}.csv",
                    mime="text/csv",
                    icon=":material/download:"
                )
            else:
                st.info("Nenhuma baixa por perda registrada para esta loja/CD.")

                with st.expander("Configuração do Banco de Dados (Tabela 'perdas')", expanded=False, icon=":material/info:"):
                    st.markdown("""
                    Se a tabela `perdas` ainda não existir (ou estiver no schema antigo), execute o SQL do arquivo
                    `schema_lote_validade.sql` no **SQL Editor** do Supabase — ele cria/ajusta `perdas` para
                    referenciar `lojas` e `lotes` no novo schema.
                    """)

    # ==================================================================
    # ABA: GESTÃO DE USUÁRIOS — só admin
    # ==================================================================
    if "usuarios" in abas:
        with abas["usuarios"]:
            st.subheader(":material/manage_accounts: Gestão de Usuários")
            st.caption("Defina o perfil e libere o acesso de cada conta. A conta de login (email/senha) continua sendo criada manualmente no painel do Supabase — aqui você só define o que cada pessoa pode fazer no sistema.")

            df_usuarios = carregar_usuarios()
            if df_usuarios.empty:
                st.info("Nenhum usuário fez login no sistema ainda.")
            else:
                st.dataframe(df_usuarios.drop(columns=["id"]), use_container_width=True, hide_index=True)

                st.divider()
                st.markdown("##### :material/edit: Editar Perfil de um Usuário")
                opcoes_usuarios = {f"{row['Apelido']} — {row['Perfil']}": row for _, row in df_usuarios.iterrows()}
                rotulo_usuario_sel = st.selectbox("Selecione o usuário:", list(opcoes_usuarios.keys()), key="usuarios_sel")
                usuario_sel = opcoes_usuarios[rotulo_usuario_sel]

                with st.form("form_editar_usuario"):
                    perfil_atual = usuario_sel["Perfil"] if usuario_sel["Perfil"] != "(sem perfil)" else None
                    opcoes_perfil = ["cd", "compra_venda", "admin"]
                    idx_perfil = opcoes_perfil.index(perfil_atual) if perfil_atual in opcoes_perfil else 0
                    novo_perfil = st.selectbox(
                        "Perfil:", opcoes_perfil, index=idx_perfil,
                        format_func=lambda p: {
                            "cd": "CD (só recebimento, fica pendente)",
                            "compra_venda": "Compra e Venda (acesso total + aprova/rejeita)",
                            "admin": "Coordenador/Admin (acesso total + gestão de usuários)"
                        }.get(p, p)
                    )
                    novo_ativo = st.checkbox("Conta ativa (liberada para acessar o sistema)", value=(usuario_sel["Ativo"] == "✅ Sim"))

                    if st.form_submit_button("Salvar", type="primary", icon=":material/save:"):
                        if atualizar_perfil_usuario(usuario_sel["id"], novo_perfil, novo_ativo):
                            st.rerun()

# --- 8. MOTOR DO APLICATIVO ---
if st.session_state['autenticado']:
    tela_principal()
else:
    tela_login()
