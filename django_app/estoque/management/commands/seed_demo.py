from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from estoque.models import Loja, Lote, Movimentacao, Produto

User = get_user_model()

# (codigo_barras, nome, marca)
PRODUTOS_DEMO = [
    ("7891000100019", "Whey Protein Concentrado 900g - Baunilha", "Max Titanium"),
    ("7891000100026", "Whey Protein Isolado 900g - Chocolate", "Optimum Nutrition"),
    ("7891000100033", "Creatina Monohidratada 300g", "Growth Supplements"),
    ("7891000100040", "Pré-Treino Horus 300g", "Under Labz"),
    ("7891000100057", "BCAA 2:1:1 120 cápsulas", "Integralmedica"),
    ("7891000100064", "Glutamina 300g", "Growth Supplements"),
    ("7891000100071", "Hipercalórico Massa 3kg - Chocolate", "Max Titanium"),
    ("7891000100088", "Barra de Proteína Chocolate 90g", "Probiótica"),
    ("7891000100095", "Multivitamínico 60 cápsulas", "Vitafor"),
    ("7891000100101", "Ômega 3 120 cápsulas", "Vitafor"),
]

# (índice do produto em PRODUTOS_DEMO, quantidade, dias até vencer a partir de hoje)
# dias negativos = já vencido; cobre as faixas usadas na Planilha (vencido / ≤30 / ≤90 / ok)
LOTES_DEMO = [
    (0, 40, 200),   # Whey Concentrado — ok
    (0, 12, 20),    # Whey Concentrado — crítico (≤30 dias)
    (1, 25, 400),   # Whey Isolado — ok
    (2, 60, 15),    # Creatina — crítico
    (3, 8, -5),     # Pré-Treino — vencido
    (4, 30, 75),    # BCAA — atenção (≤90 dias)
    (5, 18, 300),   # Glutamina — ok
    (6, 10, 60),    # Hipercalórico — atenção
    (7, 50, 45),    # Barra de Proteína — atenção
    (8, 22, 500),   # Multivitamínico — ok
]


class Command(BaseCommand):
    help = "Popula o banco com produtos fictícios de suplementos e alguns lotes de exemplo, para testar a Planilha/Remanejamento/Pendências sem digitar tudo na mão. Idempotente — seguro rodar de novo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loja", default="Centro de Distribuição",
            help="Nome da loja/CD onde os lotes de exemplo entram (criada se não existir).",
        )

    def handle(self, *args, **options):
        usuario = User.objects.filter(is_superuser=True).order_by("date_joined").first() or User.objects.first()
        if not usuario:
            self.stderr.write(self.style.ERROR(
                "Nenhum usuário encontrado — crie um admin primeiro (/cadastro/ ou createsuperuser) antes de rodar este comando."
            ))
            return

        loja, criada = Loja.objects.get_or_create(nome=options["loja"])
        if criada:
            self.stdout.write(self.style.SUCCESS(f"Loja/CD criada: {loja.nome}"))

        produtos = []
        for codigo, nome, marca in PRODUTOS_DEMO:
            produto, criado = Produto.objects.get_or_create(
                codigo_barras=codigo, defaults={"nome": nome, "marca": marca}
            )
            produtos.append(produto)
            if criado:
                self.stdout.write(f"  + Produto: {produto.nome} ({produto.codigo_barras})")

        hoje = date.today()
        criados_lote = 0
        with transaction.atomic():
            for idx_produto, quantidade, dias in LOTES_DEMO:
                produto = produtos[idx_produto]
                validade = hoje + timedelta(days=dias)

                lote, criado = Lote.objects.get_or_create(
                    produto=produto, validade=validade, loja_atual=loja, status="aprovado", lote=None,
                    defaults={
                        "quantidade": quantidade, "criado_por": usuario.get_username(),
                        "criado_em": timezone.now(), "aprovado_por": usuario.get_username(),
                        "aprovado_em": timezone.now(),
                    },
                )
                if criado:
                    criados_lote += 1
                    Movimentacao.objects.create(
                        lote=lote, tipo="entrada", loja_origem=None, loja_destino=loja,
                        quantidade=quantidade, data=timezone.now(),
                        responsavel=usuario.get_username(), responsavel_user=usuario,
                        observacoes="Lote de exemplo (seed_demo)", status="aprovado",
                    )
                    self.stdout.write(f"  + Lote: {produto.nome} — {quantidade}un — vence {validade.strftime('%d/%m/%Y')}")

        self.stdout.write(self.style.SUCCESS(
            f"\nPronto: {len(produtos)} produto(s) no catálogo, {criados_lote} lote(s) novo(s) criado(s) em '{loja.nome}'."
        ))
