from django.contrib import admin

from .models import ItemNotaFiscal, Lote, Loja, Movimentacao, NotaFiscal, Produto


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ("nome", "codigo_barras", "marca")
    search_fields = ("nome", "codigo_barras")


@admin.register(Loja)
class LojaAdmin(admin.ModelAdmin):
    list_display = ("nome",)


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    list_display = ("produto", "lote", "validade", "loja_atual", "quantidade", "status")
    list_filter = ("status", "loja_atual")
    search_fields = ("produto__nome", "lote")


@admin.register(Movimentacao)
class MovimentacaoAdmin(admin.ModelAdmin):
    list_display = ("lote", "tipo", "loja_origem", "loja_destino", "quantidade", "data", "status")
    list_filter = ("tipo", "status")


class ItemNotaFiscalInline(admin.TabularInline):
    model = ItemNotaFiscal
    extra = 0
    fields = ("produto", "validade", "quantidade", "observacao", "status", "motivo_rejeicao", "lote_gerado")
    readonly_fields = ("lote_gerado",)


@admin.register(NotaFiscal)
class NotaFiscalAdmin(admin.ModelAdmin):
    list_display = ("numero", "fornecedor", "loja_destino", "responsavel", "status", "data_recebimento")
    list_filter = ("status", "loja_destino")
    search_fields = ("numero", "fornecedor")
    inlines = [ItemNotaFiscalInline]
