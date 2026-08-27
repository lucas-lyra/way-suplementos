# Way Suplementos — Django

Sistema de estoque (lote/validade + remanejamento + fluxo de aprovação) em
Django. Este é o sistema principal do projeto.

Usa o **Postgres do Supabase**, reaproveitando os dados de `produtos`, `lojas`,
`lotes` e `movimentacoes` de uma versão anterior deste projeto (feita em
Streamlit, já removida do repositório). Autenticação e perfis de acesso são do
próprio Django (`django.contrib.auth` + Grupos) — a tabela `usuarios` daquela
versão anterior (ligada ao Supabase Auth) ficou órfã no banco, sem uso.

## Perfis de acesso

| Grupo (perfil) | O que pode fazer |
|---|---|
| **CD** | Só a tela de Recebimento. Toda entrada nasce **pendente**. Sem acesso a remanejamento, rotas, aprovação, lojas ou usuários. |
| **Compra e Venda** | Acesso total: recebimento direto, remanejamento, rotas de entrega, aprova/rejeita (por item ou NF inteira), gestão de lojas e de usuários. |
| **Coordenador/Admin** | Exatamente o mesmo acesso de Compra e Venda hoje — os dois grupos só existem separados para refletir o cargo da pessoa, não uma diferença de permissão. |
| **Motorista** | Só a tela de Rotas de Entrega — vê as rotas atribuídas a ele e marca paradas como entregues. Sem acesso a mais nada. |

Regra central: nenhum lançamento do perfil **CD** conta como estoque disponível
até ser aprovado.

## Passo a passo para rodar localmente

### 1. Instalar dependências

```powershell
cd django_app
python -m pip install -r requirements.txt
```

(Já instalado nesta máquina durante o desenvolvimento — `pip install` de novo é
só para conferir/reinstalar se precisar.)

### 2. Configurar o `.env`

Já existe um `django_app/.env` criado a partir do `.env.example` (com uma
`DJANGO_SECRET_KEY` real já gerada). Falta só preencher `DATABASE_URL`.

Não é a URL/anon key da API REST do Supabase — aqui é conexão direta ao
Postgres. Para pegar a string certa:

1. Painel do Supabase → **Project Settings → Database → Connection string**.
2. Copie a URI (formato `postgresql://postgres:[SUA-SENHA]@...`).
   - Se aparecer opção de **Connection Pooling**, use o modo **Session**
     (porta `5432`), não o **Transaction** (porta `6543`) — o Django mantém
     conexões persistentes por request, o modo Transaction não é compatível
     com isso.
3. Cole no lugar de `DATABASE_URL=` no `.env`, substituindo `[SUA-SENHA]` pela
   senha real do banco (Supabase mostra a URI com esse placeholder — a senha
   de verdade é a que você definiu ao criar o projeto, ou você pode resetar em
   **Database → Settings**).

### 3. Rodar as migrations do Django

```powershell
python manage.py migrate
```

Isso cria as tabelas próprias do Django (`auth_user`, `django_session`, etc.) e
os 3 grupos de perfil (CD / Compra e Venda / Coordenador/Admin) — **não** mexe
em `produtos`/`lojas`/`lotes`/`movimentacoes`, que já existem.

### 4. Rodar o SQL adicional (depois do migrate, não antes)

Abra `sql_migracao_django.sql` no SQL Editor do Supabase e rode. Ele adiciona
3 colunas novas em `movimentacoes` (`status`, `motivo_rejeicao`,
`responsavel_user_id`) — a última é uma FK para `auth_user`, por isso precisa
rodar **depois** do passo 3.

Rode também `sql_migracao_rotas.sql` — adiciona a coluna `endereco` em `lojas`
(usada pelas Paradas de Rota). Pode rodar em qualquer ordem em relação ao
anterior, contanto que seja depois do `migrate`.

### 5. Popular dados de exemplo (opcional)

```powershell
python manage.py seed_demo
```

Cria 10 produtos fictícios de suplementos + lotes de exemplo com validades
variadas (vencido, crítico, ok) na loja "Centro de Distribuição" — útil pra
testar Remanejamento, Rotas e a Planilha sem digitar tudo na mão. Idempotente.

### 7. Rodar o servidor e criar o primeiro usuário (admin)

```powershell
python manage.py runserver
```

Acesse http://127.0.0.1:8000/cadastro/ e crie uma conta — como é o **primeiro
cadastro do sistema**, ela vira admin automaticamente (bootstrap; sem isso
ninguém teria perfil pra liberar os próximos). Alternativa via terminal:
`python manage.py createsuperuser` (aí precisa entrar no
[Django Admin](http://127.0.0.1:8000/admin/) → Groups e adicionar ao grupo
"Coordenador/Admin" manualmente).

Depois de logado como admin, crie as lojas em **Lojas** (menu superior) e
libere outras contas em **Usuários**.

## Deploy no Render

O `render.yaml` na raiz do repositório já descreve o serviço inteiro
(comando de build, comando de start, variáveis de ambiente). Passo a passo:

1. Certifique-se que o repositório está atualizado no GitHub (`git push`).
2. No painel do [Render](https://dashboard.render.com/) → **New → Blueprint**.
3. Conecte este repositório GitHub. O Render lê o `render.yaml` automaticamente
   e propõe criar o serviço `way-suplementos`.
4. Quando pedir a variável **DATABASE_URL** (marcada como secreta, não vem
   preenchida do `render.yaml`), cole a mesma connection string do Supabase que
   você usa no `.env` local (Session pooler, porta 5432 — ver passo 2 acima).
5. Clique em **Apply** / **Create**. O Render vai:
   - Instalar as dependências (`requirements.txt`)
   - Rodar `collectstatic` (arquivos estáticos do Django Admin)
   - Rodar `migrate` (cria as tabelas do Django no mesmo Postgres do Supabase —
     idempotente, seguro rodar de novo se você já rodou localmente)
   - Subir o servidor com `gunicorn`
6. Depois que o deploy terminar, rode `sql_migracao_django.sql` e
   `sql_migracao_rotas.sql` no Supabase se ainda não tiver rodado (mesma
   instrução do passo 4 acima) — isso não é automatizado pelo deploy.
7. Acesse a URL que o Render gerar (`https://way-suplementos-xxxx.onrender.com`)
   e vá em `/cadastro/` pra criar a primeira conta (vira admin automaticamente,
   igual no ambiente local).

**Sobre o plano gratuito do Render**: o serviço "dorme" depois de um tempo sem
acesso e demora ~30-50s pra acordar na próxima visita — normal, não é erro.

**Se preferir configurar manualmente** (sem Blueprint): crie um Web Service
apontando pra este repositório, defina **Root Directory** como `django_app`,
**Build Command** como `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`,
**Start Command** como `gunicorn waysuplementos.wsgi:application`, e configure
manualmente as mesmas variáveis de ambiente que estão no `render.yaml`.

## Estrutura

```
django_app/
├── manage.py
├── requirements.txt
├── .env.example              # copie para .env e preencha
├── sql_migracao_django.sql   # colunas novas em movimentacoes
├── sql_migracao_rotas.sql    # coluna endereco em lojas
├── waysuplementos/           # settings, urls, wsgi/asgi
└── estoque/                   # app principal
    ├── models.py              # Produto/Loja/Lote/Movimentacao — managed=False
                                # NotaFiscal/ItemNotaFiscal/NotaFiscalSaida/
                                # ParadaRota/ItemParada — managed=True (Django cria)
    ├── permissions.py          # controle de acesso por Group (perfil)
    ├── views.py
    ├── forms.py
    ├── admin.py
    ├── urls.py
    ├── migrations/
    ├── management/commands/
    │   └── seed_demo.py        # popula produtos/lotes fictícios (opcional)
    └── templates/estoque/      # Bootstrap 5 (via CDN), mobile-first
```

## Funcionalidades que a versão anterior (Streamlit) tinha e esta não tem

O briefing deste app Django não pediu, então não foi portado (dá pra adicionar
depois se quiser): baixa por perda/descarte, linha do tempo de auditoria por
lote, alertas por WhatsApp, dashboard de prejuízo financeiro.

## Fora de escopo (conforme briefing)

Este sistema **não** se integra com o sistema de emissão de notas fiscais já
existente — são sistemas independentes.
