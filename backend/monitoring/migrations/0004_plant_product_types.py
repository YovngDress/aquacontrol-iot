"""Tipos de instalación de la planta de agua potable.

Reemplaza los tipos de la plataforma original (puesta a tierra) por los de
este proyecto. Los tipos originales solo se borran si ninguna instalación
los usa todavía.
"""
from django.db import migrations

PLANT_TYPES = [
    ("Planta de tratamiento", "planta-tratamiento"),
    ("Estanque de almacenamiento", "estanque"),
    ("Punto de muestreo", "punto-muestreo"),
    ("Estación de bombeo", "estacion-bombeo"),
]
OLD_SLUGS = ["electroiman-activo", "malla-a-tierra", "barra-copperweld"]


def forwards(apps, schema_editor):
    ProductType = apps.get_model("monitoring", "ProductType")
    for name, slug in PLANT_TYPES:
        ProductType.objects.get_or_create(slug=slug, defaults={"name": name})
    ProductType.objects.filter(slug__in=OLD_SLUGS, products__isnull=True).delete()


def backwards(apps, schema_editor):
    ProductType = apps.get_model("monitoring", "ProductType")
    ProductType.objects.filter(slug__in=[slug for _, slug in PLANT_TYPES], products__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("monitoring", "0003_plant_help_texts"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
