"""Gateway Raspberry Pi: PLC (Modbus TCP) <-> plataforma (MQTT).

Qué hace:
  1. Lee el PLC por Modbus TCP cada POLL_SECONDS y publica sus variables
     como el dispositivo PLC_DEVICE_SLUG (nivel, caudal, presión, bomba,
     válvula, modo automático, dosificador, alarma).
  2. Escucha los comandos del dashboard (<prefijo>/<plc>/<variable>/set),
     valida el token y escribe el registro de comando correspondiente en el PLC;
     luego publica el estado real leído del PLC (así el botón del dashboard
     confirma el cambio en vez de suponerlo).
  3. Reenvía al PLC las lecturas de calidad del ESP32 (pH, cloro libre,
     turbiedad) para que la lógica del PLC pueda dosificar cloro y levantar
     la alarma.
  4. Publica su propia telemetría como RPI_DEVICE_SLUG: temperatura y uso de
     CPU de la Raspberry Pi y el estado del enlace Modbus con el PLC.

Uso:
    cp config.example.env config.env   # y completa tokens/broker
    python gateway.py                  # lee config.env del mismo directorio
    python gateway.py --config /ruta/a/otro.env
"""
import argparse
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from pymodbus.client import ModbusTcpClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GATEWAY] %(message)s")
log = logging.getLogger("gateway")

# Mapa Modbus (solo holding registers) — debe calzar con plc/AquaControl_S7-1200.scl
# y con plc_simulado.py. Dirección N = "ModbusData".HR[N] en el S7-1200 = registro 4000(N+1).
# variable -> (registro de estado, escala)
PLC_STATUS = {
    "nivel_estanque": (0, 10),
    "caudal": (1, 10),
    "presion": (2, 100),
    "dosificador_cloro": (3, 1),
    "bomba": (4, 1),
    "valvula": (5, 1),
    "modo_auto": (6, 1),
    "alarma": (7, 1),
}
HR_HEARTBEAT = 8
# variable -> registro de comando (lo que el dashboard puede escribir)
PLC_COMMANDS = {"bomba": 10, "valvula": 11, "modo_auto": 12, "dosificador_cloro": 13}
DIGITAL = {"bomba", "valvula", "modo_auto"}
READ_COUNT = 9  # registros 0..8

# variable del ESP32 -> (registro del PLC, escala)
FORWARD_TO_PLC = {"ph": (20, 100), "cloro_libre": (21, 100), "turbidez": (22, 100)}


def load_env_file(path):
    """Lee un archivo KEY=VALUE simple (sin dependencias). Las variables de
    entorno ya definidas tienen prioridad (útil en Docker)."""
    if path and Path(path).exists():
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def cfg(name, default=None):
    return os.environ.get(name, default)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CpuSampler:
    """Uso de CPU a partir de /proc/stat y temperatura del SoC de la Raspberry Pi."""

    def __init__(self):
        self.prev = self._read_stat()

    @staticmethod
    def _read_stat():
        try:
            with open("/proc/stat", encoding="ascii") as f:
                parts = [int(x) for x in f.readline().split()[1:]]
            idle = parts[3] + parts[4]
            return idle, sum(parts)
        except (OSError, ValueError, IndexError):
            return None

    def usage(self):
        cur = self._read_stat()
        if cur is None or self.prev is None:
            return None
        d_idle, d_total = cur[0] - self.prev[0], cur[1] - self.prev[1]
        self.prev = cur
        return round(100.0 * (1 - d_idle / d_total), 1) if d_total > 0 else 0.0

    @staticmethod
    def temperature():
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", encoding="ascii") as f:
                return round(int(f.read().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            # En un PC sin sensor térmico (pruebas) se informa un valor simulado.
            return round(45 + random.uniform(-2, 2), 1)


class Gateway:
    def __init__(self):
        self.prefix = cfg("MQTT_TOPIC_PREFIX", "planta")
        self.plc_slug = cfg("PLC_DEVICE_SLUG", "plc-planta-01")
        self.plc_token = cfg("PLC_DEVICE_TOKEN", "")
        self.rpi_slug = cfg("RPI_DEVICE_SLUG", "rpi-gateway-01")
        self.rpi_token = cfg("RPI_DEVICE_TOKEN", "")
        self.esp32_slug = cfg("ESP32_DEVICE_SLUG", "esp32-calidad-01")
        self.poll_seconds = float(cfg("POLL_SECONDS", "2"))
        self.publish_seconds = float(cfg("PUBLISH_SECONDS", "5"))

        self.plc = ModbusTcpClient(cfg("PLC_HOST", "127.0.0.1"), port=int(cfg("PLC_PORT", "5020")), timeout=2)
        self.plc_unit = int(cfg("PLC_UNIT_ID", "1"))
        self.plc_lock = threading.Lock()
        self.plc_online = False
        self.last_heartbeat = None
        self.heartbeat_stale_polls = 0

        self.last_published = {}
        self.last_publish_time = {}
        self.cpu = CpuSampler()

        self.mqtt = mqtt.Client(client_id=f"{self.rpi_slug}-gateway")
        if cfg("MQTT_USERNAME"):
            self.mqtt.username_pw_set(cfg("MQTT_USERNAME"), cfg("MQTT_PASSWORD", ""))
        self.mqtt.on_connect = self.on_connect
        self.mqtt.on_message = self.on_message
        self.mqtt.reconnect_delay_set(min_delay=1, max_delay=30)

    # ------------------------------------------------------------------ MQTT
    def on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            log.error("Conexión MQTT rechazada (rc=%s): revisa usuario/contraseña", rc)
            return
        log.info("MQTT conectado a %s:%s", cfg("MQTT_BROKER_HOST"), cfg("MQTT_BROKER_PORT"))
        client.subscribe(f"{self.prefix}/{self.plc_slug}/+/set", qos=1)
        for variable in FORWARD_TO_PLC:
            client.subscribe(f"{self.prefix}/{self.esp32_slug}/{variable}", qos=0)

    def on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if len(parts) == 4 and parts[1] == self.plc_slug and parts[3] == "set":
            self.handle_command(parts[2], data)
        elif len(parts) == 3 and parts[1] == self.esp32_slug and parts[2] in FORWARD_TO_PLC:
            self.forward_quality(parts[2], data)

    def publish(self, slug, token, variable, value, force=False):
        now = time.monotonic()
        key = (slug, variable)
        changed = self.last_published.get(key) != value
        due = now - self.last_publish_time.get(key, 0) >= self.publish_seconds
        if not (force or changed or due):
            return
        payload = json.dumps({"value": value, "timestamp": now_iso(), "token": token})
        self.mqtt.publish(f"{self.prefix}/{slug}/{variable}", payload, qos=1)
        self.last_published[key] = value
        self.last_publish_time[key] = now

    # --------------------------------------------------------------- Comandos
    def handle_command(self, variable, data):
        if str(data.get("token", "")) != self.plc_token:
            log.warning("Comando para %s rechazado: token inválido", variable)
            return
        if variable not in PLC_COMMANDS:
            log.warning("La variable %s no es escribible en el PLC", variable)
            return
        try:
            value = float(data["value"])
        except (KeyError, TypeError, ValueError):
            return
        raw = (1 if value >= 0.5 else 0) if variable in DIGITAL else int(max(0, min(100, round(value))))
        with self.plc_lock:
            if not self.ensure_plc():
                log.error("Comando %s=%s no aplicado: PLC sin conexión", variable, value)
                return
            result = self.plc.write_register(PLC_COMMANDS[variable], raw, slave=self.plc_unit)
        if result.isError():
            log.error("El PLC rechazó la escritura de %s: %s", variable, result)
            return
        log.info("Comando aplicado en PLC: %s = %s", variable, value)
        time.sleep(1.2)  # deja correr un ciclo de OB30 (0.5 s) para leer el estado real
        self.poll_plc(force=True)

    def forward_quality(self, variable, data):
        value = data.get("value")
        if not isinstance(value, (int, float)):
            return
        addr, scale = FORWARD_TO_PLC[variable]
        with self.plc_lock:
            if self.ensure_plc():
                self.plc.write_register(addr, int(max(0, min(65535, round(value * scale)))), slave=self.plc_unit)

    # -------------------------------------------------------------------- PLC
    def ensure_plc(self):
        if self.plc.connected:
            return True
        return bool(self.plc.connect())

    def poll_plc(self, force=False):
        with self.plc_lock:
            ok = self.ensure_plc()
            regs = None
            if ok:
                regs = self.plc.read_holding_registers(0, READ_COUNT, slave=self.plc_unit)
                ok = not regs.isError()
            if not ok:
                self.plc.close()

        if ok:
            heartbeat = regs.registers[HR_HEARTBEAT]
            self.heartbeat_stale_polls = self.heartbeat_stale_polls + 1 if heartbeat == self.last_heartbeat else 0
            self.last_heartbeat = heartbeat
            ok = self.heartbeat_stale_polls < 3  # el PLC responde pero su programa está en STOP

        if ok != self.plc_online:
            log.info("Enlace con PLC: %s", "OK" if ok else "PERDIDO")
        self.plc_online = ok
        if not ok:
            return

        for variable, (addr, scale) in PLC_STATUS.items():
            raw = regs.registers[addr]
            value = (1 if raw else 0) if variable in DIGITAL or variable == "alarma" else round(raw / scale, 2)
            self.publish(self.plc_slug, self.plc_token, variable, value, force=force)

    def publish_rpi(self):
        self.publish(self.rpi_slug, self.rpi_token, "plc_conectado", 1 if self.plc_online else 0)
        self.publish(self.rpi_slug, self.rpi_token, "cpu_temp", self.cpu.temperature(), force=True)
        usage = self.cpu.usage()
        if usage is not None:
            self.publish(self.rpi_slug, self.rpi_token, "cpu_uso", usage, force=True)

    # ------------------------------------------------------------------ Loop
    def run(self):
        host, port = cfg("MQTT_BROKER_HOST", "localhost"), int(cfg("MQTT_BROKER_PORT", "1883"))
        log.info("Conectando a MQTT %s:%s y PLC %s:%s", host, port, cfg("PLC_HOST", "127.0.0.1"), cfg("PLC_PORT", "5020"))
        self.mqtt.connect_async(host, port, keepalive=60)
        self.mqtt.loop_start()
        last_rpi = 0.0
        try:
            while True:
                if self.mqtt.is_connected():
                    self.poll_plc()
                    if time.monotonic() - last_rpi >= self.publish_seconds:
                        self.publish_rpi()
                        last_rpi = time.monotonic()
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            self.mqtt.loop_stop()
            self.plc.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gateway PLC (Modbus TCP) <-> MQTT para la Raspberry Pi")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.env")))
    args = parser.parse_args()
    load_env_file(args.config)
    if not cfg("PLC_DEVICE_TOKEN") or not cfg("RPI_DEVICE_TOKEN"):
        log.warning("Faltan PLC_DEVICE_TOKEN / RPI_DEVICE_TOKEN: la plataforma rechazará las lecturas.")
    Gateway().run()
