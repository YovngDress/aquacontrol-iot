# PLC Siemens S7-1200 — programa en TIA Portal

El S7-1200 controla la planta y **simula el nivel del estanque** dentro de su
propio programa. La Raspberry Pi lo lee y le escribe por **Modbus TCP**
(puerto 502) con `raspberry/gateway.py`.

```
 Dashboard ──MQTT── Raspberry Pi (gateway.py) ──Modbus TCP :502── S7-1200
                                                                  ├─ MB_SERVER (OB1)     ← "ModbusData".HR[0..29]
                                                                  └─ FB_Planta (OB30, 500 ms)
                                                                       ├─ simula nivel, caudal, presión
                                                                       ├─ bomba por nivel, dosificador de cloro
                                                                       ├─ alarma NCh 409
                                                                       └─ Q0.0 bomba · Q0.1 válvula · Q0.2 alarma · Q0.3 auto
```

Archivo: [`AquaControl_S7-1200.scl`](AquaControl_S7-1200.scl). Contiene el DB
`ModbusData` y el FB `FB_Planta`. El mapa Modbus está en el encabezado del
archivo y es idéntico al de `raspberry/plc_simulado.py`.

## 1. Configuración del dispositivo

1. **Configuración de dispositivos** → CPU → **Interfaz PROFINET** →
   Direcciones Ethernet: IP **192.168.0.1**, máscara **255.255.255.0**.
2. Tu PC con TIA Portal debe estar en la misma red (ej. 192.168.0.20) para
   cargar el programa.

No hace falta habilitar "Permitir acceso vía PUT/GET": eso es para S7 y aquí
se usa Modbus.

## 2. Importar el DB y el FB

1. Árbol del proyecto → `PLC_1` → **Fuentes externas** → **Agregar nuevo
   archivo externo** → elige `AquaControl_S7-1200.scl`.
2. Clic derecho sobre el archivo → **Generar bloques a partir de la fuente**.
3. Deben aparecer en *Bloques de programa*: **ModbusData [DB]** y **FB_Planta [FB]**.

Si TIA marca un error al generar, copia el mensaje (línea y texto) y me lo
mandas.

## 3. OB30: la lógica cada 500 ms

1. **Agregar nuevo bloque** → **Bloque de organización** → **Cyclic interrupt**
   → tiempo de ciclo **500000 µs** (500 ms). Lenguaje: **SCL**.
2. Escribe en el OB30:

   ```pascal
   "FB_Planta_DB"(Q_Bomba   => %Q0.0,
                  Q_Valvula => %Q0.1,
                  Q_Alarma  => %Q0.2,
                  Q_Auto    => %Q0.3);
   ```
   Al compilar, TIA pregunta si crear el DB de instancia `FB_Planta_DB`: acepta.

   Alternativa en KOP: arrastra `FB_Planta` a una red y conecta las 4 salidas a `%Q0.0`–`%Q0.3`.

> El modelo usa 0.5 s por ciclo. Si cambias el tiempo del OB30, cambia
> también el `0.5` en la fórmula del nivel.

## 4. OB1: servidor Modbus TCP (MB_SERVER)

1. Abre **Main [OB1]** (KOP/FUP).
2. Instrucciones → **Comunicación** → **Otros** → **MODBUS TCP** →
   arrastra **MB_SERVER** a una red y acepta el DB de instancia (`MB_SERVER_DB`).
3. Parámetros:

   | Parámetro | Valor |
   |---|---|
   | `DISCONNECT` | `FALSE` |
   | `MB_HOLD_REG` | `"ModbusData".HR` |
   | `CONNECT` | lo crea el paso 4 |

4. Selecciona el bloque → ventana **Propiedades** → **Configuración** →
   **Parámetros de conexión**:
   - Interlocutor: **Sin especificar**.
   - Datos de conexión: **<nuevo>**. TIA crea un DB `TCON_IP_v4` y lo conecta a `CONNECT`.
   - Tipo: **TCP**, ID de conexión: **1**.
   - Puerto local: **502**.
   - Establecimiento activo: del lado del **interlocutor**, no del PLC.
5. Opcional: conecta `ERROR` y `STATUS` a marcas (ej. `%M10.0`, `%MW12`) para diagnosticar.

> **Versiones antiguas de la instrucción:** si tu MB_SERVER muestra el
> parámetro `IP_PORT` en vez de `CONNECT` (V4 o anterior), pon `IP_PORT := 502`
> y `MB_HOLD_REG := P#DB<n>.DBX0.0 WORD 30`, donde `<n>` es el número del DB
> `ModbusData`.

`ModbusData` **debe quedar con acceso no optimizado**. El archivo ya lo crea
así; puedes revisarlo en Propiedades del DB → Atributos.

## 5. Compilar, cargar y probar

1. **Compilar** (hardware y software) → **Cargar en dispositivo** → poner la CPU en **RUN**.
2. Crea una **tabla de observación** con `"ModbusData".HR[0]`, `HR[4]`,
   `HR[8]` y `HR[12]` y obsérvala online:
   - `HR[8]` (heartbeat) debe ir subiendo.
   - `HR[0]` (nivel ×10) se mueve y `HR[4]` (bomba) cambia al cruzar 30 % / 90 %.
3. Para probar el modo manual desde TIA: forzar `HR[12] := 0` y luego `HR[10] := 0` apaga la bomba (LED de Q0.0).

PLCSIM **no** sirve para probar Modbus TCP con un S7-1200: la prueba
Raspberry ↔ PLC necesita el PLC físico. La lógica (OB30) sí se puede revisar en PLCSIM.

## 6. Red Raspberry Pi ↔ PLC

Conecta la Raspberry Pi al PLC con un cable Ethernet, directo o mediante un
switch. La Raspberry sigue usando **WiFi** para llegar al broker MQTT o a internet.

En la Raspberry Pi (Raspberry Pi OS Bookworm) dale una IP fija al puerto
Ethernet **sin** ruta por defecto, para que internet siga saliendo por WiFi:

```bash
nmcli con show                       # busca el nombre de la conexión cableada, ej. "Wired connection 1"
sudo nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 192.168.0.10/24 ipv4.never-default yes
sudo nmcli con up "Wired connection 1"
ping 192.168.0.1                     # debe responder el PLC
```

Prueba de lectura Modbus desde la Raspberry (en `raspberry/`):

```bash
.venv/bin/python -c "from pymodbus.client import ModbusTcpClient as C; c=C('192.168.0.1',port=502); print(c.connect(), c.read_holding_registers(0,9,slave=1).registers)"
```

Debe imprimir `True [nivel, caudal, presión, dosif, bomba, válvula, auto, alarma, heartbeat]`.

## 7. Apuntar el gateway al PLC real

En `raspberry/config.env`:

```
PLC_HOST=192.168.0.1
PLC_PORT=502
PLC_UNIT_ID=1
```

Y desactiva el PLC simulado:

```bash
sudo systemctl disable --now plc-simulado
sudo systemctl restart gateway
journalctl -u gateway -f        # debe decir "Enlace con PLC: OK"
```

El dashboard no cambia: las variables del PLC llegan con los mismos nombres.

## Diagnóstico

| Síntoma | Causa probable |
|---|---|
| `ping 192.168.0.1` no responde | IP de la Raspberry fuera de la red 192.168.0.x, o cable o switch |
| Ping OK pero `connect()` da `False` | MB_SERVER no está en OB1, puerto distinto de 502, o CPU en STOP |
| Lee pero `Enlace con PLC: PERDIDO` | `HR[8]` no cambia: el OB30 no se ejecuta o la CPU está en STOP |
| `ERROR` del MB_SERVER en TRUE | Busca el código de `STATUS` en la ayuda de TIA (F1 sobre MB_SERVER); lo típico es `MB_HOLD_REG` mal apuntado o `ModbusData` optimizado |
| El botón de bomba "se devuelve" | Es normal en **modo automático**: el PLC manda; pasa a manual primero |
