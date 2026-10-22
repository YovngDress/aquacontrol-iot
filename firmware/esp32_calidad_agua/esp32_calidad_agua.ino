// AquaControl IoT - ESP32 de calidad de agua potable
//
// Publica cada PUBLISH_INTERVAL_MS, como el dispositivo DEVICE_SLUG:
//   <prefijo>/<slug>/ph                 (sensor real, analógico, calibración de 2 puntos)
//   <prefijo>/<slug>/turbidez           NTU    (simulado)
//   <prefijo>/<slug>/cloro_libre        mg/L   (simulado: responde al dosificador del PLC)
//   <prefijo>/<slug>/temperatura        °C     (simulado o DS18B20)
//   <prefijo>/<slug>/oxigeno_disuelto   mg/L   (simulado)
// Payload: {"value": 7.21, "timestamp": "...", "token": "<DEVICE_TOKEN>"}
//
// Además escucha al PLC (vía gateway de la Raspberry Pi):
//   <prefijo>/<plc>/alarma              -> enciende ALARM_PIN
//   <prefijo>/<plc>/dosificador_cloro   -> el cloro simulado converge a dosificador * 0.02 mg/L
//
// Librerías (Arduino IDE -> Manage Libraries):
//   WiFiManager (tzapu), PubSubClient (Nick O'Leary), ArduinoJson (Benoit Blanchon, v6.x)

#include <WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

#include "config.h"

WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);

char alarmTopic[128];
char dosingTopic[128];

unsigned long lastPublishAt = 0;

// Estado de las variables simuladas
float simTurbidez = 0.7f;
float simCloro = 0.8f;
float simTemperatura = 14.0f;
float simOxigeno = 8.0f;
float simPh = 7.3f;
float plcDosing = -1.0f;  // % del dosificador recibido del PLC (-1 = aún no llega)

float randomStep(float maxStep) {
  return ((float)random(-1000, 1001) / 1000.0f) * maxStep;
}

float clampf(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// ------------------------------------------------------------------ WiFi
void setupWiFi() {
  if (strlen(WIFI_SSID) > 0) {
    Serial.print("Conectando directo a WiFi ");
    Serial.println(WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
      delay(500);
      Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("\nWiFi conectado, IP: ");
      Serial.println(WiFi.localIP());
      return;
    }
    Serial.println("\nNo se pudo conectar con WIFI_SSID/WIFI_PASSWORD, se abre el portal cautivo.");
  }

  WiFiManager wifiManager;
  if (!wifiManager.autoConnect(DEVICE_AP_TAG)) {
    Serial.println("No se pudo conectar a WiFi, reiniciando...");
    delay(3000);
    ESP.restart();
  }
  Serial.print("WiFi conectado, IP: ");
  Serial.println(WiFi.localIP());
}

String isoTimestampNow() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo, 100)) {
    return "";  // sin hora NTP aún: el servidor usa la hora de recepción
  }
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  return String(buf);
}

// ------------------------------------------------------------------ MQTT
void publishTelemetry(const char* variable, float value) {
  StaticJsonDocument<192> doc;
  doc["value"] = roundf(value * 100.0f) / 100.0f;  // 2 decimales
  String ts = isoTimestampNow();
  if (ts.length() > 0) {
    doc["timestamp"] = ts;
  }
  doc["token"] = DEVICE_TOKEN;

  char payload[192];
  size_t len = serializeJson(doc, payload);

  char topic[128];
  snprintf(topic, sizeof(topic), "%s/%s/%s", MQTT_TOPIC_PREFIX, DEVICE_SLUG, variable);
  mqttClient.publish(topic, (uint8_t*)payload, len, false);
  Serial.print("-> ");
  Serial.print(topic);
  Serial.print(" ");
  Serial.println(payload);
}

// Mensajes del PLC (telemetría que publica el gateway de la Raspberry Pi)
void handleMessage(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<192> doc;
  if (deserializeJson(doc, payload, length)) {
    return;
  }
  float value = doc["value"] | 0.0f;

  if (strcmp(topic, alarmTopic) == 0) {
    digitalWrite(ALARM_PIN, value >= 0.5f ? HIGH : LOW);
    Serial.print("Alarma del PLC: ");
    Serial.println(value >= 0.5f ? "ACTIVA" : "normal");
  } else if (strcmp(topic, dosingTopic) == 0) {
    plcDosing = value;
  }
}

void reconnectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Conectando a MQTT ");
    Serial.print(MQTT_BROKER_HOST);
    Serial.print(":");
    Serial.println(MQTT_BROKER_PORT);

    String clientId = String(DEVICE_SLUG) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    if (mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD)) {
      Serial.println("MQTT conectado.");
      mqttClient.subscribe(alarmTopic);
      mqttClient.subscribe(dosingTopic);
    } else {
      Serial.print("Fallo MQTT, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" reintentando en 5s...");
      delay(5000);
    }
  }
}

// --------------------------------------------------------------- Sensores
float readPh() {
#if USE_REAL_PH
  uint32_t sumMv = 0;
  for (int i = 0; i < PH_SAMPLES; i++) {
    sumMv += analogReadMilliVolts(PH_PIN);
    delay(10);
  }
  float mv = (float)sumMv / PH_SAMPLES;
  // Recta entre los dos puntos de calibración (pH 7 y pH 4)
  float slope = (7.0f - 4.0f) / (PH_MV_AT_7 - PH_MV_AT_4);
  float ph = 7.0f + (mv - PH_MV_AT_7) * slope;
  Serial.print("pH raw mV=");
  Serial.print(mv, 0);
  Serial.print(" -> pH=");
  Serial.println(ph, 2);
  return clampf(ph, 0.0f, 14.0f);
#else
  simPh = clampf(simPh + randomStep(0.04f) + (7.3f - simPh) * 0.05f, 6.0f, 9.0f);
  return simPh;
#endif
}

float readTurbidez() {
  simTurbidez = clampf(simTurbidez + randomStep(0.08f) + (0.7f - simTurbidez) * 0.1f, 0.1f, 8.0f);
  return simTurbidez;
}

float readCloro() {
  float target = plcDosing >= 0 ? plcDosing * 0.02f : 0.8f;
  simCloro = clampf(simCloro + randomStep(0.03f) + (target - simCloro) * 0.15f, 0.0f, 3.0f);
  return simCloro;
}

float readTemperature() {
#if SIMULATE_TEMPERATURA
  simTemperatura = clampf(simTemperatura + randomStep(0.1f) + (14.0f - simTemperatura) * 0.05f, 8.0f, 22.0f);
  return simTemperatura;
#else
  // TODO DS18B20: agregar librerías OneWire + DallasTemperature y leer aquí.
  return NAN;
#endif
}

float readOxigeno() {
  simOxigeno = clampf(simOxigeno + randomStep(0.08f) + (8.0f - simOxigeno) * 0.05f, 5.5f, 10.0f);
  return simOxigeno;
}

void readAndPublishAll() {
  publishTelemetry("ph", readPh());
#if SIMULATE_TURBIDEZ
  publishTelemetry("turbidez", readTurbidez());
#endif
#if SIMULATE_CLORO
  publishTelemetry("cloro_libre", readCloro());
#endif
  float t = readTemperature();
  if (!isnan(t)) {
    publishTelemetry("temperatura", t);
  }
#if SIMULATE_OXIGENO
  publishTelemetry("oxigeno_disuelto", readOxigeno());
#endif
}

// ------------------------------------------------------------------ Main
void setup() {
  Serial.begin(115200);
  delay(200);
  randomSeed(esp_random());

  pinMode(ALARM_PIN, OUTPUT);
  digitalWrite(ALARM_PIN, LOW);
  analogSetPinAttenuation(PH_PIN, ADC_11db);  // rango ~0-3.1 V

  setupWiFi();
  configTime(GMT_OFFSET_SEC, DST_OFFSET_SEC, NTP_SERVER);

  snprintf(alarmTopic, sizeof(alarmTopic), "%s/%s/alarma", MQTT_TOPIC_PREFIX, PLC_DEVICE_SLUG);
  snprintf(dosingTopic, sizeof(dosingTopic), "%s/%s/dosificador_cloro", MQTT_TOPIC_PREFIX, PLC_DEVICE_SLUG);

  mqttClient.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
  mqttClient.setCallback(handleMessage);
  mqttClient.setBufferSize(512);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    setupWiFi();
  }
  if (!mqttClient.connected()) {
    reconnectMQTT();
  }
  mqttClient.loop();

  unsigned long nowMs = millis();
  if (nowMs - lastPublishAt >= PUBLISH_INTERVAL_MS) {
    lastPublishAt = nowMs;
    readAndPublishAll();
  }
}
