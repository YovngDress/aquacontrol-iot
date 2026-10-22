# AquaControl IoT — Planta de agua potable (NCh 409)

Plataforma web IoT en **Django** para monitorear y controlar una planta de
agua potable. Conecta los tres equipos que exige el proyecto:

| Equipo | Rol en la planta | Cómo se conecta |
|---|---|---|
| **ESP32** | Mide la calidad del agua: **pH (sensor real)**, turbiedad, cloro libre, temperatura y oxígeno disuelto (simulados) | WiFi → MQTT |
| **PLC** | Controla el proceso: bomba, electroválvula, dosificador de cloro, nivel del estanque, alarma | Modbus TCP → Raspberry Pi |
| **Raspberry Pi** | Gateway: lee/escribe el PLC por Modbus TCP y lo publica por MQTT; corre el PLC simulado | Ethernet/WiFi → MQTT |

Basada en la plataforma [`urbanatika/ecs-iot`](https://github.com/urbanatika/ecs-iot)
(Mosquitto + Django + PostgreSQL + Docker). Se adaptó el diseño, las
variables de proceso, las entradas/salidas y el dashboard a este proyecto.

## Arquitectura

```
 ┌──────────── Campo ─────────────┐                ┌───────────── Servidor (Docker / Railway) ─────────────┐
 │                                │                │                                                       │
 │  Sonda pH ─┐                   │   MQTT         │  ┌───────────┐   ┌───────────────┐   ┌────────────┐   │
 │            ESP32 ──────────────┼────────────────┼─▶│ Mosquitto │──▶│ mqtt_listener │──▶│ PostgreSQL │   │
 │  LED alarma┘   ▲               │                │  └───────────┘   └───────────────┘   └─────┬──────┘   │
 │                │ alarma/dosif. │                │        ▲                                   │          │
 │  PLC ◀─Modbus TCP─▶ Raspberry Pi ──────────────┼────────┘  comandos /set        ┌──────────▼───────┐  │
 │  (bomba, válvula,   (gateway.py)│   MQTT         │        ◀──────────────────────│ Django (dashboard)│◀─ navegador
 │   dosificador)                  │                │                               └──────────────────┘  │
 └─────────────────────────────────┘                └───────────────────────────────────────────────────────┘
```

Flujo de control (lazo cerrado):

1. El ESP32 publica pH, cloro y turbiedad.
2. El gateway de la Raspberry Pi reenvía esas lecturas a registros del PLC.
3. El PLC (modo automático) ajusta el **dosificador de cloro** para mantener
   ~0.8 mg/L, controla la **bomba** por nivel (ON < 30 %, OFF > 90 %) y
   levanta la **alarma** si algo sale de NCh 409.
4. El ESP32 enciende su LED de alarma cuando el PLC la activa, y su cloro
   (simulado) responde al dosificador.
5. Desde el dashboard el operador pasa a **modo manual** y acciona bomba,
   válvula o dosificador; el gateway escribe la orden en el PLC y publica el
   estado real (el botón confirma lo que hizo el PLC, no lo supone).

## Dashboard

- **Cumplimiento NCh 409/1**: pH 6.5–8.5, turbiedad ≤ 2 NTU, cloro libre
  residual 0.2–2.0 mg/L, con veredicto "Agua apta / No cumple".
- **Diagrama del proceso** animado: captación → bomba → estanque (nivel) →
  válvula → red (caudal, presión), dosificador, modo y alarma.
- **Tarjetas por dispositivo** (ESP32 / PLC / Raspberry Pi) con su gadget:
  medidores para calidad y nivel, gráficos, botones (bomba, válvula, modo
  automático), slider (dosificador) y LED (alarma, enlace con PLC).
- Login obligatorio, roles (Global / Administrador / Operador), mapa,
  instalaciones y registro de dispositivos con token (heredado de la plataforma base).

Temperatura y oxígeno disuelto **no tienen límite en NCh 409/1**; se muestran
con un rango operacional de referencia que no afecta el veredicto. Todos los
rangos están en [`backend/monitoring/plant.py`](backend/monitoring/plant.py).

## Variables y topics MQTT

Topic: `planta/<dispositivo>/<variable>` · Payload: `{"value": 7.2, "timestamp": "...", "token": "<token>"}`
Comandos: `planta/<dispositivo>/<variable>/set` · `{"value": 1, "token": "<token>"}`

| Dispositivo | Variable | Unidad | Tipo | Gadget |
|---|---|---|---|---|
| `esp32-calidad-01` | `ph` | — | entrada (sensor real) | medidor |
| | `turbidez` | NTU | entrada (simulada) | medidor |
| | `cloro_libre` | mg/L | entrada (simulada) | medidor |
| | `temperatura` | °C | entrada (simulada / DS18B20) | gráfico |
| | `oxigeno_disuelto` | mg/L | entrada (simulada) | gráfico |
| `plc-planta-01` | `nivel_estanque` | % | entrada PLC | medidor |
| | `caudal` | L/min | entrada PLC | gráfico |
| | `presion` | bar | entrada PLC | numérico |
| | `bomba` | 0/1 | **salida** PLC | botón |
| | `valvula` | 0/1 | **salida** PLC | botón |
| | `dosificador_cloro` | % | **salida** PLC | slider |
| | `modo_auto` | 0/1 | marca PLC | botón |
| | `alarma` | 0/1 | salida PLC | LED |
| `rpi-gateway-01` | `cpu_temp`, `cpu_uso` | °C, % | estado Raspberry Pi | numérico / medidor |
| | `plc_conectado` | 0/1 | enlace Modbus | LED |

Una variable nueva se agrega en `plant.py`; el sensor y su gadget se crean
solos con el primer mensaje MQTT.

## Puesta en marcha (PC con Docker, sin hardware)

Requisitos: Docker Desktop y Git.

```bash
cp .env.example .env            # Windows: copy .env.example .env
# edita .env: cambia todas las contraseñas
```

Genera el archivo de usuarios de Mosquitto (una sola vez; usa el mismo
usuario/contraseña que pusiste en `MQTT_USERNAME`/`MQTT_PASSWORD`):

```bash
docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" eclipse-mosquitto:2 \
  sh -c "mosquitto_passwd -b -c /mosquitto/config/passwd planta TU_PASSWORD_MQTT && chmod 644 /mosquitto/config/passwd"
```
(En Windows CMD usa `%cd%` en vez de `$PWD`; en PowerShell, `${PWD}`.)

Levanta todo y carga la planta de demostración:

```bash
docker compose up --build -d
docker compose exec web python manage.py seed_demo_data
```

`seed_demo_data` registra los 3 dispositivos y al final imprime
`SIMULATOR_DEVICE_TOKENS=...`, `PLC_DEVICE_TOKEN=...` y `RPI_DEVICE_TOKEN=...`.
Pégalos en `.env` y reinicia los simuladores:

```bash
docker compose up -d simulator gateway
```

Abre <http://localhost:8000/monitoring/> e inicia sesión con
`DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`.

Servicios de `docker-compose.yml`:

| Servicio | Rol |
|---|---|
| `mosquitto` | Broker MQTT con usuario/contraseña |
| `db` | PostgreSQL |
| `migrate` | Migraciones + superusuario (una vez) |
| `web` | Django (gunicorn): dashboard y API |
| `mqtt_listener` | Guarda en la BD las lecturas MQTT validadas por token |
| `simulator` | Simula el ESP32 (si no está conectado el real) |
| `plc_sim` | PLC simulado, Modbus TCP :5020 |
| `gateway` | Gateway Modbus ↔ MQTT (lo mismo que corre la Raspberry Pi) |

Cuando conecten el hardware real: `docker compose stop simulator plc_sim gateway`
y usen el ESP32 y la Raspberry Pi.

## Hardware real

- **ESP32** → [`firmware/README.md`](firmware/README.md) (sketch, conexión de la sonda de pH, calibración).
- **Raspberry Pi + PLC** → [`raspberry/README.md`](raspberry/README.md) (PLC simulado, gateway, mapa Modbus, servicios systemd).

El ESP32 y la Raspberry Pi deben llegar al broker: en la misma red, la IP del
PC que corre Docker y el puerto `MOSQUITTO_HOST_PORT`; desde internet, el TCP
Proxy de Railway (ver abajo).

## Demostración sugerida para la entrega

1. Dashboard en "Agua apta", modo automático: el PLC prende/apaga la bomba
   solo según el nivel (diagrama animado).
2. Sumergir la sonda de pH en vinagre o bicarbonato → pH fuera de 6.5–8.5 →
   panel "No cumple", **alarma del PLC** y **LED del ESP32** encendidos.
3. Pasar a **modo manual**, cerrar la válvula y mover el dosificador: el PLC
   aplica la orden y el cloro del ESP32 sube/baja.
4. Apagar el PLC simulado en la Raspberry Pi → "PLC sin enlace" en el dashboard.
5. El simulador genera cada 3–6 min un evento de turbiedad alta (~40 s) para mostrar la alarma sin tocar nada.

## Despliegue con acceso público (Railway)

Igual que la plataforma base: servicios `web`, `mqtt_listener` (Custom
Start Command `python manage.py run_mqtt_listener`) y `mosquitto` (Root
Directory `mosquitto`, con TCP Proxy en el puerto 1883) + plugin PostgreSQL,
todos desde este repo.

- Variables de `web` / `mqtt_listener`: las de `.env.example` (sin `WEB_HOST_PORT`/`MOSQUITTO_HOST_PORT`), `POSTGRES_*` apuntando al plugin, `MQTT_BROKER_HOST=mosquitto.railway.internal`, `DJANGO_ALLOWED_HOSTS=<tu-app>.up.railway.app` y `DJANGO_CSRF_TRUSTED_ORIGINS=https://<tu-app>.up.railway.app`.
- El ESP32 y la Raspberry Pi apuntan al host:puerto público del TCP Proxy de `mosquitto`.
- Superusuario: `railway run python manage.py createsuperuser`; luego `seed_demo_data`.
- No despliegues `simulator`, `plc_sim` ni `gateway` en Railway (la Raspberry Pi hace ese trabajo).

## Correr Django sin Docker (ej. en la Raspberry Pi)

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DB_ENGINE=sqlite MQTT_BROKER_HOST=localhost MQTT_USERNAME=planta MQTT_PASSWORD=... DJANGO_DEBUG=true
python manage.py migrate && python manage.py createsuperuser && python manage.py seed_demo_data
python manage.py runserver 0.0.0.0:8000   # y en otra terminal: python manage.py run_mqtt_listener
```

## Estructura

```
backend/            Django: app monitoring (modelos, listener MQTT, dashboard)
  monitoring/plant.py   ← variables de la planta y límites NCh 409
firmware/           Sketch del ESP32 (pH real + sensores simulados)
raspberry/          PLC simulado (Modbus TCP) + gateway Modbus ↔ MQTT + systemd
simulator/          Simulador del ESP32 para pruebas sin hardware
mosquitto/          Configuración del broker
```

## Créditos

Plataforma base: `urbanatika/ecs-iot` (profesor del curso). Adaptación a
planta de agua potable: grupo del proyecto.
