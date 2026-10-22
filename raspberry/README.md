# Raspberry Pi — gateway Modbus TCP ↔ MQTT y PLC simulado

La Raspberry Pi es el puente entre el **PLC** (que habla Modbus TCP) y la
plataforma (que habla MQTT):

```
PLC (real o plc_simulado.py) ◀── Modbus TCP ──▶ gateway.py ◀── MQTT ──▶ Mosquitto ──▶ Django
```

- `plc_simulado.py`: PLC simulado (servidor Modbus TCP) con la lógica de la
  planta. Lo usamos porque no tenemos PLC físico.
- `gateway.py`: lee el PLC y publica sus variables, aplica los comandos del
  dashboard, reenvía al PLC el pH/cloro/turbiedad del ESP32 y publica el
  estado de la propia Raspberry Pi (temperatura y uso de CPU, enlace con el PLC).

## Mapa Modbus del PLC (unidad 1, direcciones base 0)

| Tipo | Dir. | Variable | Escala | Acceso |
|---|---|---|---|---|
| Coil | 0 | `bomba` | 1 = ON | L/E |
| Coil | 1 | `valvula` | 1 = abierta | L/E |
| Coil | 2 | `modo_auto` | 1 = automático | L/E |
| Coil | 3 | `alarma` | 1 = activa | L |
| Holding | 0 | `nivel_estanque` | % ×10 | L |
| Holding | 1 | `caudal` | L/min ×10 | L |
| Holding | 2 | `presion` | bar ×100 | L |
| Holding | 3 | `dosificador_cloro` | % | L/E |
| Holding | 10 | pH (desde ESP32) | ×100 | E (gateway) |
| Holding | 11 | cloro libre (desde ESP32) | ×100 | E (gateway) |
| Holding | 12 | turbiedad (desde ESP32) | ×100 | E (gateway) |
| Holding | 13 | heartbeat | contador | L |

**Lógica del PLC** (ciclo de 0.5 s): en automático, bomba ON con nivel
< 30 % y OFF con > 90 %, y dosificador regulado para ~0.8 mg/L de cloro;
cierra la válvula si el nivel baja de 5 %; alarma con nivel < 10 % o > 97 %
o pH/cloro/turbiedad fuera de NCh 409. En modo manual, bomba y dosificador
quedan como los deje el operador desde el dashboard.

Si consiguen un **PLC real** (S7-1200, LOGO! 8, Delta, Schneider M221, ...)
con Modbus TCP: programen esa misma tabla y lógica en el PLC y en
`config.env` pongan `PLC_HOST=<IP del PLC>` y `PLC_PORT=502`. Nada más cambia.

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
