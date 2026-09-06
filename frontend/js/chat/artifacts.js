/* Рендер allowlisted UIBlock из ответа агента. Модуль не знает о состоянии
   диалога: всё, что нужно снаружи, приходит через context и hooks. */
import { element, safeArray, numericValue } from '../shared/dom.js';
import { buildChart } from './chart.js';
import { renderDashboard } from './dashboard.js';
import { renderConnections } from './connections.js';
import { renderNews } from './news.js';
import { appendProse } from './prose.js';

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

export function statusBadge(status) {
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
    target.tabIndex = -1;
    target.focus({ preventScroll: true });
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

function renderFindingList(block, context) {
  const card = element('section', 'rich-block finding-block');
  if (block.title === 'Метки источника') card.classList.add('policy-block');
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

const AVAILABILITY_LABELS = {
  DATA: 'Данные есть',
  PARTIAL: 'Данные неполны',
  NO_DATA: 'Данных нет',
};

function renderComparisonTable(block, context) {
  const card = element('section', 'rich-block comparison-block');
  card.appendChild(element('h3', null, block.title || 'Сравнение контрагентов'));
  const columns = safeArray(block.columns);
  const rows = safeArray(block.rows);
  if (!columns.length || !rows.length) {
    card.appendChild(element('div', 'chat-empty-state',
      block.empty_message || 'Сопоставимых показателей нет.'));
    return card;
  }

  const scroller = element('div', 'comparison-scroll');
  const table = element('table', 'comparison-table');
  const head = element('thead');
  const headRow = element('tr');
  headRow.appendChild(element('th', 'comparison-measure', 'Показатель'));
  columns.forEach((column) => {
    const cell = element('th');
    cell.append(
      element('span', 'comparison-company', column.name || 'Контрагент'),
      element('span', 'comparison-inn', `ИНН ${column.inn || '—'}`),
      element('span', `comparison-availability availability-${column.availability || 'NO_DATA'}`,
        AVAILABILITY_LABELS[column.availability] || 'Данных нет'),
    );
    headRow.appendChild(cell);
  });
  head.appendChild(headRow);

  const body = element('tbody');
  rows.forEach((row) => {
    const line = element('tr');
    const label = element('th', 'comparison-measure', row.label || 'Показатель');
    label.scope = 'row';
    line.appendChild(label);
    safeArray(row.cells).forEach((cell) => {
      const item = element('td', `comparison-cell cell-${cell.state || 'no_data'}`);
      item.appendChild(element('span', 'comparison-value', cell.display_value || 'Нет данных'));
      if (cell.evidence_id) {
        const button = evidenceButton(context, cell.evidence_id);
        if (button) item.appendChild(button);
      }
      line.appendChild(item);
    });
    body.appendChild(line);
  });

  table.append(head, body);
  scroller.appendChild(table);
  card.appendChild(scroller);
  return card;
}

function companyWord(count) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'компания';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'компании';
  return 'компаний';
}

function renderCompanyShortlist(block) {
  const card = element('section', 'rich-block shortlist-block');
  card.appendChild(element('h3', null, block.title || 'Подходят под условие'));

  const criteria = safeArray(block.criteria);
  if (criteria.length) {
    const chips = element('div', 'shortlist-criteria');
    criteria.forEach((item) => chips.appendChild(element('span', 'shortlist-chip', item)));
    card.appendChild(chips);
  }

  const rows = safeArray(block.rows);
  if (!rows.length) {
    card.appendChild(element('div', 'chat-empty-state',
      block.empty_message || 'Под эти критерии карточек не нашлось.'));
    return card;
  }

  const total = Number(block.total) || rows.length;
  const matched = `${total} ${companyWord(total)}`;
  card.appendChild(element('p', 'shortlist-total',
    total > rows.length
      ? `Под условие подходит ${matched}, показаны первые ${rows.length}.`
      : `Под условие подходит ${matched}.`));

  const scroller = element('div', 'comparison-scroll');
  const table = element('table', 'comparison-table shortlist-table');
  const head = element('thead');
  const headRow = element('tr');
  ['Компания', 'Выручка', 'Прибыль', 'Иски', 'Исп. производств', 'Стоп-факторы', 'Банк / ЗСК']
    .forEach((label) => headRow.appendChild(element('th', null, label)));
  head.appendChild(headRow);

  const body = element('tbody');
  rows.forEach((row) => {
    const line = element('tr');
    const company = element('th', 'comparison-measure');
    company.scope = 'row';
    company.append(
      element('span', 'comparison-company', row.name || 'Контрагент'),
      element('span', 'comparison-inn', `ИНН ${row.inn || '—'}${row.fin_year ? ` · ${row.fin_year}` : ''}`),
    );
    line.appendChild(company);
    line.appendChild(element('td', 'comparison-cell', row.proceeds_display || 'Нет данных'));
    line.appendChild(element('td', 'comparison-cell', row.profit_display || 'Нет данных'));
    line.appendChild(element('td', 'comparison-cell', row.claims_display || 'Нет данных'));
    line.appendChild(element('td', 'comparison-cell', String(row.enforcement_count ?? 0)));
    const stops = Number(row.hard_stops) || 0;
    const stopCell = element('td', `comparison-cell shortlist-stops-${stops ? 'yes' : 'no'}`);
    stopCell.appendChild(element('span', null, stops ? String(stops) : 'нет'));
    line.appendChild(stopCell);
    line.appendChild(element('td', 'comparison-cell',
      `${row.risk_level || 'UNKNOWN'} / ${row.zsk_risk_level || 'UNKNOWN'}`));
    body.appendChild(line);
  });

  table.append(head, body);
  scroller.appendChild(table);
  card.appendChild(scroller);
  card.appendChild(element('p', 'shortlist-hint',
    'Назовите два-три ИНН из списка, чтобы сравнить их подробно.'));
  return card;
}

const BLOCK_RENDERERS = {
  company_card: renderDashboard,
  text: renderTextBlock,
  metric_grid: renderMetricGrid,
  line_chart: renderLineChart,
  finding_list: renderFindingList,
  comparison_table: renderComparisonTable,
  company_shortlist: renderCompanyShortlist,
  connection_graph: renderConnections,
  evidence_list: renderEvidenceList,
};

function renderUnsupportedBlock() {
  const fallback = element('section', 'rich-block unsupported-block');
  fallback.append(
    element('p', null, 'Не удалось показать дополнительный материал. Основной ответ доступен выше.'),
  );
  return fallback;
}

export function buildAssistantMessage(payload, hooks = {}) {
  const article = element('article', 'chat-message chat-message-assistant');
  const avatar = element('div', 'assistant-avatar', 'A');
  avatar.setAttribute('aria-hidden', 'true');
  const body = element('div', 'assistant-content');
  article.setAttribute('aria-label', 'AI-аналитик');
  const metadata = payload && payload.metadata ? payload.metadata : {};
  const author = element('div', 'assistant-author', 'AI-аналитик');
  body.appendChild(author);
  const prefix = `${String(metadata.agent_run_id || Date.now()).replace(/[^a-zA-Z0-9_-]/g, '')}-${hooks.index || 0}`;
  const context = {
    prefix,
    evidence: new Map(safeArray(payload.evidence).filter((item) => item && item.id)
      .map((item) => [item.id, item])),
    onReportUrl: hooks.onReportUrl,
    onSuggestion: hooks.onSuggestion,
  };
  context.evidenceButton = (id) => evidenceButton(context, id);
  if (payload.leading_artifact) {
    body.appendChild(payload.leading_artifact.type === 'company_summary'
      ? renderDashboard(payload.leading_artifact, context) : renderUnsupportedBlock());
  }
  const lead = element('div', 'assistant-lead');
  if (metadata.status && metadata.status !== 'completed') lead.appendChild(statusBadge(metadata.status));
  appendProse(lead, payload.message || 'Ответ не сформирован.');
  body.appendChild(lead);

  const blocks = safeArray(payload.blocks).filter((block) => !block || block.type !== 'evidence_list');
  if (blocks.length) {
    const stack = element('div', 'rich-stack');
    blocks.forEach((block) => {
      const renderer = block && Object.hasOwn(BLOCK_RENDERERS, block.type) && BLOCK_RENDERERS[block.type];
      stack.appendChild(renderer ? renderer(block, context) : renderUnsupportedBlock());
    });
    body.appendChild(stack);
  }
  if (context.evidence.size) body.appendChild(renderEvidenceList({
    title: 'Источники ответа', evidence_ids: [...context.evidence.keys()],
  }, context));

  const actions = safeArray(payload.suggested_actions);
  if (actions.length) {
    const actionRow = element('div', 'suggested-actions');
    actions.forEach((action) => {
      const suggestion = typeof action === 'string'
        ? { label: action, prompt: action, mode: 'compose' } : action;
      if (!suggestion || typeof suggestion.label !== 'string' || typeof suggestion.prompt !== 'string') return;
      const button = element('button', 'suggested-action', suggestion.label);
      button.type = 'button';
      button.addEventListener('click', () => {
        if (hooks.onSuggestion) hooks.onSuggestion(suggestion);
      });
      actionRow.appendChild(button);
    });
    body.appendChild(actionRow);
  }

  const news = renderNews(payload, prefix);
  if (news) body.appendChild(news);

  article.append(avatar, body);
  return article;
}
