"""Catálogo de variables de la planta de agua potable y límites NCh 409/1.

Centraliza todo lo que es "propio del proyecto": nombre legible de cada
variable, unidad, gadget por defecto con el que aparece en el dashboard y,
cuando aplica, el rango que exige la norma chilena de agua potable
NCh 409/1 (Of. 2005).

- ``norm``: rango exigido por NCh 409/1 -> cuenta para el cumplimiento.
- ``ref``:  rango operacional de referencia de la planta (NO es exigencia
  de la norma, ej. temperatura u oxígeno disuelto). Se colorea en el
  dashboard pero no afecta el veredicto de cumplimiento.

Para agregar una variable nueva basta con sumarla aquí; el listener MQTT
crea el sensor y su gadget por defecto automáticamente en el primer
mensaje que llegue.
"""

NORM_NAME = "NCh 409/1"

VARIABLES = {
    # --- Calidad del agua (ESP32) ---
    "ph": {
        "label": "pH",
        "unit": "",
        "widget": "meter",
        "min": 0,
        "max": 14,
        "norm": (6.5, 8.5),
    },
    "turbidez": {
        "label": "Turbiedad",
        "unit": "NTU",
        "widget": "meter",
        "min": 0,
        "max": 10,
        "norm": (None, 2.0),
    },
    "cloro_libre": {
        "label": "Cloro libre residual",
        "unit": "mg/L",
        "widget": "meter",
        "min": 0,
        "max": 3,
        "norm": (0.2, 2.0),
    },
    "temperatura": {
        "label": "Temperatura del agua",
        "unit": "°C",
        "widget": "line_chart",
        "ref": (5.0, 25.0),
    },
    "oxigeno_disuelto": {
        "label": "Oxígeno disuelto",
        "unit": "mg/L",
        "widget": "line_chart",
        "ref": (5.0, None),
    },
    # --- Proceso (PLC, vía gateway Raspberry Pi) ---
    "nivel_estanque": {
        "label": "Nivel del estanque",
        "unit": "%",
        "widget": "meter",
        "min": 0,
        "max": 100,
        "ref": (20.0, 95.0),
    },
    "caudal": {"label": "Caudal de salida", "unit": "L/min", "widget": "line_chart"},
    "presion": {"label": "Presión de red", "unit": "bar", "widget": "numeric", "ref": (1.5, 4.0)},
    "bomba": {"label": "Bomba de impulsión", "unit": "", "widget": "button"},
    "valvula": {"label": "Electroválvula de salida", "unit": "", "widget": "button"},
    "modo_auto": {"label": "Modo automático del PLC", "unit": "", "widget": "button"},
    "dosificador_cloro": {
        "label": "Dosificador de cloro",
        "unit": "%",
        "widget": "slider",
        "min": 0,
        "max": 100,
        "step": 5,
    },
    "alarma": {"label": "Alarma de planta", "unit": "", "widget": "led", "threshold": 1},
    # --- Raspberry Pi (gateway) ---
    "cpu_temp": {"label": "Temperatura CPU", "unit": "°C", "widget": "numeric", "ref": (None, 70.0)},
    "cpu_uso": {"label": "Uso de CPU", "unit": "%", "widget": "meter", "min": 0, "max": 100},
    "plc_conectado": {"label": "Enlace Modbus con PLC", "unit": "", "widget": "led", "threshold": 1},
}

# Orden en que se muestran las variables reguladas en el panel de cumplimiento.
NORM_VARIABLES = [name for name, spec in VARIABLES.items() if "norm" in spec]


def get_spec(variable):
    return VARIABLES.get(variable, {})


def label_for(variable):
    return get_spec(variable).get("label", variable.replace("_", " ").capitalize())


def default_unit(variable):
    return get_spec(variable).get("unit", "")


def _in_range(value, bounds):
    low, high = bounds
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def range_text(bounds):
    low, high = bounds
    if low is not None and high is not None:
        return f"{low:g} – {high:g}"
    if high is not None:
        return f"≤ {high:g}"
    return f"≥ {low:g}"


def evaluate(variable, value):
    """Devuelve (estado, rango_texto, es_norma).

    estado: "ok", "fuera" o "" (sin rango definido / sin valor).
    """
    spec = get_spec(variable)
    bounds = spec.get("norm") or spec.get("ref")
    if bounds is None or value is None:
        return "", "", False
    status = "ok" if _in_range(value, bounds) else "fuera"
    return status, range_text(bounds), "norm" in spec


def widget_defaults(variable):
    """Campos iniciales del Widget para un sensor recién creado."""
    spec = get_spec(variable)
    fields = {"widget_type": spec.get("widget", "line_chart")}
    for key, field in (("min", "min_value"), ("max", "max_value"), ("step", "slider_step"),
                       ("threshold", "led_on_threshold")):
        if key in spec:
            fields[field] = spec[key]
    return fields
