/* ──────────────────────────────────────────
   Greenhouse Dashboard JS — fără emoji, iconuri SVG
────────────────────────────────────────── */

let mainChart, rainChart, soilChart;

// ── Temă ──────────────────────────────────
function initTheme() {
  const saved = localStorage.getItem('gh-theme') || 'dark';
  applyTheme(saved);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('themeToggle');
  if (btn) {
    btn.innerHTML = theme === 'dark'
      ? '<svg class="icon"><use href="#i-moon"/></svg>'
      : '<svg class="icon"><use href="#i-sun"/></svg>';
  }
  localStorage.setItem('gh-theme', theme);

  if (mainChart) { updateChartTheme(mainChart); mainChart.update(); }
  if (rainChart) { updateChartTheme(rainChart); rainChart.update(); }
  if (soilChart) { updateChartTheme(soilChart); soilChart.update(); }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  applyTheme(current === 'dark' ? 'light' : 'dark');
}

function isDark() {
  return (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';
}

// ── Culori temă ───────────────────────────
function getColors() {
  const dark = isDark();
  return {
    temp:         dark ? '#e08b6d' : '#bf6443',
    tempFill:     dark ? 'rgba(224,139,109,0.12)' : 'rgba(191,100,67,0.10)',
    hum:          dark ? '#6fb9dd' : '#1f7fae',
    humFill:      dark ? 'rgba(111,185,221,0.10)' : 'rgba(31,127,174,0.08)',
    rain:         dark ? '#6fb9dd' : '#1f7fae',
    rainFill:     dark ? 'rgba(111,185,221,0.25)' : 'rgba(31,127,174,0.22)',
    soil:         dark ? '#5fc886' : '#228650',
    soilFill:     dark ? 'rgba(95,200,134,0.22)' : 'rgba(34,134,80,0.18)',
    grid:         dark ? 'rgba(214,233,218,0.05)' : 'rgba(28,42,32,0.07)',
    tick:         dark ? '#8da595' : '#5f7468',
    tooltip_bg:   dark ? '#151c16' : '#fbfcf8',
    tooltip_border: dark ? 'rgba(214,233,218,0.10)' : 'rgba(28,42,32,0.12)',
    tooltip_title: dark ? '#e9efe8' : '#1c2a20',
    tooltip_body:  dark ? '#8da595' : '#5f7468',
  };
}

function updateChartTheme(chart) {
  const C = getColors();
  chart.options.scales.x.grid.color  = C.grid;
  chart.options.scales.x.ticks.color = C.tick;
  chart.options.scales.y.grid.color  = C.grid;
  chart.options.scales.y.ticks.color = C.tick;
  chart.options.plugins.tooltip.backgroundColor = C.tooltip_bg;
  chart.options.plugins.tooltip.borderColor     = C.tooltip_border;
  chart.options.plugins.tooltip.titleColor      = C.tooltip_title;
  chart.options.plugins.tooltip.bodyColor       = C.tooltip_body;
}

// ── Setări globale Chart.js ───────────────
Chart.defaults.font.family = "'IBM Plex Mono', monospace";
Chart.defaults.font.size   = 11;

// ── Butoane interval ──────────────────────
function setActiveRangeButton(range) {
  document.querySelectorAll('.seg-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.range === range);
  });
}

// ── Mini statistici ───────────────────────
function computeStats(arr) {
  const valid = arr.filter(v => v !== null && v !== undefined && !isNaN(v));
  if (!valid.length) return null;
  return {
    min: Math.min(...valid).toFixed(1),
    max: Math.max(...valid).toFixed(1),
    avg: (valid.reduce((a, b) => a + b, 0) / valid.length).toFixed(1),
  };
}

function renderMiniStats(tempData, humData) {
  const t = computeStats(tempData);
  const h = computeStats(humData);
  const el = document.getElementById('miniStats');
  if (!el) return;

  let html = '';
  if (t) {
    html += `<div class="stat-chip temp-chip">temp. min <strong>${t.min}\u00b0</strong></div>`;
    html += `<div class="stat-chip temp-chip">medie <strong>${t.avg}\u00b0</strong></div>`;
    html += `<div class="stat-chip temp-chip">max <strong>${t.max}\u00b0</strong></div>`;
  }
  if (h) {
    html += `<div class="stat-chip hum-chip">umid. min <strong>${h.min}%</strong></div>`;
    html += `<div class="stat-chip hum-chip">medie <strong>${h.avg}%</strong></div>`;
    html += `<div class="stat-chip hum-chip">max <strong>${h.max}%</strong></div>`;
  }
  el.innerHTML = html;
}

// ── Grafic principal ──────────────────────
function initChart() {
  const C   = getColors();
  const ctx = document.getElementById('mainChart');

  mainChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: INIT.labels,
      datasets: [
        {
          label: 'Temperatură (°C)',
          data: INIT.temp,
          borderColor: C.temp,
          backgroundColor: C.tempFill,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: C.temp,
          tension: 0.4,
          fill: true,
        },
        {
          label: 'Umiditate (%)',
          data: INIT.hum,
          borderColor: C.hum,
          backgroundColor: C.humFill,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: C.hum,
          tension: 0.4,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top',
          align: 'end',
          labels: {
            boxWidth: 10, boxHeight: 10,
            borderRadius: 3, useBorderRadius: true,
            padding: 16,
            font: { size: 11, family: "'Instrument Sans', sans-serif" },
            color: () => isDark() ? '#8da595' : '#5f7468',
          },
        },
        tooltip: {
          backgroundColor: C.tooltip_bg,
          borderColor: C.tooltip_border,
          borderWidth: 1,
          padding: 12,
          titleColor: C.tooltip_title,
          bodyColor: C.tooltip_body,
          titleFont: { size: 11, family: "'IBM Plex Mono', monospace" },
          bodyFont:  { size: 12, family: "'IBM Plex Mono', monospace" },
          cornerRadius: 10,
          displayColors: true,
          boxWidth: 8, boxHeight: 8,
        },
        zoom: {
          zoom: { wheel: { enabled: true }, mode: 'x' },
          pan:  { enabled: true, mode: 'x' },
        },
      },
      scales: {
        x: {
          grid: { color: C.grid },
          ticks: { color: C.tick, maxTicksLimit: 8, maxRotation: 0 },
          border: { color: 'transparent' },
        },
        y: {
          beginAtZero: false,
          grid: { color: C.grid },
          ticks: { color: C.tick },
          border: { color: 'transparent' },
        },
      },
    },
  });

  renderMiniStats(INIT.temp, INIT.hum);
}

// ── Încărcare timeseries ──────────────────
let currentRange = 'day';

async function loadTimeseries(range) {
  currentRange = range;
  setActiveRangeButton(range);
  const r = await fetch(`/api/timeseries?range=${range}`);
  const d = await r.json();
  mainChart.data.labels           = d.labels;
  mainChart.data.datasets[0].data = d.temperature;
  mainChart.data.datasets[1].data = d.humidity;
  mainChart.update('active');
  renderMiniStats(d.temperature, d.humidity);
}

// ── Culori heatmap ────────────────────────
function colorByValue(metric, v) {
  if (v === null || v === undefined) {
    return isDark() ? 'rgba(214,233,218,0.03)' : 'rgba(28,42,32,0.03)';
  }

  if (metric === 'humidity_avg_6min') {
    if (v < 35)  return 'rgba(226,96,79,0.30)';
    if (v < 55)  return 'rgba(217,164,65,0.28)';
    if (v < 75)  return 'rgba(95,200,134,0.28)';
    return              'rgba(111,185,221,0.28)';
  }
  if (v < 15)  return 'rgba(111,185,221,0.28)';
  if (v < 20)  return 'rgba(95,200,134,0.24)';
  if (v < 25)  return 'rgba(95,200,134,0.38)';
  if (v < 30)  return 'rgba(217,164,65,0.30)';
  return              'rgba(226,96,79,0.34)';
}

function buildLegend(metric) {
  return metric === 'humidity_avg_6min'
    ? 'umiditate: &lt;35% · 35–55% · 55–75% · &gt;75%'
    : 'temperatură: &lt;15° · 15–20° · 20–25° · 25–30° · &gt;30°';
}

function formatVal(metric, v) {
  if (v === null || v === undefined) return '—';
  return metric === 'humidity_avg_6min' ? `${v}%` : `${v}\u00b0C`;
}

// ── Heatmap: grilă 24 ore ─────────────────
function renderHeatmapDayGrid(payload) {
  const heatmap = document.getElementById('heatmap');
  heatmap.className = 'heatmap heatmap-day';
  heatmap.innerHTML = '';

  payload.values.forEach((v, i) => {
    const cell = document.createElement('div');
    cell.className = 'hm-cell';
    cell.style.background = colorByValue(payload.metric, v);
    cell.title = `${payload.date} ${payload.labels[i]} → ${v ?? 'fără date'}`;
    cell.innerHTML = `
      <div class="hm-top">${payload.labels[i]}</div>
      <div class="hm-val">${formatVal(payload.metric, v)}</div>
    `;
    heatmap.appendChild(cell);
  });
}

// ── Heatmap: grilă lunară ─────────────────
function getMonthMeta(monthStr) {
  const [y, m] = monthStr.split('-').map(Number);
  const first  = new Date(y, m - 1, 1);
  const last   = new Date(y, m, 0);
  const jsDow  = first.getDay();
  return { daysInMonth: last.getDate(), mondayIndex: (jsDow + 6) % 7 };
}

function renderHeatmapMonthGrid(payload) {
  const heatmap = document.getElementById('heatmap');
  heatmap.className = 'heatmap heatmap-month';
  heatmap.innerHTML = '';

  const { daysInMonth, mondayIndex } = getMonthMeta(payload.month);
  const map = {};
  payload.days.forEach((d, i) => (map[d] = payload.values[i]));

  ['L','Ma','Mi','J','V','S','D'].forEach(h => {
    const head = document.createElement('div');
    head.className = 'hm-head';
    head.textContent = h;
    heatmap.appendChild(head);
  });

  for (let i = 0; i < mondayIndex; i++) {
    const blank = document.createElement('div');
    blank.className = 'hm-blank';
    heatmap.appendChild(blank);
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const dd      = String(day).padStart(2, '0');
    const dateStr = `${payload.month}-${dd}`;
    const v       = (dateStr in map) ? map[dateStr] : null;
    const cell    = document.createElement('div');
    cell.className        = 'hm-daycell';
    cell.style.background = colorByValue(payload.metric, v);
    cell.title            = `${dateStr} → ${v ?? 'fără date'}`;
    cell.innerHTML = `
      <div class="hm-day">${day}</div>
      <div class="hm-val-sm">${v !== null ? formatVal(payload.metric, v) : ''}</div>
    `;
    cell.onclick = () => {
      document.getElementById('hmMode').value = 'daygrid';
      document.getElementById('hmDate').value = dateStr;
      applyHeatmap();
    };
    heatmap.appendChild(cell);
  }
}

async function applyHeatmap() {
  const metric  = document.getElementById('hmMetric').value;
  const mode    = document.getElementById('hmMode').value;
  const hmDate  = document.getElementById('hmDate');
  const hmMonth = document.getElementById('hmMonth');
  const legend  = document.getElementById('hmLegend');

  legend.innerHTML = buildLegend(metric);
  hmDate.style.display  = mode === 'daygrid'   ? 'block' : 'none';
  hmMonth.style.display = mode === 'monthgrid' ? 'block' : 'none';

  let url = `/api/heatmap?mode=${mode}&metric=${metric}`;
  if (mode === 'daygrid') url += `&date=${hmDate.value}`;
  else                    url += `&month=${hmMonth.value}`;

  const r       = await fetch(url);
  const payload = await r.json();

  if (payload.mode === 'monthgrid') renderHeatmapMonthGrid(payload);
  else                              renderHeatmapDayGrid(payload);
}

// ── Grafice evenimente (ploaie & sol) ─────
function buildEventScales() {
  const C = getColors();
  return {
    x: {
      grid:   { color: C.grid },
      ticks:  { color: C.tick, maxTicksLimit: 10, maxRotation: 30 },
      border: { color: 'transparent' },
    },
    y: {
      min: -0.1, max: 1.5,
      grid:   { color: C.grid },
      ticks: {
        color: C.tick,
        stepSize: 1,
        callback: v => v === 1 ? 'DA' : v === 0 ? 'NU' : '',
      },
      border: { color: 'transparent' },
    },
  };
}

function buildEventTooltip(label) {
  const C = getColors();
  return {
    backgroundColor: C.tooltip_bg,
    borderColor:     C.tooltip_border,
    borderWidth: 1,
    padding: 10,
    titleColor: C.tooltip_title,
    bodyColor:  C.tooltip_body,
    titleFont: { size: 11, family: "'IBM Plex Mono', monospace" },
    bodyFont:  { size: 12, family: "'IBM Plex Mono', monospace" },
    cornerRadius: 8,
    callbacks: {
      label: ctx => `${label}: ${ctx.parsed.y === 1 ? 'Detectat' : 'Absent'}`,
    },
  };
}

function initEventChart(canvasId, color, fill, label) {
  const ctx = document.getElementById(canvasId);
  return new Chart(ctx, {
    type: 'bar',
    data: { labels: [], datasets: [{
      label,
      data: [],
      backgroundColor: fill,
      borderColor: color,
      borderWidth: 1,
      borderRadius: 3,
      barPercentage: 0.7,
    }]},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: {
        legend: { display: false },
        tooltip: buildEventTooltip(label),
      },
      scales: buildEventScales(),
    },
  });
}

// Fereastra de timp pentru selecția curentă
function getEventWindow(dateInputId, rangeSelectId) {
  const date  = document.getElementById(dateInputId).value;
  const range = document.getElementById(rangeSelectId).value;
  const end   = new Date(date);
  end.setDate(end.getDate() + 1);

  let start = new Date(date);
  if (range === 'week')  start.setDate(start.getDate() - 6);
  if (range === 'month') start.setDate(start.getDate() - 29);

  return {
    start: start.toISOString().split('T')[0],
    end:   end.toISOString().split('T')[0],
  };
}

// Rezumat evenimente
function renderEventSummary(containerId, data, sensorLabel, colorClass) {
  const el = document.getElementById(containerId);
  if (!el) return;

  const totalEvents = data.filter(v => v === 1).length;
  const totalPoints = data.length;
  const pct = totalPoints > 0 ? ((totalEvents / totalPoints) * 100).toFixed(0) : 0;

  if (totalPoints === 0) {
    el.innerHTML = `<div class="ev-chip">Fără date pentru perioada selectată</div>`;
    return;
  }

  el.innerHTML = `
    <div class="ev-chip ${colorClass}">
      detecții <strong>${totalEvents}</strong>
    </div>
    <div class="ev-chip ${colorClass}">
      timp activ <strong>${pct}%</strong>
    </div>
    <div class="ev-chip">
      eșantioane <strong>${totalPoints}</strong>
    </div>
  `;
}

async function loadEventChart(chart, sensor, dateInputId, rangeSelectId, summaryId, colorClass, sensorLabel) {
  const { start, end } = getEventWindow(dateInputId, rangeSelectId);

  try {
    const r = await fetch(`/api/events?sensor=${sensor}&start=${start}&end=${end}`);
    if (!r.ok) throw new Error('API error');
    const d = await r.json();

    chart.data.labels           = d.labels;
    chart.data.datasets[0].data = d.values;
    chart.update('active');

    renderEventSummary(summaryId, d.values, sensorLabel, colorClass);
  } catch(e) {
    console.warn(`Event chart error (${sensor}):`, e);
    const el = document.getElementById(summaryId);
    if (el) el.innerHTML = `<div class="ev-chip">Eroare la încărcarea datelor</div>`;
  }
}

// ── Auto-refresh carduri stare ────────────
const ALERT_SENSORS = ['rain', 'fire', 'gas', 'soil'];
const CARD_LABELS   = { rain: 'Ploaie', fire: 'Flacără', gas: 'Gaz', soil: 'Sol umed' };
const CARD_ICONS    = {
  rain: '#i-rain',
  fire: '#i-fire',
  gas:  '#i-gas',
  soil: '#i-soil',
};
// Texte „umane" în loc de 0/1
const CARD_STATES = {
  rain: { on: 'Detectată', off: 'Absentă' },
  fire: { on: 'Detectată', off: 'Absentă' },
  gas:  { on: 'Detectat',  off: 'Absent'  },
  soil: { on: 'Da',        off: 'Uscat'   },
};

function buildCardHTML(name, value) {
  const isAlert = value === 1;
  const stateText = isAlert ? CARD_STATES[name].on : CARD_STATES[name].off;
  return `<div class="mini-card ${isAlert ? 'alert' : ''}" data-sensor="${name}">
    <div class="mini-rail"></div>
    <div class="mini-icon"><svg class="icon"><use href="${CARD_ICONS[name]}"/></svg></div>
    <div class="mini-info">
      <span class="mini-label">${CARD_LABELS[name]}</span>
      <span class="mini-value">${stateText}</span>
    </div>
  </div>`;
}

let _lastStatus = null;
let _inside = { temp: null, hum: null };

function updateLiveBadge(d) {
  const badge = document.getElementById('liveStatus');
  const text  = document.getElementById('liveText');
  if (!badge || !text) return;

  if (d.climate_offline) {
    badge.classList.add('offline');
    if (d.climate_age_sec === null) {
      text.textContent = 'fără date de la senzori';
    } else {
      const min = Math.round(d.climate_age_sec / 60);
      text.textContent = `senzor climă offline · ${min} min`;
    }
  } else {
    badge.classList.remove('offline');
    text.textContent = 'transmisie live';
  }
}

async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) return;
    const d = await r.json();

    // badge online/offline + valorile interioare se actualizează mereu
    updateLiveBadge(d);
    _inside.temp = d.temp;
    _inside.hum  = d.hum;
    renderWeatherStrip();

    // nu reconstrui cardurile dacă nimic nu s-a schimbat
    const signature = ALERT_SENSORS.map(n => d[n]).join(',');
    if (signature === _lastStatus) return;
    _lastStatus = signature;

    const container = document.querySelector('.cards');
    if (!container) return;
    container.innerHTML = ALERT_SENSORS.map(n => buildCardHTML(n, d[n])).join('');
  } catch (e) {
    console.warn('Status refresh failed:', e);
  }
}

// ── Vremea de afară vs. în seră ───────────
let _weather = null;

async function loadWeather() {
  try {
    const r = await fetch('/api/weather');
    if (!r.ok) return;
    _weather = await r.json();
    renderWeatherStrip();
  } catch (e) {
    console.warn('Weather load failed:', e);
  }
}

function fmtNum(v, suffix) {
  return (v === null || v === undefined) ? '—' : `${Number(v).toFixed(1)}${suffix}`;
}

function renderWeatherStrip() {
  const el = document.getElementById('weatherStrip');
  if (!el || !_weather) return;

  el.hidden = false;
  el.innerHTML = `
    <div class="ws-group">
      <svg class="icon icon-sm"><use href="#i-cloud"/></svg>
      <span class="ws-label">Afară</span>
      <strong>${fmtNum(_weather.temp, '\u00b0')}</strong>
      <span class="ws-sep">·</span>
      <strong>${fmtNum(_weather.hum, '%')}</strong>
      ${_weather.desc ? `<span class="ws-desc">${_weather.desc}</span>` : ''}
    </div>
    <div class="ws-divider"></div>
    <div class="ws-group">
      <svg class="icon icon-sm"><use href="#i-leaf"/></svg>
      <span class="ws-label">În seră</span>
      <strong class="ws-temp">${fmtNum(_inside.temp, '\u00b0')}</strong>
      <span class="ws-sep">·</span>
      <strong class="ws-hum">${fmtNum(_inside.hum, '%')}</strong>
    </div>
  `;
}

// ── Istoric alerte ────────────────────────
const ALERT_NAMES = { fire: 'Flacără', gas: 'Gaz' };

function formatDuration(sec) {
  if (sec < 60) return `${sec} sec`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

async function loadAlerts() {
  const el = document.getElementById('alertsList');
  if (!el) return;
  try {
    const r = await fetch('/api/alerts?limit=10');
    if (!r.ok) throw new Error('API error');
    const d = await r.json();

    if (!d.alerts.length) {
      el.innerHTML = `<div class="alerts-empty">Nicio alertă înregistrată — sera e în siguranță.</div>`;
      return;
    }

    el.innerHTML = d.alerts.map(a => `
      <div class="alert-row ${a.ongoing ? 'ongoing' : ''}">
        <span class="alert-type alert-${a.sensor}">
          <svg class="icon icon-sm"><use href="#i-${a.sensor === 'fire' ? 'fire' : 'gas'}"/></svg>
          ${ALERT_NAMES[a.sensor] || a.sensor}
        </span>
        <span class="alert-start">${a.start}</span>
        <span class="alert-duration">${a.ongoing ? 'în desfășurare' : formatDuration(a.duration_sec)}</span>
      </div>
    `).join('');
  } catch (e) {
    console.warn('Alerts load failed:', e);
    el.innerHTML = `<div class="alerts-empty">Eroare la încărcarea istoricului de alerte.</div>`;
  }
}

// ── Inițializare ──────────────────────────
window.onload = () => {
  initTheme();
  initChart();
  setActiveRangeButton(INIT.range);

  document.getElementById('themeToggle').onclick = toggleTheme;

  refreshStatus();
  setInterval(refreshStatus, 5000);

  document.querySelectorAll('.seg-btn').forEach(btn => {
    btn.onclick = () => loadTimeseries(btn.dataset.range);
  });

  document.getElementById('btnResetZoom').onclick = () => mainChart.resetZoom();
  document.getElementById('btnExport').onclick = () => {
    window.location.href = `/api/export?range=${currentRange}`;
  };

  // Vremea de afară + istoric alerte
  loadWeather();
  setInterval(loadWeather, 10 * 60 * 1000);   // la 10 minute
  loadAlerts();
  setInterval(loadAlerts, 60 * 1000);         // la 1 minut

  // Heatmap init
  const today = new Date();
  const yyyy  = today.getFullYear();
  const mm    = String(today.getMonth() + 1).padStart(2, '0');
  const dd    = String(today.getDate()).padStart(2, '0');
  const todayStr = `${yyyy}-${mm}-${dd}`;
  const monthStr = `${yyyy}-${mm}`;

  document.getElementById('hmDate').value  = todayStr;
  document.getElementById('hmMonth').value = monthStr;

  ['hmMetric','hmMode','hmDate','hmMonth'].forEach(id => {
    document.getElementById(id).addEventListener('change', applyHeatmap);
  });

  document.getElementById('hmMetric').value = INIT.heatmap.metric || 'temperature_avg_6min';
  document.getElementById('hmMode').value   = INIT.heatmap.mode   || 'daygrid';
  applyHeatmap();

  // Grafice evenimente
  const C = getColors();
  rainChart = initEventChart('rainChart', C.rain, C.rainFill, 'Ploaie');
  soilChart = initEventChart('soilChart', C.soil, C.soilFill, 'Sol umed');

  document.getElementById('rainDate').value = todayStr;
  document.getElementById('soilDate').value = todayStr;

  loadEventChart(rainChart, 'rain', 'rainDate', 'rainRange', 'rainSummary', 'rain-chip', 'Ploaie');
  loadEventChart(soilChart, 'soil', 'soilDate', 'soilRange', 'soilSummary', 'soil-chip', 'Sol umed');

  ['rainDate','rainRange'].forEach(id => {
    document.getElementById(id).addEventListener('change', () =>
      loadEventChart(rainChart, 'rain', 'rainDate', 'rainRange', 'rainSummary', 'rain-chip', 'Ploaie')
    );
  });

  ['soilDate','soilRange'].forEach(id => {
    document.getElementById(id).addEventListener('change', () =>
      loadEventChart(soilChart, 'soil', 'soilDate', 'soilRange', 'soilSummary', 'soil-chip', 'Sol umed')
    );
  });
};
