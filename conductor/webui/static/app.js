'use strict';

/* Conductor web dashboard — polling, tab switching, Live/Stats/Tail rendering */

const RING_BUFFER_MAX = 1000;
const INITIAL_BACKFILL = 200;
const TAIL_POLL_MS = 1000;
const SUMMARY_POLL_MS = 5000;

const RANGE_DAYS = { '24h': 1, '7d': 7, '30d': 30 };
const RANGE_LABELS = { '24h': '24H', '7d': '7D', '30d': '30D' };

const state = {
  activeTab: 'live',
  statsRange: '24h',
  tailModelFilter: 'all',
  tailRuleFilter: 'all',
  selectedRowId: null,
  rows: [],
  lastRowId: 0,
  summary: null,
  spendTrend: [],
  health: { up: true, default_model: null, error: null },
  ladder: [],
  proxyDown: false,
};

/* ── Formatting (mirrors conductor/dashboard/render.py) ── */

function fmtCost(c) {
  return c != null ? `$${c.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })}` : '?';
}

function fmtTokens(n) {
  return n != null ? n.toLocaleString('en-US') : '?';
}

function fmtClock(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtDateTime(ts) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtLatency(ms) {
  if (ms == null) return '?';
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function short(s, width) {
  if (s == null) return '';
  if (s.length <= width) return s;
  if (width <= 1) return '…'.slice(0, width);
  return s.slice(0, width - 1) + '…';
}

function shortModel(model) {
  if (!model) return '?';
  if (model.startsWith('claude-')) return model.slice(7);
  if (model.startsWith('anthropic/')) return model.slice(10);
  return model;
}

function rowColorClass(row) {
  if (row.escalated) return 'escalated';
  if (row.status == null || row.status !== 200) return 'error';
  return 'normal';
}

function summaryCostDisplay(summary) {
  if (!summary) return '?';
  if (summary.total_calls === 0 && summary.total_cost == null) return fmtCost(0);
  return fmtCost(summary.total_cost);
}

function nullText(v) {
  return v != null ? v : '—';
}

function streamText(stream) {
  if (stream === 1 || stream === true) return 'yes';
  if (stream === 0 || stream === false) return 'no';
  return '—';
}

/* ── API helpers ── */

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

function setProxyDown(down) {
  state.proxyDown = down;
  renderHeader();
}

/* ── Row buffer ── */

function mergeRows(incoming) {
  if (!incoming.length) return;
  const byId = new Map(state.rows.map((r) => [r.id, r]));
  for (const row of incoming) byId.set(row.id, row);
  state.rows = [...byId.values()].sort((a, b) => a.id - b.id);
  if (state.rows.length > RING_BUFFER_MAX) {
    state.rows = state.rows.slice(-RING_BUFFER_MAX);
  }
  state.lastRowId = state.rows.length ? state.rows[state.rows.length - 1].id : 0;
}

/* ── DOM helpers ── */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function modelChips(reqShort, routed) {
  const wrap = el('span', 'model-chips');
  wrap.appendChild(el('span', 'chip chip--req', reqShort));
  wrap.appendChild(el('span', 'chip-arrow', '➜'));
  wrap.appendChild(el('span', 'chip chip--routed', routed || '?'));
  return wrap;
}

function escalatedId(row) {
  return (row.escalated ? '⤴' : '') + String(row.id);
}

/* ── Render: header + KPIs ── */

function renderHeader() {
  const dot = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  const ladderEl = document.getElementById('ladder-text');

  const up = !state.proxyDown && state.health.up;
  dot.className = `status-dot ${up ? 'status-dot--up' : 'status-dot--down'}`;
  label.className = `status-label ${up ? 'status-label--up' : 'status-label--down'}`;

  if (up) {
    label.textContent = 'UP';
  } else {
    const err = state.health.error || 'unreachable';
    label.textContent = `DOWN (${err})`;
  }

  if (state.ladder.length) {
    const shortLadder = state.ladder.map(shortModel).join(' → ');
    ladderEl.textContent = `ladder: ${shortLadder}`;
  } else {
    ladderEl.textContent = 'ladder: —';
  }
}

function renderKPIs() {
  const s = state.summary;
  document.getElementById('kpi-calls').textContent = s ? s.total_calls.toLocaleString('en-US') : '0';
  document.getElementById('kpi-spend').textContent = summaryCostDisplay(s);
  document.getElementById('kpi-escalations').textContent = s ? String(s.escalation_count) : '0';
  document.getElementById('kpi-errors').textContent = s ? String(s.error_count) : '0';
}

/* ── Render: Live tab ── */

function renderPanelRows(container, items, nameKey) {
  container.replaceChildren();
  if (!items || !items.length) {
    container.appendChild(el('div', 'panel-empty', 'no requests yet'));
    return;
  }
  for (const item of items) {
    const row = el('div', 'panel-row');
    const name = item[nameKey] ?? '?';
    const calls = item.calls;
    const cost = fmtCost(item.cost);
    row.appendChild(el('span', null, String(name)));
    row.appendChild(el('span', 'panel-row-meta', `${calls} · ${cost}`));
    container.appendChild(row);
  }
}

function renderLiveByModel() {
  const container = document.getElementById('live-by-model');
  const items = (state.summary?.by_model || []).map(([model, calls, , , cost]) => ({
    model: model || '?',
    calls,
    cost,
  }));
  renderPanelRows(container, items, 'model');
}

function renderLiveByRule() {
  const container = document.getElementById('live-by-rule');
  const items = (state.summary?.by_rule || []).map(([rule, calls, cost]) => ({
    rule: rule || '?',
    calls,
    cost,
  }));
  renderPanelRows(container, items, 'rule');
}

function buildRequestRow(row, mode) {
  const colorCls = rowColorClass(row);
  const cls = mode === 'tail' ? `tail-row tail-row--${colorCls}` : `request-row request-row--${colorCls}`;
  const tr = el('div', cls);
  tr.dataset.rowId = String(row.id);

  const ruleText = short(row.rule, 20);
  const reqShort = shortModel(row.requested_model);
  const routed = row.routed_model || '?';
  const tok = `${fmtTokens(row.input_tokens)}/${fmtTokens(row.output_tokens)}`;

  if (mode === 'tail') {
    tr.appendChild(el('span', null, escalatedId(row)));
    tr.appendChild(el('span', null, fmtClock(row.ts)));
    tr.appendChild(el('span', null, short(row.harness, 16)));
    tr.appendChild(el('span', null, ruleText));
    tr.appendChild(modelChips(reqShort, routed));
    tr.appendChild(el('span', null, fmtCost(row.cost_usd)));
  } else {
    const cells = [
      escalatedId(row),
      fmtClock(row.ts),
      short(row.harness, 16),
      ruleText,
      null,
      tok,
      fmtCost(row.cost_usd),
      fmtLatency(row.latency_ms),
    ];
    cells.forEach((text, i) => {
      if (i === 4) {
        tr.appendChild(modelChips(reqShort, routed));
      } else {
        const span = el('span', null, text);
        if (i >= 5) span.classList.add('col-right');
        tr.appendChild(span);
      }
    });
  }

  tr.addEventListener('click', () => openDetail(row.id));
  return tr;
}

function renderLiveRows() {
  const container = document.getElementById('live-rows');
  container.replaceChildren();

  if (!state.rows.length) {
    const empty = el('div', 'panel-empty', 'waiting for ledger');
    empty.style.padding = '12px';
    container.appendChild(empty);
    return;
  }

  const display = state.rows.slice(-200);
  for (const row of display) {
    container.appendChild(buildRequestRow(row, 'live'));
  }
}

function renderLive() {
  renderLiveByModel();
  renderLiveByRule();
  renderLiveRows();
}

/* ── Render: Stats tab ── */

function renderSparkline() {
  const svg = document.getElementById('sparkline-svg');
  const label = document.getElementById('sparkline-label');
  label.textContent = `SPEND TREND — ${RANGE_LABELS[state.statsRange]}`;

  const buckets = state.spendTrend;
  svg.replaceChildren();

  if (!buckets.length) return;

  const costs = buckets.map((b) => (b.cost != null ? b.cost : 0));
  const maxCost = Math.max(...costs, 0.0001);
  const w = 700;
  const h = 100;
  const pad = 6;
  const n = buckets.length;

  const points = buckets.map((b, i) => {
    const x = n === 1 ? w / 2 : (i / (n - 1)) * w;
    const c = b.cost != null ? b.cost : 0;
    const y = h - pad - (c / maxCost) * (h - pad * 2);
    return `${x},${y}`;
  });

  const linePoints = points.join(' ');
  const areaPoints = `${linePoints} ${w},${h} 0,${h}`;

  const area = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  area.setAttribute('points', areaPoints);
  area.setAttribute('fill', 'rgba(88,166,255,.08)');
  area.setAttribute('stroke', 'none');
  svg.appendChild(area);

  const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  line.setAttribute('points', linePoints);
  line.setAttribute('fill', 'none');
  line.setAttribute('stroke', '#58a6ff');
  line.setAttribute('stroke-width', '2.5');
  svg.appendChild(line);
}

function renderStatsByModel() {
  const container = document.getElementById('stats-by-model');
  container.replaceChildren();

  const models = state.summary?.by_model || [];
  if (!models.length) {
    container.appendChild(el('div', 'panel-empty', 'no requests yet'));
    return;
  }

  const costs = models.map((m) => (m[4] != null ? m[4] : 0));
  const maxCost = Math.max(...costs, 0.0001);

  for (const [model, calls, , , cost] of models) {
    const row = el('div', 'stats-model-row');
    const c = cost != null ? cost : 0;
    const pct = (c / maxCost) * 100;

    row.appendChild(el('span', null, String(model || '?')));
    row.appendChild(el('span', 'col-calls', String(calls)));

    const track = el('span', 'spend-bar-track');
    const fill = el('span', 'spend-bar-fill');
    fill.style.width = `${pct}%`;
    track.appendChild(fill);
    row.appendChild(track);

    row.appendChild(el('span', 'col-cost', fmtCost(cost)));
    container.appendChild(row);
  }
}

function renderStatsByRule() {
  const container = document.getElementById('stats-by-rule');
  container.replaceChildren();

  const rules = state.summary?.by_rule || [];
  if (!rules.length) {
    container.appendChild(el('div', 'panel-empty', 'no requests yet'));
    return;
  }

  for (const [rule, calls, cost] of rules) {
    const row = el('div', 'stats-rule-row');
    row.appendChild(el('span', null, String(rule || '?')));
    row.appendChild(el('span', null, `${calls} · ${fmtCost(cost)}`));
    container.appendChild(row);
  }
}

function renderStats() {
  renderSparkline();
  renderStatsByModel();
  renderStatsByRule();
}

/* ── Render: Tail tab ── */

function getFilteredRows() {
  return state.rows.filter((row) => {
    if (state.tailModelFilter !== 'all' && row.routed_model !== state.tailModelFilter) return false;
    if (state.tailRuleFilter !== 'all' && row.rule !== state.tailRuleFilter) return false;
    return true;
  });
}

function updateTailFilters() {
  const modelSel = document.getElementById('tail-model-filter');
  const ruleSel = document.getElementById('tail-rule-filter');
  const prevModel = modelSel.value;
  const prevRule = ruleSel.value;

  const models = new Set();
  const rules = new Set();
  for (const row of state.rows) {
    if (row.routed_model) models.add(row.routed_model);
    if (row.rule) rules.add(row.rule);
  }

  modelSel.replaceChildren();
  modelSel.appendChild(new Option('all models', 'all'));
  for (const m of [...models].sort()) modelSel.appendChild(new Option(m, m));

  ruleSel.replaceChildren();
  ruleSel.appendChild(new Option('all rules', 'all'));
  for (const r of [...rules].sort()) ruleSel.appendChild(new Option(r, r));

  modelSel.value = [...modelSel.options].some((o) => o.value === prevModel) ? prevModel : 'all';
  ruleSel.value = [...ruleSel.options].some((o) => o.value === prevRule) ? prevRule : 'all';
  state.tailModelFilter = modelSel.value;
  state.tailRuleFilter = ruleSel.value;
}

function renderTail() {
  updateTailFilters();
  const container = document.getElementById('tail-rows');
  container.replaceChildren();

  const filtered = getFilteredRows();
  if (!state.rows.length) {
    container.appendChild(el('div', 'tail-empty', 'waiting for ledger'));
    return;
  }
  if (!filtered.length) {
    container.appendChild(el('div', 'tail-empty', 'no matching requests'));
    return;
  }

  const display = filtered.slice(-200);
  for (const row of display) {
    container.appendChild(buildRequestRow(row, 'tail'));
  }
}

/* ── Row detail modal ── */

function findRow(id) {
  return state.rows.find((r) => r.id === id) || null;
}

function openDetail(id) {
  const row = findRow(id);
  if (!row) return;

  state.selectedRowId = id;
  const modal = document.getElementById('detail-modal');
  const title = document.getElementById('detail-title');
  const body = document.getElementById('detail-body');

  title.textContent = `REQUEST #${row.id}`;
  body.replaceChildren();

  let rule = nullText(row.rule);
  if (row.escalated) rule += '        ← escalation retry';

  const latency = row.latency_ms != null ? `${row.latency_ms} ms` : '?';
  const inTok = fmtTokens(row.input_tokens);
  const outTok = fmtTokens(row.output_tokens);
  const est = fmtTokens(row.est_input_tokens);
  const tokens = `${inTok} in / ${outTok} out (est. input at routing: ${est})`;

  const lines = [
    ['time', `${fmtDateTime(row.ts)} (local)`],
    ['harness', nullText(row.harness)],
    ['rule', rule],
    ['req→routed', `${nullText(row.requested_model)} → ${nullText(row.routed_model)}`],
    ['stream', streamText(row.stream)],
    ['status', row.status != null ? String(row.status) : '?'],
    ['latency', latency],
    ['tokens', tokens],
    ['cost', fmtCost(row.cost_usd)],
  ];

  for (const [label, value] of lines) {
    const line = el('div', 'detail-line');
    const pad = '    ';
    line.textContent = `${label}${pad.slice(0, Math.max(0, 12 - label.length))}${value}`;
    body.appendChild(line);
  }

  modal.hidden = false;
}

function closeDetail() {
  state.selectedRowId = null;
  document.getElementById('detail-modal').hidden = true;
}

/* ── Tab switching ── */

function switchTab(tab) {
  state.activeTab = tab;
  const tabs = ['live', 'stats', 'tail', 'agents'];
  for (const t of tabs) {
    const panel = document.getElementById(`tab-${t}`);
    const nav = document.getElementById(`nav-${t}`);
    const active = t === tab;
    panel.hidden = !active;
    nav.classList.toggle('active', active);
  }
  if (tab === 'stats') fetchSpendTrend().then(() => renderStats());
  else refreshActiveTab();
}

function refreshActiveTab() {
  switch (state.activeTab) {
    case 'live':
      renderLive();
      break;
    case 'stats':
      renderStats();
      break;
    case 'tail':
      renderTail();
      break;
    default:
      break;
  }
}

function refreshAll() {
  renderHeader();
  renderKPIs();
  refreshActiveTab();
}

/* ── Data fetching ── */

async function fetchHealthLadder() {
  try {
    const [health, ladderData] = await Promise.all([
      apiGet('/api/health'),
      apiGet('/api/ladder'),
    ]);
    state.health = health;
    state.ladder = ladderData.ladder || [];
    setProxyDown(false);
  } catch {
    state.health = { up: false, default_model: null, error: 'unreachable' };
    setProxyDown(true);
  }
}

async function fetchSummary() {
  const days = RANGE_DAYS[state.statsRange];
  try {
    const summary = await apiGet(`/api/summary?days=${days}`);
    state.summary = summary;
    setProxyDown(false);
    renderKPIs();
    if (state.activeTab === 'live') {
      renderLiveByModel();
      renderLiveByRule();
    }
  } catch {
    setProxyDown(true);
  }
}

async function fetchSpendTrend() {
  const days = RANGE_DAYS[state.statsRange];
  try {
    const data = await apiGet(`/api/spend_trend?days=${days}&buckets=24`);
    state.spendTrend = data.buckets || [];
    setProxyDown(false);
    if (state.activeTab === 'stats') renderSparkline();
  } catch {
    setProxyDown(true);
  }
}

async function fetchInitialRows() {
  try {
    const data = await apiGet(`/api/rows/recent?n=${INITIAL_BACKFILL}`);
    mergeRows(data.rows || []);
    setProxyDown(false);
    refreshActiveTab();
  } catch {
    setProxyDown(true);
  }
}

async function pollNewRows() {
  try {
    const data = await apiGet(`/api/rows?after_id=${state.lastRowId}&limit=500`);
    if (data.rows?.length) {
      mergeRows(data.rows);
      refreshActiveTab();
    }
    setProxyDown(false);
  } catch {
    setProxyDown(true);
  }
}

async function setStatsRange(range) {
  state.statsRange = range;
  document.querySelectorAll('.range-pill').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.range === range);
  });
  await Promise.all([fetchSummary(), fetchSpendTrend()]);
  renderStats();
}

/* ── Init ── */

function bindEvents() {
  document.querySelectorAll('.nav-tab').forEach((btn) => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  document.querySelectorAll('.range-pill').forEach((btn) => {
    btn.addEventListener('click', () => setStatsRange(btn.dataset.range));
  });

  document.getElementById('tail-model-filter').addEventListener('change', (e) => {
    state.tailModelFilter = e.target.value;
    renderTail();
  });

  document.getElementById('tail-rule-filter').addEventListener('change', (e) => {
    state.tailRuleFilter = e.target.value;
    renderTail();
  });

  document.getElementById('detail-close').addEventListener('click', closeDetail);
  document.getElementById('detail-modal').addEventListener('click', (e) => {
    if (e.target.id === 'detail-modal') closeDetail();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !document.getElementById('detail-modal').hidden) {
      closeDetail();
    }
  });
}

async function init() {
  bindEvents();
  switchTab('live');

  await Promise.all([
    fetchHealthLadder(),
    fetchSummary(),
    fetchSpendTrend(),
    fetchInitialRows(),
  ]);
  refreshAll();

  setInterval(pollNewRows, TAIL_POLL_MS);
  setInterval(async () => {
    await fetchHealthLadder();
    await fetchSummary();
    if (state.activeTab === 'stats') await fetchSpendTrend();
  }, SUMMARY_POLL_MS);
}

document.addEventListener('DOMContentLoaded', init);