# Node-RED — PLC simulado + gateway + HMI

`flows.json` es un flujo de Node-RED que hace tres cosas:

1. **Simula el PLC** de la planta, con un ciclo de scan cada 500 ms. Controla
   la bomba por nivel (ON < 30 %, OFF > 90 %), regula el dosificador de cloro
   para mantener ~0.8 mg/L, cierra la válvula si el nivel baja de 5 % y
   activa la alarma si hay nivel extremo o calidad fuera de NCh 409.
2. **Hace de gateway** hacia la plataforma Django por MQTT:
   - Publica las variables del PLC como `plc-planta-01` y la telemetría de la
     Raspberry Pi como `rpi-gateway-01`.
   - Recibe los comandos del dashboard (`planta/plc-planta-01/<variable>/set`)
     y valida el token.
   - Le pasa al PLC el pH, el cloro y la turbiedad que publica el ESP32.
3. **Muestra un HMI propio** en Dashboard 2.0, en `/dashboard/plc`, con
   medidores de nivel, caudal y presión, gráfico de nivel, interruptores de
   modo automático, bomba y válvula, slider del dosificador, alarma y calidad
   recibida del ESP32.

```
 ESP32 ──MQTT──┐                                   ┌── Django (dashboard, BD)
               ├── Mosquitto ── mqtt_listener ─────┘
 Node-RED ─────┘   (en la Raspberry Pi)
   ├─ PLC simulado (OB30, 500 ms)
   ├─ gateway: estado → plataforma, comandos /set → PLC
   └─ HMI Dashboard 2.0  http://<ip>:1880/dashboard/plc
```

Para la plataforma Django, el PLC simulado es un dispositivo más: las
variables, los gadgets del dashboard y el panel NCh 409 no cambian.

## 1. Instalar Node-RED

**En la Raspberry Pi** (Raspberry Pi OS / Ubuntu), con el instalador oficial:

```bash
bash <(curl -sL https://github.com/node-red/linux-installers/releases/latest/download/update-nodejs-and-nodered-deb)
sudo systemctl enable --now nodered
```

**En un PC con Windows**, si quieres probar sin la Raspberry, instala
Node.js LTS desde nodejs.org y luego, en CMD:

```
npm install -g --unsafe-perm node-red
node-red
```

Abre el editor en `http://<ip>:1880` (en el mismo equipo, `http://localhost:1880`).

## 2. Instalar Dashboard 2.0

Menú ☰ → **Manage palette** → pestaña **Install** → busca
`@flowfuse/node-red-dashboard` → **Install**.

## 3. Importar el flujo

Menú ☰ → **Import** → **select a file to import** → elige `nodered/flows.json`
→ **Import**. Aparece la pestaña **"PLC simulado (planta de agua)"**.

## 4. Configurar el broker MQTT

Doble clic en cualquier nodo MQTT (por ejemplo "→ plataforma") → lápiz junto
a **Server: Mosquitto plataforma**:

| Campo | Valor |
|---|---|
| Server | IP del equipo donde corre Mosquitto (`localhost` si está en la misma Raspberry; si la plataforma corre en Docker en tu PC, la IP de ese PC) |
| Port | `1883` (o el `MOSQUITTO_HOST_PORT` de tu `.env`) |
| Security → Username / Password | `MQTT_USERNAME` / `MQTT_PASSWORD` de tu `.env` |

## 5. Pegar los tokens

En la plataforma corre `python manage.py seed_demo_data` (con Docker:
`docker compose exec web python manage.py seed_demo_data`). Al final imprime
`PLC_TOKEN` y `RPI_TOKEN`.

En Node-RED, haz **doble clic en la pestaña** "PLC simulado (planta de agua)"
→ sección **Environment Variables**:

| Variable | Valor |
|---|---|
| `PLC_TOKEN` | token de `plc-planta-01` |
| `RPI_TOKEN` | token de `rpi-gateway-01` |
| `PREFIX`, `PLC_SLUG`, `RPI_SLUG`, `ESP32_SLUG` | dejar como están (`planta`, `plc-planta-01`, `rpi-gateway-01`, `esp32-calidad-01`) |
| `SIM_SPEED` | `1` = tiempo real; `10` acelera el llenado y vaciado del estanque para la demo |

> Si cambias `PREFIX`, cambia también el topic de los dos nodos "←" (mqtt in),
> que vienen como `planta/+/+/set` y `planta/+/+`.

## 6. Deploy y prueba

1. Botón rojo **Deploy**.
2. Bajo los nodos MQTT debe aparecer **connected**, y bajo "PLC simulado
   (OB30)" un estado verde, por ejemplo `nivel 61.2 % · bomba ON · AUTO`.
3. HMI: `http://<ip>:1880/dashboard/plc`.
4. Plataforma: en `/monitoring/`, las tarjetas **PLC** y **Raspberry Pi**
   pasan a "en línea" y el diagrama del proceso se anima.

Cómo se comportan los controles:
- Desde el dashboard de Django o desde el HMI de Node-RED puedes pasar a
  **modo manual** y ahí mover la bomba y el dosificador. En **modo
  automático** los controla el PLC: el interruptor de la bomba y el slider
  quedan deshabilitados en el HMI, y desde Django el botón vuelve a su estado real.
- La **válvula** se maneja en ambos modos. La protección la cierra sola si
  el nivel baja de 5 %.
- Un comando con token incorrecto se rechaza y aparece un aviso en la
  pestaña **Debug** de Node-RED.

## Modificar la lógica

Edita las funciones directamente en el editor de Node-RED (doble clic en
"PLC simulado (OB30)"), o edita `build_flow.py` y regenera el archivo:

```bash
python nodered/build_flow.py
```

## Inicio de sesión (usuario y contraseña)

Node-RED trae login propio, que se configura en `~/.node-red/settings.js`:

- `adminAuth` protege el **editor** (`:1880`) con una pantalla de inicio de sesión.
- `httpNodeAuth` protege el **HMI** (`:1880/dashboard`) con usuario y contraseña.

1. Genera el hash de la contraseña (pide la clave y devuelve un texto que empieza con `$2b$`):
   ```bash
   node-red admin hash-pw
   ```
2. Edita `nano ~/.node-red/settings.js`, busca `adminAuth` (viene comentado con `//`), descoméntalo y déjalo así:
   ```js
   adminAuth: {
       type: "credentials",
       users: [{
           username: "admin",
           password: "$2b$08$...hash...",
           permissions: "*"
       }]
   },
   httpNodeAuth: { user: "operador", pass: "$2b$08$...hash..." },
   ```
3. Reinicia Node-RED: `sudo systemctl restart nodered`.
