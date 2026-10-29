"""Genera nodered/flows.json (PLC simulado + gateway MQTT + HMI Dashboard 2.0).

Uso:  python nodered/build_flow.py
No hace falta correrlo para usar el flujo: flows.json ya viene generado.
Sirve para modificar la lógica de forma ordenada (el código de cada función
está aquí, legible) y regenerar el archivo.
"""
import json
from pathlib import Path

TAB = "tab_plc"

# ---------------------------------------------------------------- funciones

FN_SCAN = r"""
// =====================================================================
// PLC simulado — ciclo de scan (equivale al OB30 de un S7-1200, 500 ms)
// Estado en flow.plc, comandos en flow.cmd, calidad del agua en flow.calidad
// =====================================================================
const DT = 0.5;                                        // s por ciclo
const SPEED = Math.max(1, Number(env.get('SIM_SPEED')) || 1);   // acelera el estanque en la demo

let s = flow.get('plc');
let cmd = flow.get('cmd');
if (!s || !cmd) {                                       // primer ciclo
    s = { nivel: 60, dosif: 40, bomba: true, valvula: true, auto: true,
          alarma: false, caudal: 0, presion: 0, tick: 0, heartbeat: 0 };
    cmd = { bomba: 1, valvula: 1, auto: 1, dosif: 40 };
}
const q = flow.get('calidad') || { ph: 0, cloro: 0, turb: 0 };

// 1) Comandos del operador
s.auto = cmd.auto !== 0;
s.valvula = cmd.valvula !== 0;

// 2) Control automático / manual
if (s.auto) {
    if (s.nivel < 30) s.bomba = true;                   // histéresis 30 % / 90 %
    else if (s.nivel > 90) s.bomba = false;
    if (q.cloro > 0) {                                  // dosificador: cloro objetivo 0.8 mg/L
        s.dosif = Math.max(0, Math.min(100, s.dosif + (0.8 - q.cloro) * 10 * DT));
    }
    cmd.bomba = s.bomba ? 1 : 0;                        // transferencia sin salto a manual
    cmd.dosif = Math.round(s.dosif);
} else {
    s.bomba = cmd.bomba !== 0;
    s.dosif = Math.max(0, Math.min(100, cmd.dosif));
}

// 3) Protección: nivel muy bajo cierra la válvula
if (s.nivel < 5) { s.valvula = false; cmd.valvula = 0; }

// 4) Simulación del estanque de 2000 L
s.tick = (s.tick + 1) % 30000;
const demanda = 80 + 8 * Math.sin(s.tick * 0.05);      // L/min
const entrada = s.bomba ? 150 : 0;                      // L/min de la bomba
const salida = (s.valvula && s.nivel > 0) ? demanda : 0;
s.nivel += (entrada - salida) * (DT * SPEED / 60) / 2000 * 100;
s.nivel = Math.max(0, Math.min(100, s.nivel));
s.caudal = salida;
s.presion = Math.max(0, 0.6 + s.nivel * 0.02 + (s.bomba ? 0.8 : 0) - (s.valvula ? 0.3 : 0));

// 5) Alarma: nivel extremo o calidad fuera de NCh 409
let alarma = s.nivel < 10 || s.nivel > 97;
if (q.ph > 0 && (q.ph < 6.5 || q.ph > 8.5)) alarma = true;
if (q.cloro > 0 && (q.cloro < 0.2 || q.cloro > 2)) alarma = true;
if (q.turb > 2) alarma = true;
s.alarma = alarma;

s.heartbeat = (s.heartbeat + 1) % 32000;
flow.set('plc', s);
flow.set('cmd', cmd);

node.status({ fill: alarma ? 'red' : 'green', shape: 'dot',
              text: `nivel ${s.nivel.toFixed(1)} % · bomba ${s.bomba ? 'ON' : 'OFF'} · ${s.auto ? 'AUTO' : 'MANUAL'}${alarma ? ' · ALARMA' : ''}` });
return null;
"""

FN_PUBLISH = r"""
// =====================================================================
// Publica el estado del PLC a la plataforma (contrato MQTT del README):
//   <PREFIX>/<PLC_SLUG>/<variable>  {"value", "timestamp", "token"}
// Envía solo si cambió o cada 10 s (o de inmediato tras un comando).
// Salida 1 -> MQTT · Salida 2 -> HMI
// =====================================================================
const s = flow.get('plc');
if (!s) return null;

const prefix = env.get('PREFIX') || 'planta';
const slug = env.get('PLC_SLUG') || 'plc-planta-01';
const token = env.get('PLC_TOKEN') || '';
const ts = new Date().toISOString();
const r = (v, d) => Math.round(v * 10 ** d) / 10 ** d;

const values = {
    nivel_estanque: r(s.nivel, 1),
    caudal: r(s.caudal, 1),
    presion: r(s.presion, 2),
    dosificador_cloro: Math.round(s.dosif),
    bomba: s.bomba ? 1 : 0,
    valvula: s.valvula ? 1 : 0,
    modo_auto: s.auto ? 1 : 0,
    alarma: s.alarma ? 1 : 0,
};

const last = context.get('last') || {};
const lastTime = context.get('lastTime') || {};
const now = Date.now();
const out = [];
for (const [variable, value] of Object.entries(values)) {
    const due = !lastTime[variable] || now - lastTime[variable] >= 10000;
    if (msg.force || due || last[variable] !== value) {
        out.push({ topic: `${prefix}/${slug}/${variable}`, payload: { value, timestamp: ts, token } });
        last[variable] = value;
        lastTime[variable] = now;
    }
}
context.set('last', last);
context.set('lastTime', lastTime);
return [out, { payload: values }];
"""

FN_HMI = r"""
// Reparte el estado del PLC a los widgets del HMI (Dashboard 2.0)
const v = msg.payload;
const q = flow.get('calidad') || {};
const manual = v.modo_auto === 0;
const fmt = (x, d) => (x > 0 ? x.toFixed(d) : '--');
return [
    { payload: v.nivel_estanque },                                   // gauge nivel
    { payload: v.caudal },                                           // gauge caudal
    { payload: v.presion },                                          // gauge presión
    { topic: 'Nivel %', payload: v.nivel_estanque },                 // chart
    { payload: v.modo_auto === 1 },                                  // switch modo auto
    { payload: v.bomba === 1, enabled: manual },                     // switch bomba (solo en manual)
    { payload: v.valvula === 1 },                                    // switch válvula
    { payload: v.dosificador_cloro, enabled: manual },               // slider dosificador (solo en manual)
    { payload: v.alarma ? '⚠ ALARMA ACTIVA' : 'Sin alarmas' },       // texto alarma
    { payload: `pH ${fmt(q.ph || 0, 2)} · Cloro ${fmt(q.cloro || 0, 2)} mg/L · Turbiedad ${fmt(q.turb || 0, 2)} NTU` },
];
"""

FN_CMD = r"""
// =====================================================================
// Comandos al PLC simulado. Llegan de:
//   - la plataforma Django:  <PREFIX>/<PLC_SLUG>/<variable>/set  {"value", "token"}
//   - el HMI de Node-RED:    msg.topic = variable, msg.payload = valor
// En modo automático el PLC manda sobre bomba y dosificador (se ignoran).
// =====================================================================
const cmd = flow.get('cmd') || { bomba: 1, valvula: 1, auto: 1, dosif: 40 };
let variable, value;

if (typeof msg.topic === 'string' && msg.topic.endsWith('/set')) {
    const parts = msg.topic.split('/');
    if (parts.length !== 4 || parts[1] !== (env.get('PLC_SLUG') || 'plc-planta-01')) return null;
    const p = msg.payload || {};
    if (String(p.token || '') !== String(env.get('PLC_TOKEN') || '')) {
        node.warn(`Comando rechazado (token inválido): ${msg.topic}`);
        return null;
    }
    variable = parts[2];
    value = Number(p.value);
} else {
    variable = msg.topic;
    value = msg.payload === true ? 1 : msg.payload === false ? 0 : Number(msg.payload);
}
if (Number.isNaN(value)) return null;

switch (variable) {
    case 'bomba':             cmd.bomba = value >= 0.5 ? 1 : 0; break;
    case 'valvula':           cmd.valvula = value >= 0.5 ? 1 : 0; break;
    case 'modo_auto':         cmd.auto = value >= 0.5 ? 1 : 0; break;
    case 'dosificador_cloro': cmd.dosif = Math.max(0, Math.min(100, Math.round(value))); break;
    default:
        node.warn(`Variable no escribible: ${variable}`);
        return null;
}
flow.set('cmd', cmd);
node.status({ fill: 'blue', shape: 'ring', text: `${variable} = ${value}` });
return { force: true };          // tras un ciclo de scan se publica el estado real
"""

FN_CALIDAD = r"""
// Recibe del ESP32 pH, cloro libre y turbiedad y se los pasa al PLC (flow.calidad)
const parts = (msg.topic || '').split('/');
if (parts.length !== 3 || parts[1] !== (env.get('ESP32_SLUG') || 'esp32-calidad-01')) return null;
const key = { ph: 'ph', cloro_libre: 'cloro', turbidez: 'turb' }[parts[2]];
if (!key) return null;
const value = Number(msg.payload && msg.payload.value);
if (Number.isNaN(value)) return null;
const q = flow.get('calidad') || { ph: 0, cloro: 0, turb: 0 };
q[key] = value;
flow.set('calidad', q);
return null;
"""

FN_RPI = r"""
// =====================================================================
// Telemetría de la Raspberry Pi (dispositivo RPI_SLUG):
//   cpu_temp (°C), cpu_uso (%), plc_conectado (PLC simulado ejecutándose)
// Si Node-RED corre en un PC sin estos archivos (ej. Windows), se simulan.
// =====================================================================
const lines = String(msg.payload || '').trim().split('\n');
let temp = parseInt(lines[0], 10) / 1000;
let uso = null;
const cpu = lines.find(l => l.startsWith('cpu '));
if (cpu) {
    const n = cpu.trim().split(/\s+/).slice(1).map(Number);
    const idle = n[3] + (n[4] || 0);
    const total = n.reduce((a, b) => a + b, 0);
    const prev = context.get('prev');
    context.set('prev', { idle, total });
    if (prev && total > prev.total) uso = 100 * (1 - (idle - prev.idle) / (total - prev.total));
}
if (Number.isNaN(temp)) temp = 45 + Math.random() * 4 - 2;
if (uso === null) uso = 10 + Math.random() * 10;

const s = flow.get('plc');
const hb = s ? s.heartbeat : null;
const vivo = hb !== null && hb !== context.get('hb');
context.set('hb', hb);

const prefix = env.get('PREFIX') || 'planta';
const slug = env.get('RPI_SLUG') || 'rpi-gateway-01';
const token = env.get('RPI_TOKEN') || '';
const ts = new Date().toISOString();
const pub = (variable, value) => ({ topic: `${prefix}/${slug}/${variable}`, payload: { value, timestamp: ts, token } });
return [[
    pub('cpu_temp', Math.round(temp * 10) / 10),
    pub('cpu_uso', Math.round(uso * 10) / 10),
    pub('plc_conectado', vivo ? 1 : 0),
]];
"""

# ------------------------------------------------------------------ nodos


def fn(id_, name, code, outputs, x, y, wires):
    return {"id": id_, "type": "function", "z": TAB, "name": name, "func": code.strip() + "\n",
            "outputs": outputs, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "",
            "libs": [], "x": x, "y": y, "wires": wires}


def inject(id_, name, repeat, x, y, wire, payload="", ptype="date"):
    return {"id": id_, "type": "inject", "z": TAB, "name": name,
            "props": [{"p": "payload"}], "repeat": str(repeat), "crontab": "", "once": True,
            "onceDelay": "1", "topic": "", "payload": payload, "payloadType": ptype,
            "x": x, "y": y, "wires": [[wire]]}


def comment(id_, name, info, x, y):
    return {"id": id_, "type": "comment", "z": TAB, "name": name, "info": info, "x": x, "y": y, "wires": []}


def group_cfg(id_, name, order, width=6):
    return {"id": id_, "type": "ui-group", "name": name, "page": "ui_page_plc", "width": str(width),
            "height": "1", "order": order, "showTitle": True, "className": "", "visible": "true",
            "disabled": "false", "groupType": "default"}


def gauge(id_, name, group, order, title, units, mn, mx, segments, x, y, gtype="gauge-half"):
    return {"id": id_, "type": "ui-gauge", "z": TAB, "name": name, "group": group, "order": order,
            "value": "payload", "valueType": "msg", "width": 3, "height": 3, "gtype": gtype,
            "gstyle": "needle", "title": title, "alwaysShowTitle": False, "floatingTitlePosition": "top-left",
            "units": units, "icon": "", "prefix": "", "suffix": "",
            "segments": [{"from": str(f), "color": c} for f, c in segments],
            "min": mn, "max": mx, "sizeThickness": 16, "sizeGap": 4, "sizeKeyThickness": 8,
            "styleRounded": True, "styleGlow": False, "className": "", "x": x, "y": y, "wires": []}


def switch(id_, label, group, order, topic, x, y, wire):
    return {"id": id_, "type": "ui-switch", "z": TAB, "name": label, "label": label, "group": group,
            "order": order, "width": 0, "height": 0, "passthru": False, "decouple": True,
            "topic": topic, "topicType": "str", "style": "", "className": "", "layout": "row-spread",
            "clickableArea": "switch", "onvalue": "true", "onvalueType": "bool", "onicon": "", "oncolor": "",
            "offvalue": "false", "offvalueType": "bool", "officon": "", "offcolor": "",
            "x": x, "y": y, "wires": [[wire]]}


def text(id_, label, group, order, x, y, font_size=16):
    return {"id": id_, "type": "ui-text", "z": TAB, "group": group, "order": order, "width": 0, "height": 0,
            "name": label, "label": label, "format": "{{msg.payload}}", "layout": "row-spread",
            "style": False, "font": "", "fontSize": font_size, "color": "#717171", "wrapText": False,
            "className": "", "value": "payload", "valueType": "msg", "x": x, "y": y, "wires": []}


G_EST, G_MANDO = "ui_group_estanque", "ui_group_mando"

nodes = [
    {"id": TAB, "type": "tab", "label": "PLC simulado (planta de agua)", "disabled": False,
     "info": "PLC simulado de la planta de agua potable + gateway hacia la plataforma Django (MQTT) "
             "+ HMI en Dashboard 2.0 (/dashboard/plc).\n\nConfigura los tokens en las **variables de "
             "entorno de esta pestaña** (doble clic en la pestaña).",
     "env": [
         {"name": "PREFIX", "value": "planta", "type": "str"},
         {"name": "PLC_SLUG", "value": "plc-planta-01", "type": "str"},
         {"name": "PLC_TOKEN", "value": "pegar-token-del-plc", "type": "str"},
         {"name": "RPI_SLUG", "value": "rpi-gateway-01", "type": "str"},
         {"name": "RPI_TOKEN", "value": "pegar-token-de-la-raspberry", "type": "str"},
         {"name": "ESP32_SLUG", "value": "esp32-calidad-01", "type": "str"},
         {"name": "SIM_SPEED", "value": "1", "type": "num"},
     ]},

    # --- configuración ---
    {"id": "mqtt_broker", "type": "mqtt-broker", "name": "Mosquitto plataforma", "broker": "localhost",
     "port": "1883", "clientid": "nodered-plc-simulado", "autoConnect": True, "usetls": False,
     "protocolVersion": "4", "keepalive": "60", "cleansession": True, "autoUnsubscribe": True,
     "birthTopic": "", "birthQos": "0", "birthRetain": "false", "birthPayload": "", "birthMsg": {},
     "closeTopic": "", "closeQos": "0", "closeRetain": "false", "closePayload": "", "closeMsg": {},
     "willTopic": "", "willQos": "0", "willRetain": "false", "willPayload": "", "willMsg": {},
     "userProps": "", "sessionExpiry": ""},
    {"id": "ui_base", "type": "ui-base", "name": "AquaControl HMI", "path": "/dashboard", "appIcon": "",
     "includeClientData": True, "acceptsClientConfig": ["ui-notification", "ui-control"],
     "showPathInSidebar": False, "headerContent": "page", "navigationStyle": "default",
     "titleBarStyle": "default", "showReconnectNotification": True, "notificationDisplayTime": 1,
     "showDisconnectNotification": True, "allowInstall": False},
    {"id": "ui_theme", "type": "ui-theme", "name": "Agua",
     "colors": {"surface": "#0B3954", "primary": "#1C8FD1", "bgPage": "#EEF3F7", "groupBg": "#ffffff",
                "groupOutline": "#cfdbe5"},
     "sizes": {"pagePadding": "12px", "groupGap": "12px", "groupBorderRadius": "8px", "widgetGap": "12px",
               "density": "default"}},
    {"id": "ui_page_plc", "type": "ui-page", "name": "HMI PLC simulado", "ui": "ui_base", "path": "/plc",
     "icon": "water-pump", "layout": "grid", "theme": "ui_theme", "breakpoints": [
         {"name": "Default", "px": "0", "cols": "3"}, {"name": "Tablet", "px": "576", "cols": "6"},
         {"name": "Small Desktop", "px": "768", "cols": "9"}, {"name": "Desktop", "px": "1024", "cols": "12"}],
     "order": 1, "className": "", "visible": "true", "disabled": "false"},
    group_cfg(G_EST, "Estanque", 1, 6),
    group_cfg(G_MANDO, "Mando del PLC", 2, 6),

    comment("c_titulo", "PLC simulado · ciclo de scan cada 500 ms",
            "Lógica de la planta: bomba por nivel (30 %/90 %), dosificador de cloro, protección de nivel bajo "
            "y alarma NCh 409. Estado en flow.plc, comandos en flow.cmd.", 190, 40),
    inject("inj_scan", "Ciclo 0.5 s", 0.5, 130, 80, "fn_scan"),
    fn("fn_scan", "PLC simulado (OB30)", FN_SCAN, 1, 340, 80, [[]]),

    comment("c_pub", "Gateway → plataforma (MQTT) y HMI", "", 170, 140),
    inject("inj_pub", "Cada 2 s", 2, 120, 180, "fn_publish"),
    fn("fn_publish", "Publicar estado PLC", FN_PUBLISH, 2, 330, 200, [["mqtt_out"], ["fn_hmi"]]),
    {"id": "mqtt_out", "type": "mqtt out", "z": TAB, "name": "→ plataforma", "topic": "", "qos": "1",
     "retain": "false", "respTopic": "", "contentType": "", "userProps": "", "correl": "", "expiry": "",
     "broker": "mqtt_broker", "x": 580, "y": 180, "wires": []},
    fn("fn_hmi", "Estado → HMI", FN_HMI, 10, 560, 300, [
        ["g_nivel"], ["g_caudal"], ["g_presion"], ["ch_nivel"], ["sw_auto"], ["sw_bomba"], ["sw_valvula"],
        ["sl_dosif"], ["tx_alarma"], ["tx_calidad"]]),
    gauge("g_nivel", "Nivel", G_EST, 1, "Nivel del estanque", "%", 0, 100,
          [(0, "#d03b3b"), (10, "#e5a50a"), (30, "#0ca30c"), (90, "#e5a50a"), (97, "#d03b3b")], 800, 220),
    gauge("g_caudal", "Caudal", G_EST, 2, "Caudal de salida", "L/min", 0, 160,
          [(0, "#1C8FD1")], 800, 260),
    gauge("g_presion", "Presión", G_EST, 3, "Presión de red", "bar", 0, 4,
          [(0, "#e5a50a"), (1.5, "#0ca30c"), (4, "#d03b3b")], 800, 300),
    {"id": "ch_nivel", "type": "ui-chart", "z": TAB, "group": G_EST, "name": "Nivel en el tiempo",
     "label": "Nivel en el tiempo", "order": 4, "chartType": "line", "category": "topic", "categoryType": "msg",
     "xAxisLabel": "", "xAxisProperty": "", "xAxisPropertyType": "timestamp", "xAxisType": "time",
     "xAxisFormat": "", "xAxisFormatType": "auto", "xmin": "", "xmax": "", "yAxisLabel": "%",
     "yAxisProperty": "payload", "yAxisPropertyType": "msg", "ymin": "0", "ymax": "100", "action": "append",
     "stackSeries": False, "pointShape": "false", "pointRadius": 4, "showLegend": False, "removeOlder": "15",
     "removeOlderUnit": "60", "removeOlderPoints": "", "colors": ["#1C8FD1", "#d03b3b", "#ff7f0e", "#2ca02c",
     "#a347e1", "#d62728", "#ff9896", "#9467bd", "#c5b0d5"], "textColor": ["#666666"], "textColorDefault": True,
     "gridColor": ["#e5e5e5"], "gridColorDefault": True, "width": 6, "height": 4, "className": "",
     "interpolation": "linear", "x": 810, "y": 340, "wires": [[]]},
    switch("sw_auto", "Modo automático", G_MANDO, 1, "modo_auto", 820, 380, "fn_cmd"),
    switch("sw_bomba", "Bomba (manual)", G_MANDO, 2, "bomba", 820, 420, "fn_cmd"),
    switch("sw_valvula", "Válvula de salida", G_MANDO, 3, "valvula", 820, 460, "fn_cmd"),
    {"id": "sl_dosif", "type": "ui-slider", "z": TAB, "group": G_MANDO, "name": "Dosificador",
     "label": "Dosificador de cloro (%)", "tooltip": "", "order": 4, "width": 0, "height": 0, "passthru": False,
     "outs": "end", "topic": "dosificador_cloro", "topicType": "str", "thumbLabel": "always",
     "showTicks": "false", "min": 0, "max": 100, "step": 5, "className": "", "iconPrepend": "",
     "iconAppend": "", "color": "", "colorTrack": "", "colorThumb": "", "showTextField": False,
     "x": 810, "y": 500, "wires": [["fn_cmd"]]},
    text("tx_alarma", "Alarma", G_MANDO, 5, 790, 540, 18),
    text("tx_calidad", "Calidad (ESP32)", G_MANDO, 6, 800, 580, 14),

    comment("c_cmd", "Comandos: plataforma Django (/set) y HMI", "", 190, 420),
    {"id": "mqtt_in_set", "type": "mqtt in", "z": TAB, "name": "← comandos plataforma",
     "topic": "planta/+/+/set", "qos": "1", "datatype": "json", "broker": "mqtt_broker", "nl": False,
     "rap": True, "rh": 0, "inputs": 0, "x": 170, "y": 460, "wires": [["fn_cmd"]]},
    fn("fn_cmd", "Aplicar comando", FN_CMD, 1, 1060, 460, [["dl_force"]]),
    {"id": "dl_force", "type": "delay", "z": TAB, "name": "esperar 1 ciclo", "pauseType": "delay",
     "timeout": "700", "timeoutUnits": "milliseconds", "rate": "1", "nbRateUnits": "1", "rateUnits": "second",
     "randomFirst": "1", "randomLast": "5", "randomUnits": "seconds", "drop": False, "allowrate": False,
     "outputs": 1, "x": 1250, "y": 460, "wires": [["fn_publish"]]},

    comment("c_cal", "Calidad del agua desde el ESP32 → PLC", "", 200, 540),
    {"id": "mqtt_in_esp", "type": "mqtt in", "z": TAB, "name": "← ESP32 (pH, cloro, turbiedad)",
     "topic": "planta/+/+", "qos": "0", "datatype": "json", "broker": "mqtt_broker", "nl": False,
     "rap": True, "rh": 0, "inputs": 0, "x": 180, "y": 580, "wires": [["fn_calidad"]]},
    fn("fn_calidad", "Calidad → PLC", FN_CALIDAD, 1, 420, 580, [[]]),

    comment("c_rpi", "Telemetría de la Raspberry Pi", "", 170, 640),
    inject("inj_rpi", "Cada 5 s", 5, 120, 680, "ex_rpi"),
    {"id": "ex_rpi", "type": "exec", "z": TAB, "name": "leer CPU",
     "command": "cat /sys/class/thermal/thermal_zone0/temp; grep '^cpu ' /proc/stat", "addpay": "",
     "append": "", "useSpawn": "false", "timer": "3", "winHide": True, "oldrc": False,
     "x": 290, "y": 680, "wires": [["fn_rpi"], [], []]},
    fn("fn_rpi", "Telemetría Raspberry", FN_RPI, 1, 480, 680, [["mqtt_out_rpi"]]),
    {"id": "mqtt_out_rpi", "type": "mqtt out", "z": TAB, "name": "→ plataforma", "topic": "", "qos": "1",
     "retain": "false", "respTopic": "", "contentType": "", "userProps": "", "correl": "", "expiry": "",
     "broker": "mqtt_broker", "x": 690, "y": 680, "wires": []},
]

out = Path(__file__).with_name("flows.json")
out.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"{out} ({len(nodes)} nodos)")
