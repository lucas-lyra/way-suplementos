from django.db import migrations


def criar_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nome in ("CD", "Compra e Venda", "Coordenador/Admin"):
        Group.objects.get_or_create(name=nome)


def remover_grupos(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=("CD", "Compra e Venda", "Coordenador/Admin")).delete()


class Migration(migrations.Migration):
    """Cria os 3 grupos/perfis usados para controle de acesso (CD, Compra e
    Venda, Coordenador/Admin). Idempotente (get_or_create)."""

    dependencies = [
        ("estoque", "0001_initial"),
        ("auth", "__first__"),
    ]

    operations = [
        migrations.RunPython(criar_grupos, remover_grupos),
    ]
