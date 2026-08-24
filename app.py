import pandas as pd
import streamlit as st
from supabase import create_client, Client
from datetime import datetime, date
import urllib.parse 

# --- 1. CADASTRE A EQUIPE DE COMPRAS E VENDAS AQUI ---
CONTATOS_COMPRA_VENDA = {
    "João (Compras e Vendas)": "5521988918455",
    "Maria (Gestão)": "5521988888888",
    "Equipe CD": "552133334444"
}

# --- 2. CONFIGURAÇÃO E CONEXÃO SEGURA ---
st.set_page_config(page_title="Way Suplementos", layout="wide", page_icon="💊")

try:
    URL = st.secrets["SUPABASE_URL"]
    KEY = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(URL, KEY)
except Exception as e:
    st.error("Erro crítico: Não foi possível conectar ao banco de dados.")
    st.stop()

# --- 3. CONTROLE DE LOGIN (SESSÃO) ---
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
if 'usuario' not in st.session_state:
    st.session_state['usuario'] = "Operador"
if 'bip_entrada_key_idx' not in st.session_state:
    st.session_state['bip_entrada_key_idx'] = 0
if 'bip_saida_key_idx' not in st.session_state:
    st.session_state['bip_saida_key_idx'] = 0

def realizar_login(apelido, senha):
    try:
        email_formatado = f"{apelido.lower().strip()}@way.com"
        supabase.auth.sign_in_with_password({"email": email_formatado, "password": senha})
        st.session_state['autenticado'] = True
        st.session_state['usuario'] = apelido.strip()
        st.rerun()
    except Exception as e:
        st.error("❌ Apelido ou senha incorretos! Tente novamente.")

def fazer_logout():
    supabase.auth.sign_out()
    st.session_state['autenticado'] = False
    st.session_state['usuario'] = "Operador"
    st.rerun()

# --- 4. VARIÁVEIS GERAIS ---
COLUNAS_TABELA_PRINCIPAL = ["Status Geral", "Nível de Estoque", "Código de Barras", "Nome", "Marca", "Quantidade Total", "Observações"]

# --- 5. FUNÇÕES DE BANCO DE DADOS ---
def classificar_validade(dias):
    if dias < 0: return "❌ Vencido"
    elif dias <= 180: return "🔴 Crítico (<=6 meses)"
    elif dias <= 270: return "🟡 Atenção (<=9 meses)"
    else: return "🟢 OK"

def classificar_nivel_estoque(qtd_total, limite_minimo):
    if qtd_total <= 3:
        return "🔴 Ruptura Crítica"
    elif qtd_total <= limite_minimo:
        return "🟡 Baixo Estoque"
    else:
        return "🟢 Normal"

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

def carregar_dados_brutos_e_agrupados():
    try:
        res = supabase.table("produtos").select("*, filiais(nome)").execute()
        if not res.data:
            return pd.DataFrame(), pd.DataFrame()
        
        raw_dados = []
        hoje = date.today()
        
        for p in res.data:
            try:
                dt_validade = datetime.strptime(p["validade"], "%Y-%m-%d").date()
                dias_para_vencer = (dt_validade - hoje).days
            except: 
                dt_validade = date.today()
                dias_para_vencer = 999 

            raw_dados.append({
                "id": p.get("id"),
                "filial_id": p.get("filial_id"),
                "Filial": p["filiais"]["nome"] if p.get("filiais") else "N/A",
                "codigo_barras": p["codigo_barras"],
                "nome": p["nome"],
                "marca": p["marca"],
                "dt_obj": dt_validade,
                "validade_str": dt_validade.strftime("%d/%m/%Y"),
                "dias": dias_para_vencer,
                "quantidade": int(p["quantidade"]),
                "observacoes": p.get("observacoes", "")
            })
        
        df_raw = pd.DataFrame(raw_dados)
        
        # Tabela agrupada para a visão limpa
        grupos = []
        for (filial, codigo), grupo in df_raw.groupby(["Filial", "codigo_barras"]):
            grupo_ordenado = grupo.sort_values(by="dt_obj")
            
            nome = grupo_ordenado.iloc[0]["nome"]
            marca = grupo_ordenado.iloc[0]["marca"]
            qtd_total = grupo_ordenado["quantidade"].sum()
            menor_dias = grupo_ordenado.iloc[0]["dias"]
            status_geral = classificar_validade(menor_dias)
            
            obs_lista = [o for o in grupo_ordenado["observacoes"].unique() if o]
            obs_final = " | ".join(obs_lista)
            
            grupos.append({
                "Filial": filial,
                "Status Geral": status_geral,
                "Menor Dias": menor_dias,
                "Código de Barras": codigo,
                "Nome": nome,
                "Marca": marca,
                "Quantidade Total": qtd_total,
                "Observações": obs_final
            })
            
        df_final = pd.DataFrame(grupos)
        if not df_final.empty:
            df_final = df_final.sort_values(by="Menor Dias")
            
        return df_final, df_raw
        
    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
        return pd.DataFrame(), pd.DataFrame()

def salvar_produto(unidade, cod, nome, marca, val, qtd, obs):
    try:
        res_f = supabase.table("filiais").select("id").eq("nome", unidade).execute()
        if len(res_f.data) == 0:
            res_nova = supabase.table("filiais").insert({"nome": unidade}).execute()
            f_id = res_nova.data[0]['id']
        else:
            f_id = res_f.data[0]['id']
        
        dados = {
            "filial_id": f_id,
            "codigo_barras": cod,
            "nome": nome,
            "marca": marca,
            "validade": str(val),
            "quantidade": qtd,
            "observacoes": obs
        }
        supabase.table("produtos").insert(dados).execute()
        st.success("✅ Produto salvo com sucesso!")
        return True
    
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")
        return False

def dar_baixa_perda(lote_id, filial_id, cod, nome, marca, validade_str, qtd_baixa, qtd_total_lote, custo_unit, motivo, obs, usuario):
    try:
        prejuizo_total = round(float(qtd_baixa) * float(custo_unit), 2)
        
        # 1. Registrar na tabela de perdas
        dado_perda = {
            "filial_id": filial_id,
            "codigo_barras": cod,
            "nome": nome,
            "marca": marca,
            "validade": validade_str,
            "quantidade": int(qtd_baixa),
            "custo_unitario": float(custo_unit),
            "prejuizo_total": prejuizo_total,
            "motivo": motivo,
            "observacoes": obs,
            "usuario": usuario
        }
        try:
            supabase.table("perdas").insert(dado_perda).execute()
        except Exception as e_perda:
            st.warning(f"⚠️ Nota: Tabela 'perdas' não foi gravada ({e_perda}). Verifique se a tabela 'perdas' foi criada no Supabase.")
        
        # 2. Atualizar ou remover da tabela de produtos
        if qtd_baixa >= qtd_total_lote:
            supabase.table("produtos").delete().eq("id", lote_id).execute()
        else:
            nova_qtd = qtd_total_lote - qtd_baixa
            supabase.table("produtos").update({"quantidade": nova_qtd}).eq("id", lote_id).execute()
            
        st.success(f"✅ Baixa de {qtd_baixa} un. de '{nome}' concluída com sucesso! Prejuízo registrado: R$ {prejuizo_total:.2f}")
        return True
    except Exception as e:
        st.error(f"Erro ao processar baixa por perda: {e}")
        return False

def carregar_historico_perdas(unidade=None):
    try:
        res = supabase.table("perdas").select("*, filiais(nome)").order("created_at", desc=True).execute()
        if not res.data:
            return pd.DataFrame()
        
        dados_perdas = []
        for item in res.data:
            filial_nome = item["filiais"]["nome"] if item.get("filiais") else "N/A"
            if unidade and unidade != "Todas" and filial_nome != unidade:
                continue
            
            created_at_dt = item.get("created_at", "")
            if created_at_dt:
                try:
                    dt_formatada = datetime.fromisoformat(created_at_dt.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
                except:
                    dt_formatada = str(created_at_dt)[:16]
            else:
                dt_formatada = "-"

            dados_perdas.append({
                "Data/Hora": dt_formatada,
                "Filial": filial_nome,
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

def registrar_envio_loja(lote_id, filial_origem_id, filial_destino, cod, nome, marca, validade_str, qtd_enviada, qtd_total_lote, obs, usuario):
    try:
        dado_envio = {
            "filial_origem_id": filial_origem_id,
            "filial_destino": filial_destino,
            "codigo_barras": cod,
            "nome": nome,
            "marca": marca,
            "validade": validade_str,
            "quantidade": int(qtd_enviada),
            "usuario": usuario,
            "observacoes": obs
        }
        try:
            supabase.table("envios_lojas").insert(dado_envio).execute()
        except Exception as e_envio:
            st.warning(f"⚠️ Nota: Tabela 'envios_lojas' não foi gravada ({e_envio}). Verifique se a tabela 'envios_lojas' foi criada no Supabase.")
        
        # Atualizar ou remover lote do CD (estoque de origem)
        if qtd_enviada >= qtd_total_lote:
            supabase.table("produtos").delete().eq("id", lote_id).execute()
        else:
            nova_qtd = qtd_total_lote - qtd_enviada
            supabase.table("produtos").update({"quantidade": nova_qtd}).eq("id", lote_id).execute()
            
        st.success(f"✅ Envio de {qtd_enviada} un. de '{nome}' para '{filial_destino}' registrado com sucesso!")
        return True
    except Exception as e:
        st.error(f"Erro ao processar envio para loja: {e}")
        return False

def carregar_historico_envios(unidade=None):
    try:
        res = supabase.table("envios_lojas").select("*, filiais(nome)").order("created_at", desc=True).execute()
        if not res.data:
            return pd.DataFrame()
        
        dados_envios = []
        for item in res.data:
            filial_origem = item["filiais"]["nome"] if item.get("filiais") else "Centro de Distribuição"
            if unidade and unidade != "Todas" and filial_origem != unidade:
                continue
            
            created_at_dt = item.get("created_at", "")
            if created_at_dt:
                try:
                    dt_formatada = datetime.fromisoformat(created_at_dt.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
                except:
                    dt_formatada = str(created_at_dt)[:16]
            else:
                dt_formatada = "-"

            dados_envios.append({
                "Data/Hora do Envio": dt_formatada,
                "Origem": filial_origem,
                "Loja de Destino": item.get("filial_destino", ""),
                "Código de Barras": item.get("codigo_barras", ""),
                "Nome do Produto": item.get("nome", ""),
                "Marca": item.get("marca", ""),
                "Validade do Lote": item.get("validade", ""),
                "Qtd Enviada": item.get("quantidade", 0),
                "Despachado Por": item.get("usuario", ""),
                "Observações / Guia": item.get("observacoes", "")
            })
            
        return pd.DataFrame(dados_envios)
    except Exception:
        return pd.DataFrame()

# --- 6. TELAS DO APLICATIVO ---
def tela_login():
    st.title("🔒 Way Suplementos - Acesso Restrito")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("form_login"):
            apelido = st.text_input("Apelido")
            senha = st.text_input("Senha", type="password")
            btn_login = st.form_submit_button("Entrar no Sistema")
            if btn_login: realizar_login(apelido, senha)

def tela_principal():
    st.title("📦 Way Suplementos - Gestão de Validades (CD)")

    try:
        res_f = supabase.table("filiais").select("nome").execute()
        lista_filiais = [f['nome'] for f in res_f.data] if res_f.data else ["Centro de Distribuição"]
    except:
        lista_filiais = ["Centro de Distribuição"]

    with st.sidebar:
        st.header("⚙️ Configurações")
        st.caption(f"👤 Usuário: **{st.session_state.get('usuario', 'Operador')}**")
        unidade = st.selectbox("Unidade Atual", options=lista_filiais)
        st.divider()
        st.subheader("📦 Parâmetros de Estoque")
        limite_minimo = st.number_input("Estoque Mínimo de Segurança (un):", min_value=1, max_value=500, value=10, step=1)
        st.divider()
        st.button("Sair (Logout)", on_click=fazer_logout, type="primary")

    aba_estoque, aba_saida, aba_envios, aba_perdas = st.tabs([
        "📦 Estoque & Validades", 
        "🚚 Saída & Despacho (FEFO)",
        "📋 Histórico de Envios (Lojas)",
        "🗑️ Histórico de Baixas & Perdas"
    ])

    with aba_estoque:
        df_agrupado, df_raw = carregar_dados_brutos_e_agrupados()
        df_filtrado = df_agrupado[df_agrupado["Filial"] == unidade].copy() if not df_agrupado.empty else df_agrupado.copy()

        if not df_filtrado.empty:
            df_filtrado["Nível de Estoque"] = df_filtrado["Quantidade Total"].apply(lambda q: classificar_nivel_estoque(q, limite_minimo))

        st.subheader("🚨 Painel de Alertas")
        
        if not df_filtrado.empty:
            df_criticos = df_filtrado[df_filtrado["Menor Dias"] <= 180]
            qtd_critico = len(df_criticos)
            qtd_atencao = len(df_filtrado[(df_filtrado["Menor Dias"] > 180) & (df_filtrado["Menor Dias"] <= 270)])
            df_ruptura = df_filtrado[df_filtrado["Quantidade Total"] <= limite_minimo]
            qtd_ruptura = len(df_ruptura)
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("🔴 Validade Crítica (<=6m)", qtd_critico)
            col2.metric("🟡 Validade Atenção (<=9m)", qtd_atencao)
            col3.metric("⚠️ Risco de Ruptura (<=limite)", qtd_ruptura)
            col4.metric("📦 Total de Produtos", len(df_filtrado))

            # --- AVISO DO CD PARA A EQUIPE DE COMPRAS E VENDAS (VALIDADE CURTA) ---
            if qtd_critico > 0:
                st.warning(f"⚠️ Há {qtd_critico} produto(s) com lotes críticos vencendo em menos de 6 meses no CD!")
                
                with st.expander("📢 Avisar Compras/Vendas para Envio às Lojas (Validade Curta)", expanded=False):
                    c_vend, c_prod = st.columns(2)
                    
                    contato_selecionado = c_vend.selectbox("Para quem deseja enviar o aviso?", list(CONTATOS_COMPRA_VENDA.keys()), key="contato_validade")
                    
                    opcoes_produtos = []
                    for _, row in df_criticos.iterrows():
                        # Soma apenas a quantidade dos lotes que estão críticos (<= 180 dias / 6 meses) para este produto
                        lotes_criticos_prod = df_raw[(df_raw["Filial"] == unidade) & (df_raw["codigo_barras"] == row["Código de Barras"]) & (df_raw["dias"] <= 180)]
                        qtd_critica_unidades = int(lotes_criticos_prod["quantidade"].sum())
                        
                        opcoes_produtos.append(f"{row['Nome']} - {row['Marca']} (Vencendo: {qtd_critica_unidades} un)")
                    
                    produto_selecionado = c_prod.selectbox("Qual produto precisa ser enviado às lojas?", opcoes_produtos, key="prod_validade_sel")
                    
                    numero_whats = CONTATOS_COMPRA_VENDA[contato_selecionado]
                    texto_msg = f"🚨 *ALERTA DO CD - VALIDADE CURTA*\nOlá!\n\nTemos o seguinte produto com lotes críticos aqui no Centro de Distribuição:\n📦 *{produto_selecionado}*\n\nPor favor, analisem para qual loja podemos enviar esses lotes para acelerar a venda!"
                    link_whatsapp = f"https://wa.me/{numero_whats}?text={urllib.parse.quote(texto_msg)}"
                    
                    st.link_button(f"📲 Enviar WhatsApp para {contato_selecionado}", link_whatsapp, type="primary")

            # --- AVISO DE RUPTURA / ESTOQUE BAIXO PARA COMPRAS (REPOSIÇÃO) ---
            if qtd_ruptura > 0:
                st.error(f"🚨 Atenção: Há {qtd_ruptura} produto(s) com estoque baixo/risco de ruptura no CD (<= {limite_minimo} un)!")
                
                with st.expander("🛒 Avisar Compras para Reposição com Fornecedor (WhatsApp)", expanded=False):
                    c_vend_r, c_prod_r = st.columns(2)
                    contato_compras = c_vend_r.selectbox("Contato do Setor de Compras:", list(CONTATOS_COMPRA_VENDA.keys()), key="contato_ruptura")
                    
                    opcoes_ruptura = []
                    for _, row in df_ruptura.iterrows():
                        opcoes_ruptura.append(f"{row['Nome']} - {row['Marca']} (Estoque Atual: {row['Quantidade Total']} un)")
                    
                    prod_ruptura_selecionado = c_prod_r.selectbox("Produto com Risco de Ruptura:", opcoes_ruptura, key="prod_ruptura_sel")
                    
                    num_whats_compras = CONTATOS_COMPRA_VENDA[contato_compras]
                    msg_ruptura = f"🚨 *ALERTA DE RUPTURA / ESTOQUE BAIXO*\nOlá!\n\nIdentificamos que o seguinte produto está com estoque crítico no CD:\n📦 *{prod_ruptura_selecionado}*\n⚙️ *Estoque Mínimo Definido:* {limite_minimo} un\n\nPor favor, providenciem um novo pedido junto ao fornecedor para evitar desabastecimento nas lojas!"
                    link_whats_ruptura = f"https://wa.me/{num_whats_compras}?text={urllib.parse.quote(msg_ruptura)}"
                    
                    st.link_button(f"📲 Solicitar Compra via WhatsApp para {contato_compras}", link_whats_ruptura, type="primary")

        # --- SEÇÃO: ENTRADA DE ESTOQUE & CADASTRO DE PRODUTOS ---
        with st.expander("📥 Lançamento de Estoque & Ficha de Produtos", expanded=False):
            sub_tab_bip, sub_tab_ficha = st.tabs([
                "⚡ Entrada Rápida por Bipagem (Lotes)",
                "📦 Ficha do Produto (Cadastro Novo)"
            ])
            
            # ABA 1: BIPAGEM RÁPIDA DE ENTRADA
            with sub_tab_bip:
                st.markdown("#### ⚡ Entrada de Mercadoria por Bipagem")
                st.info("👉 **Como usar o leitor:** Clique no campo abaixo, aponte o leitor de código de barras para o produto e bipe. O leitor digitará o código e dará Enter automaticamente.")
                
                codigo_bipado = st.text_input(
                    "Bipe o Código de Barras aqui:", 
                    key=f"leitor_bip_{st.session_state['bip_entrada_key_idx']}",
                    placeholder="Passe o leitor de código de barras..."
                )
                
                if codigo_bipado:
                    cod_limpo = codigo_bipado.strip()
                    try:
                        res_busca = supabase.table("produtos").select("nome, marca").eq("codigo_barras", cod_limpo).limit(1).execute()
                    except Exception as e_busca:
                        res_busca = None
                        st.error(f"Erro ao consultar banco: {e_busca}")
                    
                    if res_busca and res_busca.data:
                        nome_encontrado = res_busca.data[0]['nome']
                        marca_encontrada = res_busca.data[0].get('marca', '')
                        
                        st.success(f"✅ **Produto Reconhecido:** {nome_encontrado} | **Marca:** {marca_encontrada} *(Código: {cod_limpo})*")
                        
                        with st.form("form_entrada_bipada", clear_on_submit=True):
                            col_q, col_v = st.columns(2)
                            with col_q:
                                qtd_chegou = st.number_input("Quantidade que Chegou (Unidades)", min_value=1, value=1, step=1)
                            with col_v:
                                val_chegou = st.date_input("Data de Validade do Lote", value=date.today(), format="DD/MM/YYYY")
                            
                            obs_chegou = st.text_input("Observações / NF / Lote (Opcional)", placeholder="Ex: NF 12345 / Lote Fornecedor X")
                            
                            btn_confirmar_entrada = st.form_submit_button("📥 Confirmar Entrada no Estoque", type="primary")
                            
                            if btn_confirmar_entrada:
                                if salvar_produto(unidade, cod_limpo, nome_encontrado, marca_encontrada, val_chegou, qtd_chegou, obs_chegou):
                                    st.session_state['bip_entrada_key_idx'] += 1
                                    st.rerun()
                    else:
                        st.warning(f"⚠️ **Código de barras '{cod_limpo}' não encontrado no sistema!**\nVá na aba **'📦 Ficha do Produto (Cadastro Novo)'** acima para cadastrar a ficha deste produto pela primeira vez.")

            # ABA 2: FICHA DO PRODUTO (CADASTRO NOVO)
            with sub_tab_ficha:
                st.markdown("#### 📦 Ficha do Produto (1º Cadastro)")
                st.markdown("Cadastre aqui produtos que **nunca deram entrada** na Way Suplementos. O nome e marca ficarão salvos para as próximas bipagens.")
                
                with st.form("form_ficha_novo_produto", clear_on_submit=True):
                    col_f1, col_f2 = st.columns(2)
                    with col_f1:
                        cod_ficha = st.text_input("Código de Barras", placeholder="Ex: 7891234567890 (ou bipe aqui)")
                        nome_ficha = st.text_input("Nome Completo do Produto", placeholder="Ex: 100% Whey Gold Standard 900g Baunilha")
                    with col_f2:
                        marca_ficha = st.text_input("Marca / Fabricante", placeholder="Ex: Optimum Nutrition, Max Titanium, IntegralMedica...")
                        val_ficha = st.date_input("Validade do Primeiro Lote", value=date.today(), format="DD/MM/YYYY")
                    
                    col_f3, col_f4 = st.columns(2)
                    with col_f3:
                        qtd_ficha = st.number_input("Quantidade Inicial (Unidades)", min_value=1, value=1, step=1)
                    with col_f4:
                        obs_ficha = st.text_input("Observações / NF (Opcional)", placeholder="Ex: 1º Cadastro / NF 9876")
                    
                    btn_salvar_ficha = st.form_submit_button("💾 Salvar Ficha e Dar Entrada no Estoque", type="primary")
                    
                    if btn_salvar_ficha:
                        if cod_ficha and nome_ficha:
                            if salvar_produto(unidade, cod_ficha.strip(), nome_ficha.strip(), marca_ficha.strip(), val_ficha, qtd_ficha, obs_ficha):
                                st.rerun()
                        else:
                            st.warning("⚠️ Preencha pelo menos o **Código de Barras** e o **Nome do Produto**.")

        st.divider()
        st.subheader(f"📋 Estoque Consolidado: {unidade}")
        
        if not df_filtrado.empty:
            st.dataframe(df_filtrado[COLUNAS_TABELA_PRINCIPAL], use_container_width=True, hide_index=True)
            
            # --- ANÁLISE GRÁFICA SOB DEMANDA ---
            with st.expander("📊 Exibir Gráficos e Indicadores Visuais do Estoque", expanded=False):
                st.markdown("#### 📈 Visão Analítica do Estoque")
                
                g_col1, g_col2 = st.columns(2)
                with g_col1:
                    st.markdown("**Distribuição por Status de Validade**")
                    contagem_validade = df_filtrado["Status Geral"].value_counts()
                    st.bar_chart(contagem_validade, use_container_width=True)
                    
                with g_col2:
                    st.markdown("**Distribuição por Nível de Estoque (Ruptura)**")
                    contagem_estoque = df_filtrado["Nível de Estoque"].value_counts()
                    st.bar_chart(contagem_estoque, use_container_width=True)
                    
                st.markdown("---")
                g_col3, g_col4 = st.columns(2)
                with g_col3:
                    st.markdown("**Top 10 Produtos com Menor Estoque (Risco de Falta)**")
                    df_menor_estoque = df_filtrado.sort_values(by="Quantidade Total").head(10)[["Nome", "Quantidade Total"]]
                    st.bar_chart(df_menor_estoque.set_index("Nome"), use_container_width=True)
                    
                with g_col4:
                    st.markdown("**Volume Total por Marca (Top 10)**")
                    df_por_marca = df_filtrado.groupby("Marca")["Quantidade Total"].sum().sort_values(ascending=False).head(10)
                    st.bar_chart(df_por_marca, use_container_width=True)
            
            st.divider()
            
            # --- CONSULTA DETALHADA, DESPACHO E BAIXA POR PERDA ---
            with st.expander("🔍 Ver Detalhes, Lotes e Ações (Despacho para Loja / Baixa por Perda)"):
                lista_detalhe = df_filtrado["Nome"] + " (" + df_filtrado["Código de Barras"] + ")"
                escolha_detalhe = st.selectbox("Selecione o produto para gerenciar lotes:", lista_detalhe)
                
                if escolha_detalhe:
                    cod_escolhido = escolha_detalhe.split("(")[-1].replace(")", "").strip()
                    df_detalhes_prod = df_raw[(df_raw["Filial"] == unidade) & (df_raw["codigo_barras"] == cod_escolhido)]
                    df_detalhes_prod = df_detalhes_prod.sort_values(by="dt_obj")
                    
                    if not df_detalhes_prod.empty:
                        st.markdown(f"**Lotes cadastrados para:** {escolha_detalhe}")
                        
                        tabela_lotes = []
                        for _, r in df_detalhes_prod.iterrows():
                            status_lote = classificar_validade(r["dias"])
                            tabela_lotes.append({
                                "Status do Lote": status_lote,
                                "Validade": r["validade_str"],
                                "Dias para Vencer": r["dias"],
                                "Quantidade": r["quantidade"],
                                "Observações": r["observacoes"]
                            })
                        
                        st.dataframe(pd.DataFrame(tabela_lotes), use_container_width=True, hide_index=True)
                        
                        # --- SELEÇÃO DE LOTE PARA AÇÕES ---
                        st.markdown("---")
                        st.markdown("##### ⚙️ Ações para o Lote Selecionado")
                        
                        opcoes_lotes = {}
                        for _, r in df_detalhes_prod.iterrows():
                            rotulo = f"Validade: {r['validade_str']} | Qtd: {r['quantidade']} un | Status: {classificar_validade(r['dias'])}"
                            opcoes_lotes[rotulo] = r
                        
                        lote_selecionado_rotulo = st.selectbox("Selecione o Lote:", list(opcoes_lotes.keys()))
                        lote_info = opcoes_lotes[lote_selecionado_rotulo]
                        
                        sub_aba_envio, sub_aba_perda = st.tabs(["🚚 Despachar / Enviar para Loja", "🗑️ Baixa por Perda (Descarte)"])
                        
                        with sub_aba_envio:
                            with st.form("form_envio_loja", clear_on_submit=True):
                                c_e1, c_e2 = st.columns(2)
                                
                                lojas_destino_opcoes = [f for f in lista_filiais if f != unidade]
                                if not lojas_destino_opcoes:
                                    lojas_destino_opcoes = ["Loja Centro", "Loja Barra", "Loja Copacabana", "Loja Niterói", "Outra Loja"]
                                
                                destino_loja = c_e1.selectbox("Loja de Destino:", lojas_destino_opcoes)
                                qtd_envio = c_e2.number_input(
                                    "Qtd a Enviar",
                                    min_value=1,
                                    max_value=int(lote_info["quantidade"]),
                                    value=int(lote_info["quantidade"])
                                )
                                obs_envio = st.text_input("Observações / NF / Guia de Transferência", placeholder="Ex: Transferência para queima rápida de validade curta.")
                                
                                btn_confirmar_envio = st.form_submit_button("🚚 Confirmar Envio para Loja", type="primary")
                                
                                if btn_confirmar_envio:
                                    usuario_atual = st.session_state.get('usuario', 'Operador')
                                    if registrar_envio_loja(
                                        lote_id=lote_info["id"],
                                        filial_origem_id=lote_info["filial_id"],
                                        filial_destino=destino_loja,
                                        cod=lote_info["codigo_barras"],
                                        nome=lote_info["nome"],
                                        marca=lote_info["marca"],
                                        validade_str=str(lote_info["validade_str"]),
                                        qtd_enviada=qtd_envio,
                                        qtd_total_lote=lote_info["quantidade"],
                                        obs=obs_envio,
                                        usuario=usuario_atual
                                    ):
                                        st.rerun()
                        
                        with sub_aba_perda:
                            with st.form("form_baixa_perda", clear_on_submit=True):
                                c_b1, c_b2, c_b3 = st.columns(3)
                                qtd_baixa = c_b1.number_input(
                                    "Qtd a Descartar", 
                                    min_value=1, 
                                    max_value=int(lote_info["quantidade"]), 
                                    value=int(lote_info["quantidade"])
                                )
                                custo_unit = c_b2.number_input(
                                    "Custo Unitário (R$)", 
                                    min_value=0.0, 
                                    value=0.0, 
                                    step=0.50, 
                                    format="%.2f"
                                )
                                motivo = c_b3.selectbox(
                                    "Motivo do Descarte", 
                                    ["Vencimento", "Avaria no Transporte / Estoque", "Embalagem Danificada", "Contaminação / Mofo", "Outros"]
                                )
                                
                                obs_baixa = st.text_input("Observações do Descarte", placeholder="Ex: Produto venceu na prateleira sem giro.")
                                
                                prejuizo_previsto = round(float(qtd_baixa) * float(custo_unit), 2)
                                if prejuizo_previsto > 0:
                                    st.info(f"💰 **Prejuízo Financeiro Estimado:** R$ {prejuizo_previsto:.2f}")
                                
                                btn_confirmar_baixa = st.form_submit_button("🗑️ Confirmar Baixa por Perda", type="primary")
                                
                                if btn_confirmar_baixa:
                                    usuario_atual = st.session_state.get('usuario', 'Operador')
                                    if dar_baixa_perda(
                                        lote_id=lote_info["id"],
                                        filial_id=lote_info["filial_id"],
                                        cod=lote_info["codigo_barras"],
                                        nome=lote_info["nome"],
                                        marca=lote_info["marca"],
                                        validade_str=str(lote_info["validade_str"]),
                                        qtd_baixa=qtd_baixa,
                                        qtd_total_lote=lote_info["quantidade"],
                                        custo_unit=custo_unit,
                                        motivo=motivo,
                                        obs=obs_baixa,
                                        usuario=usuario_atual
                                    ):
                                        st.rerun()
        else:
            st.info("Nenhum produto encontrado nesta unidade.")

    with aba_saida:
        st.subheader(f"🚚 Saída & Despacho de Mercadorias para Lojas ({unidade})")
        st.markdown("Separe e despache produtos para as lojas filiais com **sugestão inteligente FEFO (*First-Expired, First-Out*)**, garantindo que os lotes que vencem primeiro saiam antes do CD.")
        
        df_agrupado_saida, df_raw_saida = carregar_dados_brutos_e_agrupados()
        df_estoque_unidade = df_raw_saida[df_raw_saida["Filial"] == unidade].copy() if not df_raw_saida.empty else pd.DataFrame()
        
        if df_estoque_unidade.empty:
            st.info(f"Nenhum produto em estoque na unidade **{unidade}** para realizar saídas.")
        else:
            col_bip_s, col_sel_s = st.columns(2)
            
            with col_bip_s:
                cod_saida_bipado = st.text_input(
                    "⚡ Bipar Código de Barras para Saída:",
                    key=f"leitor_bip_saida_{st.session_state['bip_saida_key_idx']}",
                    placeholder="Passe o leitor aqui..."
                )
            
            produtos_unicos = df_estoque_unidade.groupby(["codigo_barras", "nome", "marca"])["quantidade"].sum().reset_index()
            opcoes_selecao = ["Selecione um produto..."] + [
                f"{r['nome']} - {r['marca']} | Saldo Total: {r['quantidade']} un (Cód: {r['codigo_barras']})"
                for _, r in produtos_unicos.iterrows()
            ]
            
            with col_sel_s:
                prod_selecionado_manual = st.selectbox(
                    "Ou selecione o produto da lista:",
                    options=opcoes_selecao,
                    key="saida_sel_manual"
                )
            
            cod_final_saida = None
            if cod_saida_bipado:
                cod_final_saida = cod_saida_bipado.strip()
            elif prod_selecionado_manual != "Selecione um produto...":
                cod_final_saida = prod_selecionado_manual.split("(Cód: ")[-1].replace(")", "").strip()
            
            if cod_final_saida:
                lotes_do_produto = df_estoque_unidade[df_estoque_unidade["codigo_barras"] == cod_final_saida].copy()
                lotes_do_produto = lotes_do_produto.sort_values(by="dt_obj")
                
                if lotes_do_produto.empty:
                    st.error(f"⚠️ Não há lotes disponíveis em estoque com o código **{cod_final_saida}** na unidade **{unidade}**.")
                else:
                    primeiro_lote = lotes_do_produto.iloc[0]
                    nome_prod_saida = primeiro_lote["nome"]
                    marca_prod_saida = primeiro_lote["marca"]
                    saldo_total_prod = lotes_do_produto["quantidade"].sum()
                    
                    st.divider()
                    st.markdown(f"### 📦 Produto: **{nome_prod_saida}** ({marca_prod_saida})")
                    st.caption(f"Código de Barras: `{cod_final_saida}` | Saldo Total na Unidade: **{saldo_total_prod} un**")
                    
                    status_fefo = classificar_validade(primeiro_lote['dias'])
                    st.success(
                        f"🎯 **Sugestão Inteligente FEFO:** Recomendamos despachar o **Lote com Validade em {primeiro_lote['validade_str']}** "
                        f"({primeiro_lote['dias']} dias para vencer | Status: {status_fefo} | Saldo deste lote: **{primeiro_lote['quantidade']} un**)"
                    )
                    
                    st.markdown("##### 📋 Lotes Disponíveis em Estoque (Ordenados por Vencimento):")
                    tabela_lotes_view = []
                    for _, lr in lotes_do_produto.iterrows():
                        tabela_lotes_view.append({
                            "Status": classificar_validade(lr["dias"]),
                            "Validade": lr["validade_str"],
                            "Dias p/ Vencer": lr["dias"],
                            "Qtd Disponível": f"{lr['quantidade']} un",
                            "Observações": lr["observacoes"]
                        })
                    st.dataframe(pd.DataFrame(tabela_lotes_view), use_container_width=True, hide_index=True)
                    
                    with st.form("form_despacho_fefo", clear_on_submit=True):
                        st.markdown("#### 📝 Detalhes do Despacho para Loja")
                        
                        opcoes_lotes_dict = {}
                        for _, lr in lotes_do_produto.iterrows():
                            rotulo_lote = f"Validade: {lr['validade_str']} (Restam: {lr['dias']} dias) — Disponível: {lr['quantidade']} un"
                            opcoes_lotes_dict[rotulo_lote] = lr
                        
                        lote_escolhido_rotulo = st.selectbox(
                            "Qual lote deseja despachar? (Pré-selecionado o mais antigo pelo FEFO):",
                            options=list(opcoes_lotes_dict.keys()),
                            index=0
                        )
                        lote_alvo = opcoes_lotes_dict[lote_escolhido_rotulo]
                        
                        c_dest, c_qtd = st.columns(2)
                        
                        lojas_destino_opcoes = [f for f in lista_filiais if f != unidade]
                        if not lojas_destino_opcoes:
                            lojas_destino_opcoes = ["Loja Centro", "Loja Barra", "Loja Copacabana", "Loja Niterói", "Outra Loja"]
                        
                        with c_dest:
                            loja_destino_sel = st.selectbox("Loja de Destino (Filial):", lojas_destino_opcoes)
                        with c_qtd:
                            qtd_despachar = st.number_input(
                                "Quantidade a Despachar (Unidades):",
                                min_value=1,
                                max_value=int(lote_alvo["quantidade"]),
                                value=min(int(lote_alvo["quantidade"]), 1),
                                step=1
                            )
                        
                        guia_obs = st.text_input(
                            "Número do Romaneio / NF de Transferência / Observações:",
                            placeholder="Ex: NF 9988 - Transferência para reposição de vitrine"
                        )
                        
                        btn_despachar = st.form_submit_button("🚚 Confirmar Saída e Despacho para Loja", type="primary")
                        
                        if btn_despachar:
                            usuario_atual = st.session_state.get('usuario', 'Operador')
                            if registrar_envio_loja(
                                lote_id=lote_alvo["id"],
                                filial_origem_id=lote_alvo["filial_id"],
                                filial_destino=loja_destino_sel,
                                cod=lote_alvo["codigo_barras"],
                                nome=lote_alvo["nome"],
                                marca=lote_alvo["marca"],
                                validade_str=str(lote_alvo["validade_str"]),
                                qtd_enviada=qtd_despachar,
                                qtd_total_lote=lote_alvo["quantidade"],
                                obs=guia_obs,
                                usuario=usuario_atual
                            ):
                                st.session_state['bip_saida_key_idx'] += 1
                                st.rerun()

    with aba_perdas:
        st.subheader(f"🗑️ Histórico de Baixas & Perdas Financeiras: {unidade}")
        df_perdas = carregar_historico_perdas(unidade)
        
        if not df_perdas.empty:
            total_itens_perdidos = df_perdas["Qtd Descartada"].sum()
            total_prejuizo = df_perdas["Prejuízo (R$)"].sum()
            total_ocorrencias = len(df_perdas)
            
            c_p1, c_p2, c_p3 = st.columns(3)
            c_p1.metric("📦 Itens Descartados", f"{total_itens_perdidos} un")
            c_p2.metric("💸 Prejuízo Acumulado", f"R$ {total_prejuizo:,.2f}")
            c_p3.metric("📋 Total de Baixas", total_ocorrencias)
            
            st.divider()
            
            # --- DASHBOARD VISUAL DE PERDAS ---
            with st.expander("📊 Dashboard & Análise Visual de Perdas (Para Negociação com Fornecedores)", expanded=True):
                st.markdown("#### 📉 Indicadores Gráficos de Perdas")
                
                gp1, gp2 = st.columns(2)
                with gp1:
                    st.markdown("**💸 Prejuízo Total (R$) por Marca / Fornecedor**")
                    prejuizo_marca = df_perdas.groupby("Marca")["Prejuízo (R$)"].sum().sort_values(ascending=False).head(10)
                    st.bar_chart(prejuizo_marca, use_container_width=True)
                    
                with gp2:
                    st.markdown("**📦 Quantidade de Itens Descartados por Marca**")
                    qtd_marca = df_perdas.groupby("Marca")["Qtd Descartada"].sum().sort_values(ascending=False).head(10)
                    st.bar_chart(qtd_marca, use_container_width=True)
                    
                st.markdown("---")
                gp3, gp4 = st.columns(2)
                with gp3:
                    st.markdown("**🏷️ Prejuízo (R$) por Categoria de Suplemento**")
                    prejuizo_cat = df_perdas.groupby("Categoria")["Prejuízo (R$)"].sum().sort_values(ascending=False)
                    st.bar_chart(prejuizo_cat, use_container_width=True)
                    
                with gp4:
                    st.markdown("**⚠️ Motivo dos Descartes**")
                    contagem_motivo = df_perdas["Motivo"].value_counts()
                    st.bar_chart(contagem_motivo, use_container_width=True)
            
            st.divider()
            st.subheader("📋 Detalhamento dos Registros de Perdas")
            st.dataframe(df_perdas, use_container_width=True, hide_index=True)
            
            # Exportação CSV
            csv_dados = df_perdas.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 Baixar Relatório de Perdas (CSV)",
                data=csv_dados,
                file_name=f"relatorio_perdas_{unidade.replace(' ', '_')}.csv",
                mime="text/csv"
            )
        else:
            st.info("Nenhuma baixa por perda registrada para esta unidade.")
            
            with st.expander("ℹ️ Configuração do Banco de Dados (Tabela 'perdas')", expanded=False):
                st.markdown("""
                Se você ainda não criou a tabela `perdas` no Supabase, execute o comando abaixo no **SQL Editor** do Supabase:
                ```sql
                CREATE TABLE IF NOT EXISTS perdas (
                    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
                    filial_id BIGINT REFERENCES filiais(id),
                    codigo_barras TEXT,
                    nome TEXT,
                    marca TEXT,
                    validade TEXT,
                    quantidade INT,
                    custo_unitario NUMERIC(10, 2) DEFAULT 0.00,
                    prejuizo_total NUMERIC(10, 2) DEFAULT 0.00,
                    motivo TEXT DEFAULT 'Vencimento',
                    observacoes TEXT,
                    usuario TEXT
                );
                ```
                """)

    with aba_envios:
        st.subheader(f"🚚 Histórico de Lotes Enviados para as Lojas: {unidade}")
        df_envios = carregar_historico_envios(unidade)
        
        if not df_envios.empty:
            total_itens_enviados = df_envios["Qtd Enviada"].sum()
            lojas_unicas = df_envios["Loja de Destino"].nunique()
            total_despachos = len(df_envios)
            
            c_e1, c_e2, c_e3 = st.columns(3)
            c_e1.metric("📦 Unidades Despachadas", f"{total_itens_enviados} un")
            c_e2.metric("🏬 Lojas Atendidas", lojas_unicas)
            c_e3.metric("📋 Total de Envios Registrados", total_despachos)
            
            st.divider()
            
            # Filtro por loja de destino
            lojas_no_historico = ["Todas"] + sorted(list(df_envios["Loja de Destino"].unique()))
            loja_filtro = st.selectbox("Filtrar por Loja de Destino:", options=lojas_no_historico)
            
            df_envios_exibir = df_envios if loja_filtro == "Todas" else df_envios[df_envios["Loja de Destino"] == loja_filtro]
            
            st.dataframe(df_envios_exibir, use_container_width=True, hide_index=True)
            
            # Exportação CSV
            csv_envios = df_envios_exibir.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 Baixar Relatório de Envios (CSV)",
                data=csv_envios,
                file_name=f"relatorio_envios_lojas_{unidade.replace(' ', '_')}.csv",
                mime="text/csv"
            )
        else:
            st.info("Nenhum envio para lojas registrado para esta unidade.")
            
            with st.expander("ℹ️ Configuração do Banco de Dados (Tabela 'envios_lojas')", expanded=False):
                st.markdown("""
                Se você ainda não criou a tabela `envios_lojas` no Supabase, execute o comando abaixo no **SQL Editor** do Supabase:
                ```sql
                CREATE TABLE IF NOT EXISTS envios_lojas (
                    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
                    filial_origem_id BIGINT REFERENCES filiais(id),
                    filial_destino TEXT NOT NULL,
                    codigo_barras TEXT,
                    nome TEXT,
                    marca TEXT,
                    validade TEXT,
                    quantidade INT,
                    usuario TEXT,
                    observacoes TEXT
                );
                ```
                """)

# --- 7. MOTOR DO APLICATIVO ---
if st.session_state['autenticado']:
    tela_principal()
else:
    tela_login()