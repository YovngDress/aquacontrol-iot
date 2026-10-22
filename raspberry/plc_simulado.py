"""PLC simulado de la planta de agua potable (servidor Modbus TCP).

Reemplaza a un PLC físico mientras no tengamos uno: expone exactamente el
mismo mapa de memoria Modbus que tendría el PLC real, y ejecuta un "ciclo de
scan" con la lógica de control de la planta. Si después consiguen un PLC
(Siemens S7-1200, Delta, Schneider M221, LOGO! 8, ...) basta con programar
este mismo mapa en el PLC y apuntar el gateway a su IP: ni el gateway ni la
plataforma Django cambian.

Uso (en la Raspberry Pi):
    python plc_simulado.py                 # escucha en 0.0.0.0:5020
    python plc_simulado.py --port 502      # puerto Modbus estándar (requiere sudo)

Mapa Modbus (direcciones base 0, unidad/slave 1):

  Coils (FC1 lectura / FC5 escritura)
    0  bomba              1 = en marcha
    1  valvula            1 = abierta (entrega agua a la red)
    2  modo_auto          1 = el PLC controla bomba y dosificador
    3  alarma             1 = condición fuera de rango (solo lectura)

  Holding registers (FC3 lectura / FC6 escritura)
    0  nivel_estanque     % x10       (ej. 655 = 65.5 %)
    1  caudal             L/min x10
    2  presion            bar x100
    3  dosificador_cloro  % (0-100)
    10 ph_in              pH x100     (lo escribe el gateway desde el ESP32)
    11 cloro_in           mg/L x100   (idem)
    12 turbidez_in        NTU x100    (idem)
    13 heartbeat          contador de ciclos (el gateway detecta si el PLC se colgó)

Lógica de control (cada ciclo de 0.5 s):
  - Modo automático: bomba ON si nivel < 30 %, OFF si nivel > 90 % (histéresis);
    el dosificador se ajusta para mantener el cloro libre cerca de 0.8 mg/L.
  - Modo manual: bomba y dosificador quedan como los deje el operador.
  - Protección: si el nivel baja de 5 % se cierra la válvula de salida.
  - Alarma: nivel < 10 % o > 97 %, o pH / cloro / turbiedad fuera de NCh 409.
"""
import argparse
import asyncio
import logging
import random

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PLC] %(message)s")
log = logging.getLogger("plc")

# --- Mapa de memoria ---
CO_BOMBA, CO_VALVULA, CO_MODO_AUTO, CO_ALARMA = 0, 1, 2, 3
HR_NIVEL, HR_CAUDAL, HR_PRESION, HR_DOSIF = 0, 1, 2, 3
HR_PH_IN, HR_CLORO_IN, HR_TURB_IN, HR_HEARTBEAT = 10, 11, 12, 13

FC_COILS, FC_HOLDING = 1, 3

# --- Parámetros del proceso ---
SCAN_SECONDS = 0.5
TANK_LITERS = 2000.0
PUMP_FLOW_LPM = 150.0
DEMAND_LPM = 80.0
LEVEL_START_PUMP, LEVEL_STOP_PUMP = 30.0, 90.0
CLORO_SETPOINT = 0.8


class Plant:
    def __init__(self, store):
        self.store = store
        self.level = 60.0
        self.heartbeat = 0
        self.co(CO_BOMBA, 1)
        self.co(CO_VALVULA, 1)
        self.co(CO_MODO_AUTO, 1)
        self.hr(HR_DOSIF, 40)

    # Helpers de lectura/escritura sobre la memoria Modbus
    def co(self, addr, value=None):
        if value is None:
            return int(self.store.getValues(FC_COILS, addr, 1)[0])
        self.store.setValues(FC_COILS, addr, [bool(value)])

    def hr(self, addr, value=None):
        if value is None:
            return int(self.store.getValues(FC_HOLDING, addr, 1)[0])
        self.store.setValues(FC_HOLDING, addr, [int(max(0, min(65535, round(value))))])

    def scan(self):
        dt_min = SCAN_SECONDS / 60.0
        auto = self.co(CO_MODO_AUTO)
        bomba = self.co(CO_BOMBA)
        valvula = self.co(CO_VALVULA)
        dosif = self.hr(HR_DOSIF)

        ph = self.hr(HR_PH_IN) / 100.0
        cloro = self.hr(HR_CLORO_IN) / 100.0
        turb = self.hr(HR_TURB_IN) / 100.0

        # 1) Control automático
        if auto:
            if self.level < LEVEL_START_PUMP:
                bomba = 1
            elif self.level > LEVEL_STOP_PUMP:
                bomba = 0
            if cloro > 0:  # solo si el gateway ya nos está enviando el cloro medido
                dosif += (CLORO_SETPOINT - cloro) * 10 * SCAN_SECONDS
                dosif = max(0.0, min(100.0, dosif))

        # 2) Protección de nivel bajo
        if self.level < 5.0:
            valvula = 0

        # 3) Dinámica del estanque
        inflow = PUMP_FLOW_LPM if bomba else 0.0
        outflow = (DEMAND_LPM + random.uniform(-8, 8)) if valvula and self.level > 0 else 0.0
        self.level += (inflow - outflow) * dt_min / TANK_LITERS * 100.0
        self.level = max(0.0, min(100.0, self.level))

        presion = 0.6 + self.level * 0.02 + (0.8 if bomba else 0.0) - (0.3 if valvula else 0.0)
        presion += random.uniform(-0.05, 0.05)

        # 4) Alarmas
        alarma = self.level < 10 or self.level > 97
        if ph > 0 and not 6.5 <= ph <= 8.5:
            alarma = True
        if cloro > 0 and not 0.2 <= cloro <= 2.0:
            alarma = True
        if turb > 2.0:
            alarma = True

        # 5) Escritura de salidas
        self.co(CO_BOMBA, bomba)
        self.co(CO_VALVULA, valvula)
        self.co(CO_ALARMA, alarma)
        self.hr(HR_NIVEL, self.level * 10)
        self.hr(HR_CAUDAL, outflow * 10)
        self.hr(HR_PRESION, max(0.0, presion) * 100)
        self.hr(HR_DOSIF, dosif)
        self.heartbeat = (self.heartbeat + 1) % 65536
        self.hr(HR_HEARTBEAT, self.heartbeat)


async def scan_loop(plant):
    last_log = 0
    while True:
        plant.scan()
        last_log += 1
        if last_log >= 20:  # cada ~10 s
            last_log = 0
            log.info(
                "nivel=%.1f%% bomba=%d valvula=%d auto=%d dosif=%d%% alarma=%d",
                plant.level, plant.co(CO_BOMBA), plant.co(CO_VALVULA), plant.co(CO_MODO_AUTO),
                plant.hr(HR_DOSIF), plant.co(CO_ALARMA),
            )
        await asyncio.sleep(SCAN_SECONDS)


async def main(host, port):
    store = ModbusSlaveContext(
        co=ModbusSequentialDataBlock(0, [False] * 32),
        di=ModbusSequentialDataBlock(0, [False] * 32),
        hr=ModbusSequentialDataBlock(0, [0] * 32),
        ir=ModbusSequentialDataBlock(0, [0] * 32),
        zero_mode=True,  # dirección 0 = primer registro (igual que en la tabla de arriba)
    )
    context = ModbusServerContext(slaves=store, single=True)
    plant = Plant(store)
    asyncio.create_task(scan_loop(plant))
    log.info("PLC simulado escuchando Modbus TCP en %s:%s", host, port)
    await StartAsyncTcpServer(context=context, address=(host, port))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PLC simulado (Modbus TCP) de la planta de agua potable")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5020)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port))
