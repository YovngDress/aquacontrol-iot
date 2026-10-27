# Raspberry Pi — gateway Modbus TCP ↔ MQTT y PLC simulado

La Raspberry Pi es el puente entre el **PLC** (que habla Modbus TCP) y la
plataforma (que habla MQTT):

```
S7-1200 (o plc_simulado.py) ◀── Modbus TCP ──▶ gateway.py ◀── MQTT ──▶ Mosquitto ──▶ Django
```

- `plc_simulado.py`: réplica en Python del programa del S7-1200 (mismo mapa
  y misma lógica). Sirve para probar sin el PLC; con el S7-1200 conectado no se usa.
- `gateway.py`: lee el PLC y publica sus variables, aplica los comandos del
  dashboard, reenvía al PLC el pH/cloro/turbiedad del ESP32 y publica el
  estado de la propia Raspberry Pi (temperatura y uso de CPU, enlace con el PLC).

## Mapa Modbus del PLC

Solo se usan **holding registers** (FC3 lectura / FC6 escritura), en base 0.
En el S7-1200 son `"ModbusData".HR[n]`. El programa está en [`../plc/`](../plc/README.md).

| Dir. | Variable | Escala | Quién escribe |
|---|---|---|---|
| 0 | `nivel_estanque` | % ×10 | PLC |
| 1 | `caudal` | L/min ×10 | PLC |
| 2 | `presion` | bar ×100 | PLC |
| 3 | `dosificador_cloro` | % | PLC |
| 4 | `bomba` | 0/1 | PLC |
| 5 | `valvula` | 0/1 | PLC |
| 6 | `modo_auto` | 0/1 | PLC |
| 7 | `alarma` | 0/1 | PLC |
| 8 | heartbeat | contador | PLC |
| 10 | comando bomba | 0/1 | gateway (dashboard) |
| 11 | comando válvula | 0/1 | gateway (dashboard) |
| 12 | comando modo automático | 0/1 | gateway (dashboard) |
| 13 | comando dosificador | % | gateway (dashboard) |
| 20 | pH | ×100 | gateway (ESP32) |
| 21 | cloro libre | ×100 | gateway (ESP32) |
| 22 | turbiedad | ×100 | gateway (ESP32) |

Hay registros de **estado** y de **comando** separados porque en el S7-1200
las coils Modbus son las salidas físicas `%Q` y el programa las sobrescribe
en cada ciclo. Así el PLC decide qué orden aplica: en automático ignora la
bomba y el dosificador manuales, y al pasar a manual no hay salto, porque el
comando ya vale lo mismo que el estado.

**Lógica** (OB30 cada 0.5 s en el PLC; igual en `plc_simulado.py`): en
automático, bomba ON con nivel < 30 % y OFF con > 90 %, y dosificador
regulado para ~0.8 mg/L de cloro; nivel < 5 % cierra la válvula; alarma con
nivel < 10 % o > 97 %, o pH / cloro / turbiedad fuera de NCh 409.

**Con el S7-1200 real:** sigue [`plc/README.md`](../plc/README.md) (TIA
Portal, red Ethernet Raspberry ↔ PLC) y en `config.env` pon
`PLC_HOST=192.168.0.1` y `PLC_PORT=502`. `plc_simulado.py` queda solo para
pruebas sin el PLC.

## Instalación en la Raspberry Pi (Raspberry Pi OS Bookworm)

```bash
git clone https://github.com/<su-usuario>/<su-repo>.git ~/aquacontrol
cd ~/aquacontrol/raspberry
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.env config.env
nano config.env        # broker MQTT, contraseña y tokens PLC_DEVICE_TOKEN / RPI_DEVICE_TOKEN
```

Probar a mano (dos terminales):

```bash
.venv/bin/python plc_simulado.py
.venv/bin/python gateway.py
```

Deben verse `MQTT conectado` y `Enlace con PLC: OK`, y en el dashboard las
tarjetas del PLC y de la Raspberry Pi "en línea".

### Arranque automático (systemd)

Los `.service` asumen usuario `pi` y el repo en `/home/pi/aquacontrol`
(edita `User=` y las rutas si es distinto).

```bash
sudo cp systemd/plc-simulado.service systemd/gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now plc-simulado gateway
journalctl -u gateway -f      # ver el log
```

## Diagnóstico

- `Conexión MQTT rechazada (rc=5)`: usuario/contraseña MQTT.
- `Enlace con PLC: PERDIDO`: el PLC no responde en `PLC_HOST:PLC_PORT`, o
  responde pero su heartbeat dejó de cambiar (programa detenido).
- `Comando ... rechazado: token inválido`: `PLC_DEVICE_TOKEN` no coincide
  con el del dispositivo `plc-planta-01` en la plataforma.
- Probar el PLC sin gateway (desde la Pi):
  `.venv/bin/python -c "from pymodbus.client import ModbusTcpClient as C; c=C('127.0.0.1',port=5020); c.connect(); print(c.read_holding_registers(0,4,slave=1).registers)"`
