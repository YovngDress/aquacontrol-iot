// Copia este archivo a "config.h" (mismo directorio) y completa tus valores.
// config.h queda ignorado por git (contiene el token del dispositivo) — ver .gitignore.
#pragma once

// --- Identidad del dispositivo (la genera la plataforma al registrar el Device) ---
// Dashboard -> Dispositivos -> "+ Agregar dispositivo" -> te muestra el ID (slug) y el token.
// (o corre `python manage.py seed_demo_data`, que imprime el token al final)
#define DEVICE_SLUG   "esp32-calidad-01"       // debe coincidir EXACTO con el slug registrado
#define DEVICE_TOKEN  "paste-token-here"       // token único del dispositivo
#define DEVICE_AP_TAG "esp32-calidad-01-setup" // nombre del punto de acceso WiFiManager al aprovisionar

// --- WiFi (opcional): si se define, intenta conectar directo antes del portal cautivo ---
#define WIFI_SSID     ""
#define WIFI_PASSWORD ""

// --- Broker MQTT ---
// Local (docker compose): IP de tu PC en la red local (no "localhost") y MOSQUITTO_HOST_PORT del .env.
// Railway: host:puerto del TCP Proxy del servicio mosquitto.
#define MQTT_BROKER_HOST  "192.168.1.100"
#define MQTT_BROKER_PORT  1883
#define MQTT_USERNAME     "planta"                   // MQTT_USERNAME del .env
#define MQTT_PASSWORD     "change-me-mqtt-password"  // MQTT_PASSWORD del .env
#define MQTT_TOPIC_PREFIX "planta"                   // MQTT_TOPIC_PREFIX del .env

// Slug del PLC: el ESP32 escucha su alarma (LED) y su dosificador (cloro simulado)
#define PLC_DEVICE_SLUG "plc-planta-01"

#define PUBLISH_INTERVAL_MS 5000

// --- Sensor de pH real (ej. módulo PH-4502C o "Analog pH Sensor" de DFRobot) ---
// IMPORTANTE: el módulo PH-4502C entrega hasta 5 V y el ADC del ESP32 soporta 3.3 V:
// usa un divisor resistivo (ej. 10k / 20k) entre la salida "Po" y el pin.
#define USE_REAL_PH     1      // 0 = pH también simulado
#define PH_PIN          34     // ADC1 (32-39): funciona con WiFi encendido
#define PH_SAMPLES      30     // promedio de N lecturas para bajar el ruido
// Calibración de 2 puntos: sumerge la sonda en buffer pH 7 y pH 4 y anota
// el voltaje que muestra el Monitor Serie ("pH raw mV=...").
#define PH_MV_AT_7      1650.0f
#define PH_MV_AT_4      2030.0f

// --- Variables simuladas (aún sin sensor físico) ---
#define SIMULATE_TURBIDEZ    1
#define SIMULATE_CLORO       1
#define SIMULATE_TEMPERATURA 1   // si tienen DS18B20, ponlo en 0 y completa readTemperature() en el .ino
#define SIMULATE_OXIGENO     1

// --- Actuador local: LED/buzzer que se enciende con la alarma del PLC ---
#define ALARM_PIN 2   // LED integrado en muchas placas ESP32 DevKit

// --- NTP (para el "timestamp" de cada lectura) ---
#define NTP_SERVER     "pool.ntp.org"
#define GMT_OFFSET_SEC 0
#define DST_OFFSET_SEC 0
