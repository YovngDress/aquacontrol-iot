"""Datos de demostración de la planta de agua potable.

Uso:
    python manage.py seed_demo_data
    python manage.py seed_demo_data --owner admin --hours 6 --seed 2026

Crea (o reutiliza, si ya existen) la instalación "Planta de tratamiento
Sector Norte" y sus tres dispositivos:

    esp32-calidad-01   ESP32         pH, turbiedad, cloro libre, temperatura, oxígeno disuelto
    plc-planta-01      PLC           nivel, caudal, presión, bomba, válvula, dosificador, alarma, modo auto
    rpi-gateway-01     Raspberry Pi  temperatura y uso de CPU, enlace Modbus con el PLC

Genera una serie histórica de lecturas por sensor para que el dashboard no
se vea vacío, y al final imprime los tokens listos para pegar en el .env
(simulador / gateway de la Raspberry Pi) y en el config.h del ESP32.
Se puede re-correr sin duplicar nada.
"""
import math
import random
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from monitoring.models import Device, Product, ProductType, Profile, Sensor, SensorReading
from monitoring.plant import default_unit

PLANT_NAME = "Planta de tratamiento Sector Norte"
PLANT_LOCATION = (-33.4489, -70.6693)

# slug -> (nombre, tipo de hardware, ubicación, {variable: (base, amplitud, ruido)})
DEVICES = {
    "esp32-calidad-01": (
        "ESP32 · Calidad de agua",
        "ESP32",
        "Salida del estanque",
        {
            "ph": (7.3, 0.25, 0.05),
            "turbidez": (0.8, 0.4, 0.1),
            "cloro_libre": (0.8, 0.25, 0.05),
            "temperatura": (14.0, 2.0, 0.2),
            "oxigeno_disuelto": (8.0, 0.6, 0.1),
        },
    ),
    "plc-planta-01": (
        "PLC · Control de planta",
        "PLC",
        "Tablero de control",
        {
            "nivel_estanque": (60.0, 25.0, 1.0),
            "caudal": (80.0, 10.0, 3.0),
            "presion": (2.6, 0.3, 0.05),
            "bomba": None,
            "valvula": None,
            "modo_auto": None,
            "dosificador_cloro": None,
            "alarma": None,
        },
    ),
    "rpi-gateway-01": (
        "Raspberry Pi · Gateway",
        "Raspberry Pi",
        "Tablero de control",
        {
            "cpu_temp": (48.0, 4.0, 0.8),
            "cpu_uso": (18.0, 8.0, 4.0),
            "plc_conectado": None,
        },
    ),
}

# Estado actual de los actuadores / señales digitales (una sola lectura).
INITIAL_STATES = {
    "bomba": 1,
    "valvula": 1,
    "modo_auto": 1,
    "dosificador_cloro": 40,
    "alarma": 0,
    "plc_conectado": 1,
}


class Command(BaseCommand):
    help = "Carga la planta de agua potable de demostración (ESP32 + PLC + Raspberry Pi) con lecturas históricas."

    def add_arguments(self, parser):
        parser.add_argument("--owner", default=None, help="Usuario dueño de los dispositivos (por defecto: el primer Global).")
        parser.add_argument("--hours", type=float, default=3.0, help="Horas de historial a generar.")
        parser.add_argument("--interval", type=int, default=60, help="Segundos entre lecturas históricas.")
        parser.add_argument("--seed", type=int, default=2026, help="Semilla para aleatoriedad reproducible.")

    def handle(self, *args, **options):
        rng = random.Random(options["seed"])

        if options["owner"]:
            owner = User.objects.filter(username=options["owner"]).first()
        else:
            owner = User.objects.filter(profile__role=Profile.ROLE_GLOBAL, is_active=True).first()
        if owner is None:
            self.stderr.write(self.style.ERROR("No se encontró el usuario dueño; crea un superusuario primero."))
            return

        with transaction.atomic():
            plant_type, _ = ProductType.objects.get_or_create(
                slug="planta-tratamiento", defaults={"name": "Planta de tratamiento"}
            )
            plant, _ = Product.objects.get_or_create(
                owner=owner,
                name=PLANT_NAME,
                defaults={
                    "product_type": plant_type,
                    "latitude": PLANT_LOCATION[0],
                    "longitude": PLANT_LOCATION[1],
                },
            )

            readings_created = 0
            now = timezone.now()
            steps = max(1, int(options["hours"] * 3600 / options["interval"]))

            for slug, (name, tag, location, variables) in DEVICES.items():
                device, _ = Device.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "owner": owner,
                        "product": plant,
                        "name": name,
                        "tag": tag,
                        "location": location,
                        "latitude": PLANT_LOCATION[0],
                        "longitude": PLANT_LOCATION[1],
                    },
                )
                device.last_seen_at = now
                device.save(update_fields=["last_seen_at"])

                for variable, profile in variables.items():
                    sensor, _ = Sensor.objects.get_or_create(
                        device=device, variable=variable, defaults={"unit": default_unit(variable)}
                    )
                    sensor.get_widget()  # crea el gadget por defecto (plant.py)
                    if sensor.readings.exists():
                        continue

                    if profile is None:
                        SensorReading.objects.create(sensor=sensor, value=INITIAL_STATES[variable], timestamp=now)
                        readings_created += 1
                        continue

                    base, amplitude, noise = profile
                    phase = rng.uniform(0, 2 * math.pi)
                    batch = []
                    for i in range(steps):
                        ts = now - timedelta(seconds=(steps - i) * options["interval"])
                        wave = math.sin(phase + 2 * math.pi * i / max(steps, 60))
                        value = base + amplitude * wave + rng.gauss(0, noise)
                        batch.append(SensorReading(sensor=sensor, value=round(max(0.0, value), 3), timestamp=ts))
                    SensorReading.objects.bulk_create(batch)
                    readings_created += len(batch)

        self.stdout.write(self.style.SUCCESS(f"Listo: planta «{PLANT_NAME}», {readings_created} lecturas generadas."))
        self.stdout.write("\nTokens de los dispositivos (cópialos al .env / config.h):\n")
        tokens = {d.slug: d.auth_token for d in Device.objects.filter(slug__in=DEVICES.keys())}
        self.stdout.write(f"SIMULATOR_DEVICE_TOKENS=esp32-calidad-01:{tokens['esp32-calidad-01']}")
        self.stdout.write(f"PLC_DEVICE_TOKEN={tokens['plc-planta-01']}")
        self.stdout.write(f"RPI_DEVICE_TOKEN={tokens['rpi-gateway-01']}")
        self.stdout.write(f'#define DEVICE_TOKEN "{tokens["esp32-calidad-01"]}"   // firmware ESP32')
