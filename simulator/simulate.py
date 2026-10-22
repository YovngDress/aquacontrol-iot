"""Simulador del ESP32 de calidad de agua (para probar sin hardware).

Publica, como el dispositivo esp32-calidad-01, las mismas variables que el
firmware real: ph, turbidez, cloro_libre, temperatura y oxigeno_disuelto.

- El cloro libre responde al dosificador del PLC: escucha
  <prefijo>/plc-planta-01/dosificador_cloro y converge hacia ~0.02 mg/L por
  cada % de dosificación (40 % -> ~0.8 mg/L).
- Con SIMULATOR_EVENTS=1 (por defecto) cada tanto ocurre un "evento" de
  turbiedad alta durante ~40 s, para mostrar la alarma y el panel NCh 409
  pasando a "No cumple".

Variables de entorno: MQTT_BROKER_HOST, MQTT_BROKER_PORT, MQTT_USERNAME,
MQTT_PASSWORD, MQTT_TOPIC_PREFIX, SIMULATOR_DEVICE_TOKENS (slug:token),
SIMULATOR_INTERVAL_SECONDS, SIMULATOR_EVENTS, PLC_DEVICE_SLUG.
"""
import json
import os
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

MQTT_BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "mosquitto")
MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "planta")
PUBLISH_INTERVAL_SECONDS = float(os.environ.get("SIMULATOR_INTERVAL_SECONDS", "3"))
EVENTS_ENABLED = os.environ.get("SIMULATOR_EVENTS", "1") == "1"
PLC_SLUG = os.environ.get("PLC_DEVICE_SLUG", "plc-planta-01")
DEVICE_SLUG = "esp32-calidad-01"


def parse_device_tokens(raw):
    """Parses SIMULATOR_DEVICE_TOKENS="slug1:token1,slug2:token2" into a dict."""
    tokens = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        slug, token = pair.split(":", 1)
        tokens[slug.strip()] = token.strip()
    return tokens


TOKEN = parse_device_tokens(os.environ.get("SIMULATOR_DEVICE_TOKENS", "")).get(DEVICE_SLUG, "")

# variable -> (mínimo, máximo, valor inicial, paso máximo por tick)
RANGES = {
    "ph": (6.0, 9.0, 7.3, 0.04),
    "turbidez": (0.1, 8.0, 0.7, 0.08),
    "cloro_libre": (0.0, 3.0, 0.8, 0.03),
    "temperatura": (8.0, 22.0, 14.0, 0.1),
    "oxigeno_disuelto": (5.5, 10.0, 8.0, 0.08),
}

state = {name: spec[2] for name, spec in RANGES.items()}
dosing = {"pct": None}
event = {"until": 0.0, "next": time.time() + random.uniform(120, 240)}


def step(name):
    low, high, start, max_step = RANGES[name]
    value = state[name] + random.uniform(-max_step, max_step)

    if name == "cloro_libre" and dosing["pct"] is not None:
        target = dosing["pct"] * 0.02
        value += (target - value) * 0.15
    elif name == "turbidez":
        now = time.time()
        if EVENTS_ENABLED and now >= event["next"]:
            event["until"] = now + 40
            event["next"] = now + random.uniform(180, 360)
            print("Evento: turbiedad alta (lluvia / arrastre de sedimentos)", flush=True)
        target = 3.5 if now < event["until"] else start
        value += (target - value) * 0.2
    else:
        value += (start - value) * 0.05  # vuelve suavemente a su valor típico

    state[name] = max(low, min(high, value))
    return round(state[name], 2)


def on_connect(client, userdata, flags, rc):
    print(f"Conectado a MQTT (rc={rc}) en {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}", flush=True)
    client.subscribe(f"{MQTT_TOPIC_PREFIX}/{PLC_SLUG}/dosificador_cloro")


def on_message(client, userdata, msg):
    try:
        dosing["pct"] = float(json.loads(msg.payload)["value"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        pass


def main():
    if not TOKEN:
        print(f"Aviso: no hay token para {DEVICE_SLUG} en SIMULATOR_DEVICE_TOKENS; "
              "la plataforma pondrá las lecturas en cuarentena.", flush=True)

    client = mqtt.Client(client_id="simulador-esp32")
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    while True:
        try:
            client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            break
        except OSError as exc:
            print(f"Broker no disponible ({exc}), reintentando en 3s...", flush=True)
            time.sleep(3)

    client.loop_start()
    while True:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for name in RANGES:
            payload = json.dumps({"value": step(name), "timestamp": ts, "token": TOKEN})
            client.publish(f"{MQTT_TOPIC_PREFIX}/{DEVICE_SLUG}/{name}", payload, qos=1)
        time.sleep(PUBLISH_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
