/* Agent-first chat: POST /api/v1/chat/messages и allowlisted UIBlock renderer. */
'use strict';

const form = document.getElementById('chat-form');
const input = document.getElementById('chat-input');
const sendButton = document.getElementById('send-button');
const intro = document.getElementById('chat-intro');
const thread = document.getElementById('chat-thread');
const lastReportLink = document.getElementById('last-report-link');

const moneyFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const integerFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const SVG_NS = 'http://www.w3.org/2000/svg';

const STATUS_LABELS = {
  completed: 'Проверка завершена',
  partial: 'Частичный результат',
  needs_input: 'Нужны данные',
  error: 'Ошибка проверки',
};

const SOURCE_LABELS = {
  raw_fact: 'Исходное поле',
  derived_metric: 'Расчётный факт',
  source_signal: 'Сигнал источника',
};

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

function svgElement(tag, attributes = {}) {
  const item = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([key, value]) => item.setAttribute(key, String(value)));
  return item;
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function numericValue(value) {
  if (value === null || value === undefined || value === '') return NaN;
  return Number(value);
}

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
}

function setBusy(value) {
  input.disabled = value;
  sendButton.disabled = value;
  form.toggleAttribute('aria-busy', value);
}

function scrollToLatest() {
  window.requestAnimationFrame(() => {
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  });
}

function appendUserMessage(message) {
  const article = element('article', 'chat-message chat-message-user');
  const bubble = element('div', 'user-bubble', message);
  article.appendChild(bubble);
  thread.appendChild(article);
}

function appendLoading() {
  const article = element('article', 'chat-message chat-message-assistant loading-message');
  article.id = 'active-loading';
  const avatar = element('div', 'assistant-avatar', 'A');
  avatar.setAttribute('aria-hidden', 'true');
  const body = element('div', 'assistant-content assistant-loading');
  const title = element('strong', null, 'Запускаю полную проверку');
  const note = element('span', null, 'Собираю факты, анализирую четыре блока и проверяю evidence');
  const dots = element('span', 'loading-dots');
  dots.setAttribute('aria-hidden', 'true');
  dots.append(element('i'), element('i'), element('i'));
  body.append(title, note, dots);
  article.append(avatar, body);
  thread.appendChild(article);
  return article;
}

function statusBadge(status) {
  const badge = element('span', `assistant-status status-${status || 'error'}`,
    STATUS_LABELS[status] || 'Ответ агента');
  return badge;
}

function evidenceDomId(context, evidenceId) {
  const safe = String(evidenceId).replace(/[^a-zA-Z0-9_-]/g, '-');
  return `evidence-${context.prefix}-${safe}`;
}

function evidenceButton(context, evidenceId) {
  const evidence = context.evidence.get(evidenceId);
  if (!evidence) return null;
  const button = element('button', 'evidence-jump', 'Источник');
  button.type = 'button';
  button.setAttribute('aria-label', `Источник: ${evidence.title || evidence.fact_id || evidenceId}`);
  button.addEventListener('click', () => {
    const target = document.getElementById(evidenceDomId(context, evidenceId));
    if (!target) return;
    const details = target.closest('details');
    if (details) details.open = true;
    window.requestAnimationFrame(() => {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.classList.add('evidence-highlight');
      window.setTimeout(() => target.classList.remove('evidence-highlight'), 1600);
    });
  });
  return button;
}

function appendEvidenceButtons(parent, context, ids) {
  const buttons = safeArray(ids).map((id) => evidenceButton(context, id)).filter(Boolean);
  if (!buttons.length) return;
  const row = element('div', 'block-evidence-links');
  buttons.forEach((button) => row.appendChild(button));
  parent.appendChild(row);
}

function renderCompanyCard(block, context) {
  const card = element('section', 'rich-block company-card-block');
  const top = element('div', 'company-card-top');
  const identity = element('div', 'company-card-identity');
  identity.append(
    element('span', 'block-eyebrow', 'Контрагент'),
    element('h2', null, block.name || 'Контрагент'),
    element('p', null, `ИНН ${block.inn || 'не указан'}${block.ogrn ? ` · ОГРН ${block.ogrn}` : ''}`),
  );
  const reportLink = element('a', 'company-report-link', 'Полный отчёт →');
  reportLink.href = block.report_url || `/report?inn=${encodeURIComponent(block.inn || '')}`;
  top.append(identity, reportLink);

  const details = element('dl', 'company-data-grid');
  addDefinition(details, 'Статус', block.status || 'Нет данных');
  addDefinition(details, 'Возраст', block.years_from_registration == null
    ? 'Нет данных' : `${block.years_from_registration} лет`);
  addDefinition(details, 'Оценка банка', block.bank_risk_level || 'Нет данных');
  addDefinition(details, 'Светофор ЗСК', block.zsk_risk_level || 'Нет данных');
  if (block.address) addDefinition(details, 'Адрес', block.address, true);
  if (block.report_date) addDefinition(details, 'Дата карточки', block.report_date);

  card.append(top, details);
  appendEvidenceButtons(card, context, block.evidence_ids);
  if (block.report_url) {
    lastReportLink.href = block.report_url;
    lastReportLink.hidden = false;
  }
  return card;
}

function addDefinition(list, label, value, wide = false) {
  const group = element('div', `company-data-item${wide ? ' company-data-wide' : ''}`);
  group.append(element('dt', null, label), element('dd', null, value));
  list.appendChild(group);
}

function renderTextBlock(block, context) {
  const card = element('section', 'rich-block text-block');
  if (block.title) card.appendChild(element('h3', null, block.title));
  card.appendChild(element('p', null, block.text || 'Ответ не сформирован.'));
  appendEvidenceButtons(card, context, block.evidence_ids);
  return card;
}

function renderMetricGrid(block, context) {
  const card = element('section', 'rich-block metric-block');
  card.appendChild(element('h3', null, block.title || 'Показатели'));
  const grid = element('div', 'chat-metric-grid');
  safeArray(block.items).forEach((item) => {
    const metric = element('article', `chat-metric metric-${item.state || 'no_data'}`);
    metric.append(
      element('span', 'chat-metric-label', item.label || 'Показатель'),
      element('strong', 'chat-metric-value', item.display_value || 'Нет данных'),
    );
    if (item.evidence_id) {
      const button = evidenceButton(context, item.evidence_id);
      if (button) metric.appendChild(button);
    }
    grid.appendChild(metric);
  });
  card.appendChild(grid);
  return card;
}

function renderLineChart(block, context) {
  const card = element('section', 'rich-block chat-chart-block');
  const heading = element('div', 'rich-block-heading');
  const copy = element('div');
  copy.append(
    element('h3', null, block.title || 'Динамика'),
    element('p', null, block.description || ''),
  );
  heading.appendChild(copy);
  card.appendChild(heading);

  const series = safeArray(block.series).filter((item) => safeArray(item.points).length);
  const values = series.flatMap((item) => safeArray(item.points))
    .map((point) => numericValue(point.value)).filter(Number.isFinite);
  if (block.state !== 'data' || !values.length) {
    card.appendChild(element('div', 'chat-empty-state', block.empty_message || 'Нет данных для графика.'));
    return card;
  }

  const chart = buildChart(series, block.unit);
  card.append(chart.figure, chart.table);
  const ids = [...new Set(series.map((item) => item.evidence_id).filter(Boolean))];
  appendEvidenceButtons(card, context, ids);
  return card;
}

function buildChart(series, unit) {
  const width = 760;
  const height = 286;
  const padding = { top: 24, right: 24, bottom: 48, left: 74 };
  const allValues = series.flatMap((item) => safeArray(item.points))
    .map((point) => numericValue(point.value)).filter(Number.isFinite);
  let min = Math.min(0, ...allValues);
  let max = Math.max(0, ...allValues);
  if (min === max) max = min + 1;
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maxPoints = Math.max(...series.map((item) => item.points.length), 1);
  const xFor = (index) => padding.left + (maxPoints === 1 ? plotWidth / 2
    : index / (maxPoints - 1) * plotWidth);
  const yFor = (value) => padding.top + (max - value) / (max - min) * plotHeight;

  const figure = element('figure', 'chat-chart');
  const svg = svgElement('svg', {
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': 'График финансовой динамики по данным карточки',
  });

  for (let index = 0; index <= 4; index += 1) {
    const value = max - (max - min) * index / 4;
    const y = yFor(value);
    svg.appendChild(svgElement('line', {
      x1: padding.left, y1: y, x2: width - padding.right, y2: y,
      class: 'chart-grid-line',
    }));
    const label = svgElement('text', {
      x: padding.left - 12, y: y + 4, 'text-anchor': 'end', class: 'chart-axis-label',
    });
    label.textContent = compactNumber(value, unit);
    svg.appendChild(label);
  }

  const xLabels = series[0].points;
  xLabels.forEach((point, index) => {
    const label = svgElement('text', {
      x: xFor(index), y: height - 16, 'text-anchor': 'middle', class: 'chart-axis-label',
    });
    label.textContent = String(point.x || '');
    svg.appendChild(label);
  });

  const styles = [
    { color: '#111111', dash: '', shape: 'circle' },
    { color: '#ef3124', dash: '9 7', shape: 'square' },
  ];
  series.forEach((item, seriesIndex) => {
    const style = styles[seriesIndex % styles.length];
    let pathValue = '';
    let segmentOpen = false;
    item.points.forEach((point, index) => {
      const value = numericValue(point.value);
      if (!Number.isFinite(value)) {
        segmentOpen = false;
        return;
      }
      pathValue += `${segmentOpen ? 'L' : 'M'}${xFor(index)} ${yFor(value)} `;
      segmentOpen = true;
    });
    svg.appendChild(svgElement('path', {
      d: pathValue.trim(), fill: 'none', stroke: style.color, 'stroke-width': 3,
      'stroke-dasharray': style.dash, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    }));
    item.points.forEach((point, index) => {
      const value = numericValue(point.value);
      if (!Number.isFinite(value)) return;
      if (style.shape === 'square') {
        svg.appendChild(svgElement('rect', {
          x: xFor(index) - 4, y: yFor(value) - 4, width: 8, height: 8, fill: style.color,
        }));
      } else {
        svg.appendChild(svgElement('circle', {
          cx: xFor(index), cy: yFor(value), r: 4, fill: '#ffffff',
          stroke: style.color, 'stroke-width': 3,
        }));
      }
    });
  });

  const legend = element('figcaption', 'chat-chart-legend');
  series.forEach((item, index) => {
    const entry = element('span');
    const key = element('i', index % 2 ? 'legend-line legend-dashed' : 'legend-line');
    entry.append(key, document.createTextNode(item.label || item.key || 'Ряд'));
    legend.appendChild(entry);
  });
  figure.append(svg, legend);

  const details = element('details', 'chart-data-details');
  details.appendChild(element('summary', null, 'Показать значения графика'));
  const table = element('table', 'chart-data-table');
  const thead = element('thead');
  const header = element('tr');
  header.appendChild(element('th', null, 'Год'));
  series.forEach((item) => header.appendChild(element('th', null, item.label || item.key)));
  thead.appendChild(header);
  const tbody = element('tbody');
  xLabels.forEach((point, index) => {
    const row = element('tr');
    row.appendChild(element('td', null, point.x || '—'));
    series.forEach((item) => {
      const value = item.points[index] ? numericValue(item.points[index].value) : NaN;
      row.appendChild(element('td', null, Number.isFinite(value) ? formatChartValue(value, unit) : 'Нет данных'));
    });
    tbody.appendChild(row);
  });
  table.append(thead, tbody);
  details.appendChild(table);
  return { figure, table: details };
}

function compactNumber(value, unit) {
  const abs = Math.abs(value);
  let text;
  if (abs >= 1e9) text = `${moneyFormat.format(value / 1e9)} млрд`;
  else if (abs >= 1e6) text = `${moneyFormat.format(value / 1e6)} млн`;
  else if (abs >= 1e3) text = `${moneyFormat.format(value / 1e3)} тыс`;
  else text = integerFormat.format(value);
  return unit === 'руб' ? `${text} ₽` : text;
}

function formatChartValue(value, unit) {
  const text = moneyFormat.format(value);
  return unit === 'руб' ? `${text} ₽` : text;
}

function renderFindingList(block, context) {
  const card = element('section', 'rich-block finding-block');
  card.appendChild(element('h3', null, block.title || 'Наблюдения'));
  const items = safeArray(block.items);
  if (!items.length) {
    card.appendChild(element('div', 'chat-empty-state', block.empty_message || 'Наблюдений нет.'));
    return card;
  }
  const list = element('div', 'finding-list');
  items.forEach((item) => {
    const row = element('article', 'finding-item');
    const marker = element('span', 'finding-marker', '•');
    marker.setAttribute('aria-hidden', 'true');
    const copy = element('div');
    copy.append(element('strong', null, item.title || 'Наблюдение'), element('p', null, item.text || ''));
    appendEvidenceButtons(copy, context, item.evidence_ids);
    row.append(marker, copy);
    list.appendChild(row);
  });
  card.appendChild(list);
  return card;
}

function renderEvidenceList(block, context) {
  const details = element('details', 'rich-block evidence-block');
  const ids = safeArray(block.evidence_ids).filter((id) => context.evidence.has(id));
  const summary = element('summary');
  const summaryCopy = element('span');
  summaryCopy.append(
    element('strong', null, block.title || 'Источники'),
    element('small', null, `${ids.length} ${evidenceWord(ids.length)}`),
  );
  summary.append(summaryCopy, element('span', 'evidence-toggle', 'Показать'));
  details.appendChild(summary);

  const list = element('div', 'evidence-list');
  ids.forEach((id) => {
    const item = context.evidence.get(id);
    const row = element('article', 'evidence-item');
    row.id = evidenceDomId(context, id);
    const heading = element('div', 'evidence-item-heading');
    heading.append(
      element('strong', null, item.title || item.fact_id || id),
      element('span', null, SOURCE_LABELS[item.source] || 'Факт'),
    );
    row.append(
      heading,
      element('p', 'evidence-value', item.display_value || 'Нет данных'),
      element('code', null, item.field_ref || ''),
    );
    list.appendChild(row);
  });
  details.appendChild(list);
  details.addEventListener('toggle', () => {
    const toggle = details.querySelector('.evidence-toggle');
    if (toggle) toggle.textContent = details.open ? 'Скрыть' : 'Показать';
  });
  return details;
}

function evidenceWord(count) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'источник';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'источника';
  return 'источников';
}

const BLOCK_RENDERERS = {
  company_card: renderCompanyCard,
  text: renderTextBlock,
  metric_grid: renderMetricGrid,
  line_chart: renderLineChart,
  finding_list: renderFindingList,
  evidence_list: renderEvidenceList,
};

function renderUnsupportedBlock() {
  const fallback = element('section', 'rich-block unsupported-block');
  fallback.append(
    element('h3', null, 'Блок не поддерживается'),
    element('p', null, 'Этот формат ответа не входит в allowlist текущей версии.'),
  );
  return fallback;
}

function appendAssistantMessage(payload) {
  const article = element('article', 'chat-message chat-message-assistant');
  const avatar = element('div', 'assistant-avatar', 'A');
  avatar.setAttribute('aria-hidden', 'true');
  const body = element('div', 'assistant-content');
  const lead = element('div', 'assistant-lead');
  const metadata = payload && payload.metadata ? payload.metadata : {};
  lead.append(statusBadge(metadata.status), element('p', null, payload.message || 'Ответ не сформирован.'));
  body.appendChild(lead);

  const prefix = String(metadata.agent_run_id || Date.now()).replace(/[^a-zA-Z0-9_-]/g, '');
  const context = {
    prefix,
    evidence: new Map(safeArray(payload.evidence).map((item) => [item.id, item])),
  };
  const stack = element('div', 'rich-stack');
  safeArray(payload.blocks).forEach((block) => {
    const renderer = block && BLOCK_RENDERERS[block.type];
    stack.appendChild(renderer ? renderer(block, context) : renderUnsupportedBlock());
  });
  body.appendChild(stack);

  const actions = safeArray(payload.suggested_actions);
  if (actions.length) {
    const actionRow = element('div', 'suggested-actions');
    actions.forEach((action) => {
      const button = element('button', 'suggested-action', action);
      button.type = 'button';
      button.addEventListener('click', () => {
        input.value = action;
        resizeInput();
        input.focus();
      });
      actionRow.appendChild(button);
    });
    body.appendChild(actionRow);
  }

  article.append(avatar, body);
  thread.appendChild(article);
}

function appendRequestError(message) {
  appendAssistantMessage({
    message,
    blocks: [{ type: 'text', title: 'Не удалось получить ответ', text: message }],
    evidence: [],
    suggested_actions: [],
    metadata: { status: 'error', agent_run_id: `client-${Date.now()}` },
  });
}

async function sendMessage(message) {
  const text = String(message || '').trim();
  if (!text || form.hasAttribute('aria-busy')) return;
  intro.hidden = true;
  thread.hidden = false;
  appendUserMessage(text);
  input.value = '';
  resizeInput();
  setBusy(true);
  const loading = appendLoading();
  scrollToLatest();

  try {
    const response = await fetch('/api/v1/chat/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    loading.remove();
    if (!response.ok) {
      const detail = payload && payload.detail;
      const messageText = Array.isArray(detail)
        ? detail.map((item) => item.msg).filter(Boolean).join('. ')
        : (detail || 'Сервис временно не ответил. Попробуйте ещё раз.');
      appendRequestError(messageText);
    } else {
      appendAssistantMessage(payload || {});
    }
  } catch (error) {
    loading.remove();
    appendRequestError('Не удалось связаться с сервисом. Проверьте соединение и повторите запрос.');
  } finally {
    setBusy(false);
    input.focus();
    scrollToLatest();
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener('input', resizeInput);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll('[data-prompt]').forEach((button) => {
  button.addEventListener('click', () => {
    const prompt = button.dataset.prompt || '';
    input.value = prompt;
    resizeInput();
    input.focus();
    if (/\d{10,12}/.test(prompt)) sendMessage(prompt);
  });
});

resizeInput();
