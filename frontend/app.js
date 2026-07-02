const API_BASE = `${window.location.protocol}//${window.location.hostname}:8000/api`;

const els = {
  status: document.getElementById('apiStatus'),
  power: document.getElementById('kpiPower'),
  pf: document.getElementById('kpiPF'),
  thd: document.getElementById('kpiTHD'),
  alarms: document.getElementById('kpiAlarms'),
  latestTable: document.getElementById('latestTable'),
  alarmsList: document.getElementById('alarmsList'),
  chart: document.getElementById('trendChart'),
  cdcSelect: document.getElementById('cdcSelect'),
  agentAnswer: document.getElementById('agentAnswer'),
  llmStatus: document.getElementById('llmStatus'),
  chatForm: document.getElementById('chatForm'),
  chatInput: document.getElementById('chatInput'),
};

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  return Number(value).toFixed(digits).replace('.', ',');
}

function avg(...values) {
  return values.reduce((a, b) => a + Number(b), 0) / values.length;
}

function eventBadge(eventType) {
  const cls = eventType === 'normal' ? 'normal' : eventType.includes('sag') || eventType.includes('swell') ? 'danger' : 'warn';
  return `<span class="badge ${cls}">${eventType}</span>`;
}

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function refreshStatus() {
  try {
    await fetchJSON(`${API_BASE}/health`);
    els.status.textContent = 'API online';
    els.status.className = 'status-pill online';
  } catch (err) {
    els.status.textContent = 'API offline';
    els.status.className = 'status-pill offline';
  }
}


async function refreshLLMStatus() {
  if (!els.llmStatus) return;
  try {
    const data = await fetchJSON(`${API_BASE}/llm/status`);
    let txt = `LLM: ${data.configured_provider}`;
    if (data.configured_provider === 'ollama') {
      txt += data.ollama_available ? ` • ${data.ollama_model}` : ' • aguardando Ollama/modelo';
    }
    if (data.configured_provider === 'openai') {
      txt += data.openai_key_configured ? ` • ${data.openai_model}` : ' • chave não configurada';
    }
    els.llmStatus.textContent = txt;
  } catch (err) {
    els.llmStatus.textContent = 'LLM: status indisponível';
  }
}

async function refreshKPIs() {
  const data = await fetchJSON(`${API_BASE}/kpis`);
  els.power.textContent = fmt(data.avg_power_kw, 1);
  els.pf.textContent = fmt(data.avg_power_factor, 3);
  els.thd.textContent = `${fmt(data.avg_thd_voltage, 2)}%`;
  els.alarms.textContent = data.open_alarms_1h ?? '--';
}

async function refreshLatest() {
  const data = await fetchJSON(`${API_BASE}/latest`);
  els.latestTable.innerHTML = '';
  for (const item of data.items) {
    const voltage = avg(item.voltage_a, item.voltage_b, item.voltage_c);
    const current = avg(item.current_a, item.current_b, item.current_c);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${item.cdc}</strong></td>
      <td>${fmt(voltage, 1)} V</td>
      <td>${fmt(current, 1)} A</td>
      <td>${fmt(item.power_factor, 3)}</td>
      <td>${fmt(item.thd_voltage, 2)}%</td>
      <td>${eventBadge(item.event_type)}</td>
    `;
    els.latestTable.appendChild(tr);
  }
}

function severityBadge(sev) {
  const cls = sev === 'critical' || sev === 'high' ? 'danger' : 'warn';
  return `<span class="badge ${cls}">${sev}</span>`;
}

async function refreshAlarms() {
  const data = await fetchJSON(`${API_BASE}/alarms?limit=12`);
  els.alarmsList.innerHTML = '';
  if (!data.items.length) {
    els.alarmsList.innerHTML = '<p class="muted">Nenhum alarme registrado ainda.</p>';
    return;
  }
  for (const alarm of data.items) {
    const div = document.createElement('div');
    div.className = 'alarm-item';
    const time = new Date(alarm.ts).toLocaleTimeString('pt-BR');
    div.innerHTML = `
      <header>
        <strong>${alarm.cdc} • ${alarm.type}</strong>
        ${severityBadge(alarm.severity)}
      </header>
      <small>${time} • valor: ${fmt(alarm.value, 3)}</small>
      <p>${alarm.description}</p>
      <p><strong>Recomendação:</strong> ${alarm.recommendation}</p>
    `;
    els.alarmsList.appendChild(div);
  }
}

function drawChart(items) {
  const canvas = els.chart;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  ctx.fillStyle = '#07111f';
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = 'rgba(255,255,255,0.12)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {
    const y = 24 + (h - 56) * (i / 5);
    ctx.beginPath();
    ctx.moveTo(42, y);
    ctx.lineTo(w - 18, y);
    ctx.stroke();
  }

  if (!items.length) {
    ctx.fillStyle = '#9fb3c8';
    ctx.font = '18px Segoe UI';
    ctx.fillText('Aguardando dados do simulador...', 42, h / 2);
    return;
  }

  const padL = 42;
  const padR = 18;
  const padT = 24;
  const padB = 32;
  const cw = w - padL - padR;
  const ch = h - padT - padB;

  const series = {
    voltage: items.map(x => avg(x.voltage_a, x.voltage_b, x.voltage_c)),
    pf: items.map(x => x.power_factor),
    thd: items.map(x => x.thd_voltage),
  };

  function norm(v, min, max) {
    return (v - min) / (max - min);
  }

  function plot(values, color, min, max) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = padL + (cw * i) / Math.max(values.length - 1, 1);
      const y = padT + ch * (1 - Math.max(0, Math.min(1, norm(v, min, max))));
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  plot(series.voltage, '#49a3ff', 175, 260);
  plot(series.pf, '#57d68d', 0.78, 1.0);
  plot(series.thd, '#ffcc66', 0, 12);

  ctx.fillStyle = '#9fb3c8';
  ctx.font = '13px Segoe UI';
  ctx.fillText('normalizado', 42, 18);
  ctx.fillText(`${items[0]?.cdc || ''}`, w - 84, 18);
}

async function refreshChart() {
  const cdc = els.cdcSelect.value;
  const data = await fetchJSON(`${API_BASE}/telemetry?cdc=${encodeURIComponent(cdc)}&limit=90`);
  drawChart(data.items);
}

async function askAgent(question) {
  els.agentAnswer.textContent = 'Analisando dados do banco e decisões recentes...';
  try {
    const data = await fetchJSON(`${API_BASE}/agent/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    const meta = data.provider ? `\n\n— Fonte: ${data.provider}${data.model ? ' / ' + data.model : ''}` : '';
    els.agentAnswer.textContent = data.answer + meta;
  } catch (err) {
    els.agentAnswer.textContent = `Falha ao consultar agente: ${err.message}`;
  }
}

async function refreshAll() {
  await refreshStatus();
  await refreshLLMStatus();
  try {
    await Promise.all([refreshKPIs(), refreshLatest(), refreshAlarms(), refreshChart()]);
  } catch (err) {
    console.error(err);
  }
}

els.cdcSelect.addEventListener('change', refreshChart);
els.chatForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const question = els.chatInput.value.trim();
  if (!question) return;
  askAgent(question);
  els.chatInput.value = '';
});

document.querySelectorAll('[data-question]').forEach((btn) => {
  btn.addEventListener('click', () => askAgent(btn.dataset.question));
});

refreshAll();
setInterval(refreshAll, 3000);
setTimeout(() => askAgent('Gere um relatório executivo da qualidade de energia agora'), 5000);
