from django.db import migrations


def criar_grupo(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Motorista")


def remover_grupo(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="Motorista").delete()


class Migration(migrations.Migration):
    """Cria o grupo/perfil 'Motorista' (acesso restrito à tela de Rotas de
    Entrega). Idempotente."""

    dependencies = [
        ("estoque", "0004_notafiscalsaida_paradarota_itemparada"),
    ]

    operations = [
        migrations.RunPython(criar_grupo, remover_grupo),
    ]
