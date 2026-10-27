"""PLC simulado de la planta de agua potable (servidor Modbus TCP).

Réplica en Python del programa del Siemens S7-1200 (plc/AquaControl_S7-1200.scl):
mismo mapa de memoria Modbus y misma lógica. Sirve para probar todo sin el
PLC físico (docker compose, o en la Raspberry Pi). Con el PLC real conectado
este archivo no se usa: el gateway apunta a la IP del PLC.

Uso:
    python plc_simulado.py                 # escucha en 0.0.0.0:5020
    python plc_simulado.py --port 502      # puerto Modbus estándar (requiere sudo)

Mapa Modbus — solo holding registers (FC3 lectura / FC6 escritura), base 0.
En el S7-1200 es el arreglo "ModbusData".HR[0..29] (MB_HOLD_REG del MB_SERVER);
la dirección N corresponde al registro Modbus 4000(N+1).

  Estado (lo escribe el PLC, el gateway lo lee)
    0  nivel_estanque     % x10       (ej. 655 = 65.5 %)
    1  caudal             L/min x10
    2  presion            bar x100
    3  dosificador_cloro  %
    4  bomba              0/1
    5  valvula            0/1
    6  modo_auto          0/1
    7  alarma             0/1
    8  heartbeat          contador de ciclos (detecta PLC detenido)

  Comandos (los escribe el gateway desde el dashboard, el PLC los lee)
    10 cmd_bomba          0/1   (solo tiene efecto en modo manual)
    11 cmd_valvula        0/1
    12 cmd_modo_auto      0/1
    13 cmd_dosificador    %     (solo tiene efecto en modo manual)

  Calidad del agua (la escribe el gateway desde el ESP32)
    20 ph                 x100
    21 cloro_libre        mg/L x100
    22 turbidez           NTU x100

¿Por qué no usar coils? En el S7-1200 las coils del MB_SERVER son las salidas
físicas %Q, y el programa las sobrescribe en cada ciclo, así que una orden
por Modbus se perdería. Con registros separados de estado y de comando el
PLC decide qué aplica (por ejemplo, ignora la bomba manual en automático).

Lógica (ciclo de 0.5 s, igual a OB30 en el PLC):
  - Automático: bomba ON si nivel < 30 %, OFF si nivel > 90 %; dosificador
    regulado para ~0.8 mg/L de cloro. Los comandos de bomba y dosificador se
    igualan al estado actual (transferencia sin salto al pasar a manual).
  - Manual: bomba y dosificador = comandos del operador.
  - Protección: nivel < 5 % cierra la válvula.
  - Alarma: nivel < 10 % o > 97 %, o pH / cloro / turbiedad fuera de NCh 409.
"""
import argparse
import asyncio
import logging
import math

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PLC] %(message)s")
log = logging.getLogger("plc")

# --- Mapa de memoria (holding registers) ---
HR_NIVEL, HR_CAUDAL, HR_PRESION, HR_DOSIF = 0, 1, 2, 3
HR_BOMBA, HR_VALVULA, HR_AUTO, HR_ALARMA, HR_HEARTBEAT = 4, 5, 6, 7, 8
HR_CMD_BOMBA, HR_CMD_VALVULA, HR_CMD_AUTO, HR_CMD_DOSIF = 10, 11, 12, 13
HR_PH, HR_CLORO, HR_TURB = 20, 21, 22
HR_SIZE = 30

FC_HOLDING = 3

# --- Parámetros del proceso (los mismos del programa SCL) ---
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
        self.dosif = 40.0
        self.bomba = True
        self.tick = 0
        self.heartbeat = 0
        for addr, value in ((HR_CMD_BOMBA, 1), (HR_CMD_VALVULA, 1), (HR_CMD_AUTO, 1), (HR_CMD_DOSIF, 40)):
            self.hr(addr, value)

    def hr(self, addr, value=None):
        if value is None:
            return int(self.store.getValues(FC_HOLDING, addr, 1)[0])
        self.store.setValues(FC_HOLDING, addr, [int(max(0, min(65535, round(value))))])

    def scan(self):
        # 1) Leer comandos y calidad del agua
        auto = self.hr(HR_CMD_AUTO) != 0
        valvula = self.hr(HR_CMD_VALVULA) != 0
        ph = self.hr(HR_PH) / 100.0
        cloro = self.hr(HR_CLORO) / 100.0
        turb = self.hr(HR_TURB) / 100.0

        # 2) Control
        if auto:
            if self.level < LEVEL_START_PUMP:
                self.bomba = True
            elif self.level > LEVEL_STOP_PUMP:
                self.bomba = False
            if cloro > 0:
                self.dosif += (CLORO_SETPOINT - cloro) * 10 * SCAN_SECONDS
                self.dosif = max(0.0, min(100.0, self.dosif))
            # transferencia sin salto: el comando sigue al estado
            self.hr(HR_CMD_BOMBA, 1 if self.bomba else 0)
            self.hr(HR_CMD_DOSIF, self.dosif)
        else:
            self.bomba = self.hr(HR_CMD_BOMBA) != 0
            self.dosif = float(max(0, min(100, self.hr(HR_CMD_DOSIF))))

        # 3) Protección de nivel bajo
        if self.level < 5.0:
            valvula = False
            self.hr(HR_CMD_VALVULA, 0)

        # 4) Modelo del estanque (demanda variable, como en el PLC)
        self.tick = (self.tick + 1) % 30000
        demanda = DEMAND_LPM + 8.0 * math.sin(self.tick * 0.05)
        inflow = PUMP_FLOW_LPM if self.bomba else 0.0
        outflow = demanda if valvula and self.level > 0 else 0.0
        self.level += (inflow - outflow) * (SCAN_SECONDS / 60.0) / TANK_LITERS * 100.0
        self.level = max(0.0, min(100.0, self.level))
        presion = 0.6 + self.level * 0.02 + (0.8 if self.bomba else 0.0) - (0.3 if valvula else 0.0)

        # 5) Alarmas
        alarma = self.level < 10 or self.level > 97
        if ph > 0 and not 6.5 <= ph <= 8.5:
            alarma = True
        if cloro > 0 and not 0.2 <= cloro <= 2.0:
            alarma = True
        if turb > 2.0:
            alarma = True

        # 6) Escribir estado
        self.hr(HR_NIVEL, self.level * 10)
        self.hr(HR_CAUDAL, outflow * 10)
        self.hr(HR_PRESION, max(0.0, presion) * 100)
        self.hr(HR_DOSIF, self.dosif)
        self.hr(HR_BOMBA, 1 if self.bomba else 0)
        self.hr(HR_VALVULA, 1 if valvula else 0)
        self.hr(HR_AUTO, 1 if auto else 0)
        self.hr(HR_ALARMA, 1 if alarma else 0)
        self.heartbeat = (self.heartbeat + 1) % 32768
        self.hr(HR_HEARTBEAT, self.heartbeat)


async def scan_loop(plant):
    count = 0
    while True:
        plant.scan()
        count += 1
        if count % 20 == 0:  # cada ~10 s
            log.info(
                "nivel=%.1f%% bomba=%d valvula=%d auto=%d dosif=%d%% alarma=%d",
                plant.level, plant.hr(HR_BOMBA), plant.hr(HR_VALVULA), plant.hr(HR_AUTO),
                plant.hr(HR_DOSIF), plant.hr(HR_ALARMA),
            )
        await asyncio.sleep(SCAN_SECONDS)


async def main(host, port):
    store = ModbusSlaveContext(
        hr=ModbusSequentialDataBlock(0, [0] * HR_SIZE),
        zero_mode=True,  # dirección 0 = HR[0], igual que en el S7-1200
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
