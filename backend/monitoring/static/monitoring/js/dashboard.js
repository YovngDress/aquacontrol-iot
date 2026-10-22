(function () {
  const MAX_POINTS = 50;
  const ONLINE_THRESHOLD_SECONDS = 15;
  const POLL_INTERVAL_MS = 2500;
  const VALUE_TOLERANCE = 0.001;

  const root = document.getElementById("dashboard-root");
  if (!root) return;

  const apiUrl = root.dataset.apiUrl;
  const commandUrlTemplate = root.dataset.commandUrlTemplate || "";
  const seedDataEl = document.getElementById("seed-data");
  const seedData = seedDataEl ? JSON.parse(seedDataEl.textContent) : {};

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function hexToRgba(hex, alpha) {
    const clean = hex.replace("#", "");
    const bigint = parseInt(clean, 16);
    const r = (bigint >> 16) & 255;
    const g = (bigint >> 8) & 255;
    const b = bigint & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function formatTime(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
    return match ? decodeURIComponent(match[2]) : null;
  }

  function commandUrl(sensorId) {
    return commandUrlTemplate.replace(/\/0\/command\//, `/${sensorId}/command/`);
  }

  async function postCommand(sensorId, value) {
    try {
      const res = await fetch(commandUrl(sensorId), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken"),
        },
        body: JSON.stringify({ value: value }),
      });
      return res.ok;
    } catch (err) {
      console.error("Command failed", err);
      return false;
    }
  }

  function createChart(canvas, seedPoints) {
    const seriesColor = cssVar("--series-1");
    const grid = cssVar("--gridline");
    const muted = cssVar("--text-muted");
    const surface = cssVar("--surface-1");

    return new Chart(canvas, {
      type: "line",
      data: {
        labels: seedPoints.map((p) => formatTime(p.t)),
        datasets: [
          {
            data: seedPoints.map((p) => p.v),
            borderColor: seriesColor,
            backgroundColor: hexToRgba(seriesColor, 0.1),
            fill: true,
            borderWidth: 2,
            borderCapStyle: "round",
            borderJoinStyle: "round",
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: seriesColor,
            pointHoverBorderColor: surface,
            pointHoverBorderWidth: 2,
            tension: 0.3,
            cubicInterpolationMode: "monotone",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "nearest", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { displayColors: false },
        },
        scales: {
          x: { display: false, grid: { display: false } },
          y: {
            grid: { color: grid },
            ticks: { color: muted, maxTicksLimit: 4 },
          },
        },
      },
    });
  }

  // Texto del botón según la variable (por defecto Encendido/Apagado).
  const BUTTON_LABELS = {
    valvula: ["Abierta", "Cerrada"],
    modo_auto: ["Automático", "Manual"],
  };

  // Texto junto al LED según la variable.
  const LED_LABELS = {
    alarma: ["Alarma activa", "Sin alarma"],
    plc_conectado: ["Conectado", "Desconectado"],
  };

  function fmt(value, unit) {
    if (value === null || value === undefined) return "--";
    const rounded = Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(2).replace(/\.?0+$/, "");
    return `${rounded}${unit ? " " + unit : ""}`;
  }

  function setChip(el, status, okText, badText) {
    if (!el) return;
    el.classList.remove("chip-ok", "chip-bad");
    if (status === "ok") {
      el.textContent = okText || "OK";
      el.classList.add("chip-ok");
    } else if (status === "fuera") {
      el.textContent = badText || "Fuera de rango";
      el.classList.add("chip-bad");
    } else {
      el.textContent = "sin dato";
    }
  }

  function updateCompliance(compliance) {
    if (!compliance) return;
    const verdict = document.querySelector('[data-role="verdict"]');
    if (verdict) {
      verdict.classList.remove("verdict-ok", "verdict-bad");
      if (compliance.complies === true) {
        verdict.textContent = "Agua apta: cumple " + compliance.norm;
        verdict.classList.add("verdict-ok");
      } else if (compliance.complies === false) {
        verdict.textContent = "No cumple " + compliance.norm;
        verdict.classList.add("verdict-bad");
      } else {
        verdict.textContent = "sin datos";
      }
    }
    compliance.items.forEach((item) => {
      const el = document.querySelector(`[data-norm-var="${item.variable}"]`);
      if (!el) return;
      el.querySelector('[data-role="norm-value"]').textContent = fmt(item.value, "");
      el.classList.toggle("is-bad", item.status === "fuera");
      setChip(el.querySelector('[data-role="norm-chip"]'), item.status, "Cumple", "No cumple");
    });
  }

  function updateProcess(byVar) {
    const value = (name) => (byVar[name] ? byVar[name].value : null);
    const isOn = (name) => value(name) !== null && value(name) >= 0.5;

    document.querySelectorAll("[data-text]").forEach((el) => {
      const name = el.dataset.text;
      const s = byVar[name];
      if (!s) return;
      if (name === "bomba") el.textContent = isOn(name) ? "en marcha" : "detenida";
      else if (name === "valvula") el.textContent = isOn(name) ? "abierta" : "cerrada";
      else el.textContent = fmt(s.value, s.unit);
    });

    ["bomba", "valvula"].forEach((name) => {
      const g = document.querySelector(`[data-equip="${name}"]`);
      if (g && byVar[name]) g.classList.toggle("on", isOn(name));
    });
    document.querySelectorAll("[data-pipe]").forEach((p) => {
      const name = p.dataset.pipe;
      if (!byVar[name]) return;
      p.classList.toggle("flowing", value(name) > 0.5);
    });

    const water = document.querySelector('[data-role="tank-water"]');
    if (water && value("nivel_estanque") !== null) {
      const pct = Math.max(0, Math.min(100, value("nivel_estanque")));
      const h = (140 * pct) / 100;
      water.setAttribute("y", String(190 - h));
      water.setAttribute("height", String(h));
    }

    document.querySelectorAll("[data-flag]").forEach((chip) => {
      const name = chip.dataset.flag;
      if (!byVar[name]) return;
      const on = isOn(name);
      chip.textContent = on ? chip.dataset.on : chip.dataset.off;
      chip.classList.remove("chip-ok", "chip-bad");
      if (chip.dataset.alert) chip.classList.add(on ? "chip-bad" : "chip-ok");
      else chip.classList.add(on ? "chip-ok" : "chip-bad");
    });
  }

  const charts = {};
  const lastTimestamps = {};
  const pendingCommands = {}; // sensorId -> value we told the device to reach, cleared once confirmed

  document.querySelectorAll(".sensor-card").forEach((card) => {
    const sensorId = card.dataset.sensorId;
    const widgetType = card.dataset.widgetType;

    if (widgetType === "line_chart" && typeof Chart !== "undefined") {
      const canvas = card.querySelector('[data-role="chart"]');
      const seed = seedData[sensorId] || [];
      charts[sensorId] = createChart(canvas, seed);
      if (seed.length) {
        lastTimestamps[sensorId] = seed[seed.length - 1].t;
      }
    }

    if (widgetType === "button") {
      const btn = card.querySelector('[data-role="gadget-button"]');
      btn.addEventListener("click", async () => {
        const onValue = parseFloat(btn.dataset.onValue);
        const offValue = parseFloat(btn.dataset.offValue);
        const isLit = btn.classList.contains("lit");
        const target = isLit ? offValue : onValue;
        btn.classList.add("pending");
        pendingCommands[sensorId] = target;
        await postCommand(sensorId, target);
      });
    }

    if (widgetType === "slider") {
      const slider = card.querySelector('[data-role="gadget-slider"]');
      slider.addEventListener("change", async () => {
        pendingCommands[sensorId] = parseFloat(slider.value);
        await postCommand(sensorId, parseFloat(slider.value));
      });
    }
  });

  function isSliderBeingDragged(slider) {
    return document.activeElement === slider;
  }

  async function poll() {
    try {
      const res = await fetch(apiUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!res.ok) return;
      const data = await res.json();
      const serverNow = new Date(data.server_time).getTime();

      const byVar = {};
      data.sensors.forEach((s) => {
        if (s.value === null) return;
        if (!byVar[s.variable] || s.timestamp > byVar[s.variable].timestamp) byVar[s.variable] = s;
        const card = document.querySelector(`.sensor-card[data-sensor-id="${s.id}"]`);
        if (!card) return;
        const widgetType = card.dataset.widgetType;

        const valueEl = card.querySelector('[data-role="latest-value"]');
        if (valueEl) valueEl.textContent = fmt(s.value, s.unit);
        card.classList.toggle("is-bad", s.status === "fuera");
        setChip(card.querySelector('[data-role="status-chip"]'), s.status, "OK", "Fuera");

        if (widgetType === "line_chart") {
          const chart = charts[s.id];
          if (!chart || lastTimestamps[s.id] === s.timestamp) return;
          lastTimestamps[s.id] = s.timestamp;
          chart.data.labels.push(formatTime(s.timestamp));
          chart.data.datasets[0].data.push(s.value);
          if (chart.data.datasets[0].data.length > MAX_POINTS) {
            chart.data.labels.shift();
            chart.data.datasets[0].data.shift();
          }
          chart.update("none");
        }

        if (widgetType === "meter") {
          const fill = card.querySelector('[data-role="meter-fill"]');
          const min = parseFloat(fill.dataset.min);
          const max = parseFloat(fill.dataset.max);
          const pct = Math.max(0, Math.min(100, ((s.value - min) / (max - min)) * 100));
          fill.style.width = `${pct}%`;
          fill.classList.toggle("meter-bad", s.status === "fuera");
        }

        if (widgetType === "led") {
          const dot = card.querySelector('[data-role="led-dot"]');
          const threshold = parseFloat(dot.dataset.threshold);
          dot.classList.toggle("lit", s.value >= threshold);
          if (LED_LABELS[s.variable] && valueEl) {
            valueEl.textContent = LED_LABELS[s.variable][s.value >= threshold ? 0 : 1];
          }
        }

        if (widgetType === "button") {
          const btn = card.querySelector('[data-role="gadget-button"]');
          const onValue = parseFloat(btn.dataset.onValue);
          const isOn = Math.abs(s.value - onValue) < VALUE_TOLERANCE;
          const labels = BUTTON_LABELS[s.variable] || ["Encendido", "Apagado"];
          btn.textContent = isOn ? labels[0] : labels[1];
          btn.classList.toggle("lit", isOn);

          const pending = pendingCommands[s.id];
          if (pending !== undefined && Math.abs(s.value - pending) < VALUE_TOLERANCE) {
            delete pendingCommands[s.id];
            btn.classList.remove("pending");
          }
        }

        if (widgetType === "slider") {
          const slider = card.querySelector('[data-role="gadget-slider"]');
          if (!isSliderBeingDragged(slider)) {
            slider.value = s.value;
          }
          const pending = pendingCommands[s.id];
          if (pending !== undefined && Math.abs(s.value - pending) < VALUE_TOLERANCE) {
            delete pendingCommands[s.id];
          }
        }
      });

      updateCompliance(data.compliance);
      updateProcess(byVar);

      data.devices.forEach((dev) => {
        const card = document.querySelector(`.device-card[data-device-id="${dev.id}"]`);
        if (!card) return;
        const badge = card.querySelector('[data-role="status-badge"]');
        if (!badge) return;

        if (!dev.last_seen_at) {
          badge.textContent = "sin datos";
          badge.className = "badge";
          return;
        }
        const ageSeconds = (serverNow - new Date(dev.last_seen_at).getTime()) / 1000;
        if (ageSeconds <= ONLINE_THRESHOLD_SECONDS) {
          badge.textContent = "en línea";
          badge.className = "badge online";
        } else {
          badge.textContent = "sin señal";
          badge.className = "badge offline";
        }
      });
    } catch (err) {
      console.error("Polling failed", err);
    }
  }

  poll();
  setInterval(poll, POLL_INTERVAL_MS);
})();
