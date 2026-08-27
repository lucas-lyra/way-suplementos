from datetime import date

from django.conf import settings
from django.db import models


# Status compartilhado por Lote e Movimentacao. O valor gravado no banco é
# 'aguardando_aprovacao' (já existente no CHECK CONSTRAINT do Postgres) — o
# label exibido na interface é "Pendente".
STATUS_CHOICES = [
    ("aguardando_aprovacao", "Pendente"),
    ("aprovado", "Aprovado"),
    ("rejeitado", "Rejeitado"),
]


class Produto(models.Model):
    """Catálogo de produtos. Tabela já existente no Postgres do Supabase
    (criada por schema_lote_validade.sql) — managed=False: o Django só lê/escreve
    nela, nunca tenta criar/alterar sua estrutura via migration."""
    id = models.BigAutoField(primary_key=True)
    codigo_barras = models.CharField(max_length=64, unique=True)
    nome = models.CharField(max_length=255)
    marca = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = "produtos"

    def __str__(self):
        return f"{self.nome} ({self.codigo_barras})"


class Loja(models.Model):
    """Lojas e o próprio CD, tratado como mais uma loja. Tabela já existente."""
    id = models.BigAutoField(primary_key=True)
    nome = models.CharField(max_length=255, unique=True)

    class Meta:
        managed = False
        db_table = "lojas"

    def __str__(self):
        return self.nome


class Lote(models.Model):
    """Saldo de um produto, com validade, numa loja específica. Tabela já
    existente (o app nunca deleta uma linha, só zera a quantidade, para manter
    Movimentacao sempre rastreável)."""
    id = models.BigAutoField(primary_key=True)
    produto = models.ForeignKey(Produto, on_delete=models.DO_NOTHING, db_column="produto_id", related_name="lotes")
    lote = models.CharField("Número do Lote", max_length=100, blank=True, null=True)
    validade = models.DateField()
    loja_atual = models.ForeignKey(Loja, on_delete=models.DO_NOTHING, db_column="loja_atual_id", related_name="lotes")
    quantidade = models.IntegerField(default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="aprovado")

    # Auditoria de quem lançou/aprovou — texto simples (nome de usuário), não FK,
    # porque a coluna já existia com dados históricos em texto antes deste app
    # Django; ver Movimentacao.responsavel_user para o equivalente com FK.
    criado_por = models.CharField(max_length=150, blank=True, null=True)
    criado_em = models.DateTimeField(blank=True, null=True)
    aprovado_por = models.CharField(max_length=150, blank=True, null=True)
    aprovado_em = models.DateTimeField(blank=True, null=True)
    motivo_rejeicao = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "lotes"
        ordering = ["validade"]

    def __str__(self):
        return f"{self.produto} — Lote {self.lote or '-'} ({self.validade})"

    @property
    def dias_para_vencer(self):
        return (self.validade - date.today()).days

    @property
    def status_validade(self):
        """(chave, rótulo) usado na Planilha Compartilhada: vencido / crítico
        (≤30 dias) / atenção (≤90 dias) / ok — conforme pedido no briefing."""
        dias = self.dias_para_vencer
        if dias < 0:
            return ("vencido", "Vencido")
        if dias <= 30:
            return ("critico", "Crítico (≤30 dias)")
        if dias <= 90:
            return ("atencao", "Atenção (≤90 dias)")
        return ("ok", "OK")


class Movimentacao(models.Model):
    """Histórico de entradas e remanejamentos. Tabela já existente, mas com 3
    colunas NOVAS adicionadas por sql_migracao_django.sql (rodar depois do
    `manage.py migrate`, que é quando a tabela auth_user passa a existir):
    status, motivo_rejeicao e responsavel_user_id (FK para auth_user, conforme
    "responsável (FK para usuário)" pedido no briefing — o campo `responsavel`
    (texto) é mantido só para compatibilidade com registros antigos)."""
    TIPO_CHOICES = [("entrada", "Entrada"), ("remanejamento", "Remanejamento")]

    id = models.BigAutoField(primary_key=True)
    lote = models.ForeignKey(Lote, on_delete=models.DO_NOTHING, db_column="lote_id", related_name="movimentacoes")
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    loja_origem = models.ForeignKey(
        Loja, on_delete=models.DO_NOTHING, db_column="loja_origem_id",
        related_name="movimentacoes_saida", blank=True, null=True
    )
    loja_destino = models.ForeignKey(
        Loja, on_delete=models.DO_NOTHING, db_column="loja_destino_id",
        related_name="movimentacoes_entrada"
    )
    quantidade = models.IntegerField()
    data = models.DateTimeField()

    responsavel = models.CharField(max_length=150, blank=True, null=True)  # legado (texto)
    responsavel_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, db_column="responsavel_user_id",
        related_name="movimentacoes", blank=True, null=True
    )
    observacoes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="aprovado")
    motivo_rejeicao = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "movimentacoes"
        ordering = ["-data"]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.lote} ({self.quantidade} un)"
