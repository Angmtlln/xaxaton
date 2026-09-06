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

function evidenceButton(context, evidenceId, compact = false) {
  const evidence = context.evidence.get(evidenceId);
  if (!evidence) return null;
  const button = element('button', compact ? 'evidence-jump comparison-source' : 'evidence-jump', compact ? 'ⓘ' : 'Источник');
  if (compact) button.title = `${evidence.title || 'Источник'}: ${evidence.display_value || ''}`;
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

const BANK_RISKS = { LOW: 'Низкий', MEDIUM: 'Средний', HIGH: 'Высокий' };
const ZSK_RISKS = { GREEN: 'Зелёный', YELLOW: 'Жёлтый', RED: 'Красный' };

function comparisonCoverage(block, column, index) {
  const rows = safeArray(block.rows);
  const total = column.total_count ?? rows.length;
  const filled = column.filled_count ?? rows.filter(row => row.cells?.[index]?.state === 'data').length;
  return `${filled} из ${total} показателей`;
}

function renderComparisonSummaries(block, context) {
  const section = element('section', 'comparison-overview');
  section.appendChild(element('h2', null, 'Контрагенты в сравнении'));
  section.appendChild(element('p', 'comparison-browse', 'Листайте карточки, чтобы увидеть всех контрагентов →'));
  const grid = element('div', 'comparison-summaries');
  grid.tabIndex = 0;
  grid.setAttribute('role', 'region');
  grid.setAttribute('aria-label', 'Карточки контрагентов — прокрутка по горизонтали');
  safeArray(block.columns).forEach((column, index) => {
    const card = element('article', 'comparison-summary');
    card.append(element('h3', null, column.name || 'Контрагент'),
      element('p', 'comparison-inn', `ИНН ${column.inn || '—'}`));
    const coverage = element('p', 'comparison-coverage', comparisonCoverage(block, column, index));
    coverage.title = 'Заполненные показатели этой таблицы; полнота исходных разделов может отличаться.';
    card.appendChild(coverage);
    const status = element('div', 'comparison-ratings');
    const bank = BANK_RISKS[column.bank_risk_level];
    const risk = element('span', 'comparison-bank-risk', `Риск по данным банка: ${bank || '—'}`);
    risk.title = bank ? 'Оценка банка из исходного отчёта, не общий рейтинг AI.' : 'Источник не предоставил банковскую оценку риска.';
    status.appendChild(risk);
    if (ZSK_RISKS[column.zsk_risk_level]) {
      status.appendChild(element('span', null, `ЗСК: ${ZSK_RISKS[column.zsk_risk_level]}`));
    }
    card.appendChild(status);
    if (column.coverage_scope) card.appendChild(element('p', 'comparison-scope', column.coverage_scope));
    const facts = safeArray(column.key_facts);
    if (facts.length) {
      const list = element('ul', 'comparison-key-facts');
      facts.forEach((fact) => {
        const item = element('li');
        item.append(element('span', null, fact.label), element('strong', null, fact.display_value));
        const button = evidenceButton(context, fact.evidence_id, true);
        if (button) item.appendChild(button);
        list.appendChild(item);
      });
      card.appendChild(list);
    } else {
      card.appendChild(element('p', 'comparison-scope', 'Ключевые факты недоступны.'));
    }
    const gaps = safeArray(column.gaps);
    if (gaps.length) {
      const details = element('details', 'comparison-gaps');
      details.appendChild(element('summary', null, 'Пробелы данных'));
      gaps.forEach((gap) => details.appendChild(element('p', null, gap)));
      card.appendChild(details);
    }
    grid.appendChild(card);
  });
  section.appendChild(grid);
  return section;
}

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
  scroller.tabIndex = 0;
  scroller.setAttribute('role', 'region');
  scroller.setAttribute('aria-label', 'Детальное сравнение — прокрутка по горизонтали');
  const table = element('table', 'comparison-table');
  const head = element('thead');
  const headRow = element('tr');
  headRow.appendChild(element('th', 'comparison-measure', 'Показатель'));
  columns.forEach((column) => {
    const cell = element('th');
    cell.scope = 'col';
    cell.append(
      element('span', 'comparison-company', column.name || 'Контрагент'),
      element('span', 'comparison-inn', `ИНН ${column.inn || '—'}`),
    );
    headRow.appendChild(cell);
  });
  head.appendChild(headRow);

  const body = element('tbody');
  const groups = [
    ['finance', 'Финансы'], ['courts', 'Судебная нагрузка'],
    ['enforcement', 'Исполнительные производства'], ['regulatory', 'Надзорные проверки'],
  ];
  // Старые сохранённые ответы не содержат метаданных различий.
  const sectionOf = row => row.id?.startsWith('court.') ? 'courts'
    : row.id?.startsWith('execproc.') ? 'enforcement'
    : row.id?.startsWith('inspections.') ? 'regulatory' : row.section || 'finance';
  const controls = element('div', 'comparison-controls');
  controls.setAttribute('role', 'group');
  controls.setAttribute('aria-label', 'Показатели сравнения');
  const all = element('button', null, 'Все показатели');
  const differences = element('button', null, 'Только ключевые различия');
  const explanation = 'Различающиеся финансы, судебная нагрузка и действующие взыскания за сопоставимые периоды. Пропуски и общее число проверок или производств не включены.';
  differences.title = explanation;
  if (rows.some(row => typeof row.is_key_difference !== 'boolean')) {
    differences.disabled = true;
    differences.title = 'Повторите сравнение, чтобы получить проверенные различия для этого сохранённого ответа.';
  }
  const notice = element('p', 'comparison-filter-note');
  notice.setAttribute('aria-live', 'polite');
  all.type = differences.type = 'button';
  controls.append(all, differences);
  card.append(controls, notice);
  const renderRows = onlyDifferences => {
    all.setAttribute('aria-pressed', String(!onlyDifferences));
    differences.setAttribute('aria-pressed', String(onlyDifferences));
    const selected = rows.filter(row => !onlyDifferences || row.is_key_difference === true);
    notice.textContent = onlyDifferences
      ? `Показано ${selected.length} из ${rows.length}. ${explanation}` : '';
    notice.hidden = !onlyDifferences;
    body.replaceChildren();
    groups.forEach(([key, title]) => {
      const members = selected.filter(row => sectionOf(row) === key);
      if (!members.length) return;
      const heading = element('tr', 'comparison-section');
      const group = element('th', null, title);
      group.colSpan = columns.length + 1;
      heading.appendChild(group);
      body.appendChild(heading);
      members.forEach(row => {
        const line = element('tr');
        line.dataset.measure = row.id;
        const label = element('th', 'comparison-measure', row.label || 'Показатель');
        label.scope = 'row';
        line.appendChild(label);
        safeArray(row.cells).forEach(cell => {
          const item = element('td', `comparison-cell cell-${cell.state || 'no_data'}`);
          const content = element('span', 'comparison-cell-content');
          const missing = cell.state !== 'data';
          const value = element('span', 'comparison-value', missing ? '—' : cell.display_value);
          if (missing) {
            value.title = 'Источник не предоставил значение';
            value.setAttribute('aria-label', 'Источник не предоставил значение');
            value.tabIndex = 0;
          }
          content.appendChild(value);
          if (!missing && cell.evidence_id) {
            const button = evidenceButton(context, cell.evidence_id, true);
            if (button) content.appendChild(button);
          }
          item.appendChild(content);
          line.appendChild(item);
        });
        body.appendChild(line);
      });
    });
    if (!selected.length) {
      const row = element('tr');
      const cell = element('td', 'chat-empty-state', 'Ключевых различий по сопоставимым данным нет. Все показатели доступны в соседней вкладке.');
      cell.colSpan = columns.length + 1;
      row.appendChild(cell);
      body.appendChild(row);
    }
  };
  all.addEventListener('click', () => renderRows(false));
  differences.addEventListener('click', () => renderRows(true));
  renderRows(false);

  table.append(head, body);
  scroller.appendChild(table);
  card.appendChild(scroller);
  return card;
}

const BLOCK_RENDERERS = {
  company_card: renderDashboard,
  text: renderTextBlock,
  metric_grid: renderMetricGrid,
  line_chart: renderLineChart,
  finding_list: renderFindingList,
  comparison_table: renderComparisonTable,
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
  const comparison = safeArray(payload.blocks).find((block) => block?.type === 'comparison_table');
  if (comparison) body.appendChild(renderComparisonSummaries(comparison, context));
  if (payload.leading_artifact) {
    body.appendChild(payload.leading_artifact.type === 'company_summary'
      ? renderDashboard(payload.leading_artifact, context) : renderUnsupportedBlock());
  }
  const lead = element('div', 'assistant-lead');
  if (comparison) {
    lead.classList.add('comparison-conclusion');
    lead.appendChild(element('h3', null, metadata.synthesis === 'model' ? 'AI-вывод' : 'Краткий итог'));
  }
  if (metadata.status && !['completed', 'partial'].includes(metadata.status)) lead.appendChild(statusBadge(metadata.status));
  appendProse(lead, payload.message || 'Ответ не сформирован.');
  body.appendChild(lead);

  const blocks = safeArray(payload.blocks).filter((block) => !block || (block.type !== 'evidence_list'
    && !(comparison && block.type === 'finding_list' && block.title === 'Метки источника')));
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
