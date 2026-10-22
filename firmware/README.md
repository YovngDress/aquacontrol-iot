# Firmware ESP32 — calidad de agua

Sketch `esp32_calidad_agua/` para el ESP32 que mide la calidad del agua y
la publica por MQTT según el contrato del [README raíz](../README.md).

- **pH: sensor real** (analógico) con calibración de 2 puntos.
- Turbiedad, cloro libre, temperatura y oxígeno disuelto: **simulados** en
  el propio ESP32 (cada uno se puede reemplazar por un sensor real
  cambiando su `SIMULATE_*` a 0 y completando su función `read...()`).
- El cloro simulado responde al **dosificador del PLC** y el pin
  `ALARM_PIN` (LED integrado) se enciende con la **alarma del PLC**: así se
  ve en la maqueta la interacción PLC → ESP32.

## Requisitos

- Arduino IDE 2.x con placas ESP32 (Boards Manager → `esp32` by Espressif).
- Librerías: **WiFiManager** (tzapu), **PubSubClient** (Nick O'Leary),
  **ArduinoJson** (Benoit Blanchon, **v6.x**).

## Conexión de la sonda de pH (módulo PH-4502C)

| Módulo | ESP32 |
|---|---|
| V+ | 5V (VIN) |
| G | GND |
| Po | GPIO 34 **a través de un divisor** (Po → 10 kΩ → GPIO34 → 20 kΩ → GND) |

El módulo puede entregar hasta 5 V y el ADC del ESP32 admite 3.3 V: el
divisor es obligatorio. Usa un pin ADC1 (32–39), que funciona con el WiFi
encendido. Si usan el sensor de DFRobot (salida 0–3 V) se conecta directo.

## Configuración

```
copy esp32_calidad_agua\config.example.h esp32_calidad_agua\config.h
```

En `config.h` completa:

- `DEVICE_SLUG` / `DEVICE_TOKEN`: los del dispositivo `esp32-calidad-01`
  (Dispositivos → Aprovisionamiento, o la línea `#define DEVICE_TOKEN` que
  imprime `seed_demo_data`).
- `MQTT_BROKER_HOST` / `PORT`: IP del PC con Docker en tu red (no
  `localhost`) y `MOSQUITTO_HOST_PORT`; o el TCP Proxy de Railway.
- `MQTT_USERNAME` / `MQTT_PASSWORD` / `MQTT_TOPIC_PREFIX`: iguales al `.env`.

`config.h` está en `.gitignore`: no se sube el token al repo.

## Calibración del pH

1. Flashea el sketch y abre el Monitor Serie (115200 baud).
2. Sumerge la sonda en buffer **pH 7**, espera 1 min y anota el valor
   `pH raw mV=...` → `PH_MV_AT_7`.
3. Enjuaga, sumerge en buffer **pH 4** y anota → `PH_MV_AT_4`.
4. Vuelve a flashear. El pH se calcula con la recta entre esos dos puntos.

## Primer arranque

Si `WIFI_SSID` está vacío, el ESP32 levanta el AP `DEVICE_AP_TAG`:
conéctate desde el celular y elige la red WiFi en el portal. Luego conecta
a MQTT y publica cada `PUBLISH_INTERVAL_MS` en
`planta/esp32-calidad-01/<variable>`.

## Diagnóstico

- **No aparecen lecturas**: revisa el Monitor Serie (¿WiFi? ¿MQTT `rc=...`?)
  y en el Admin `UnregisteredReading` (slug o token incorrecto).
- **`rc=5`**: usuario/contraseña MQTT incorrectos.
- **El pH marca 0 o 14 fijo**: revisa el divisor y la calibración.
- Espiar el tráfico: `mosquitto_sub -h <broker> -p <puerto> -u planta -P <pass> -t "planta/#" -v`
