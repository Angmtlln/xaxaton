/* Компактная сводка: значения принадлежат backend, незаполненная роза — только схема. */
import { element, svgElement, safeArray, numericValue } from '../shared/dom.js';
import { compactNumber } from './chart.js';

const BANK = { LOW: 'Низкий', MEDIUM: 'Средний', HIGH: 'Высокий' };
const ZSK = { GREEN: 'Зелёный', YELLOW: 'Жёлтый', RED: 'Красный' };
const TONES = { LOW: 'clear', GREEN: 'clear', MEDIUM: 'unknown', YELLOW: 'unknown', HIGH: 'attention', RED: 'attention' };

function indicator(label, value, labels) {
  const item = element('span', `summary-indicator tone-${TONES[value] || 'none'}`);
  item.append(element('span', null, label), element('b', null,
    labels[value] || (value && value !== 'UNKNOWN' ? value : 'Нет данных')));
  return item;
}

function emptyRadar(prefix) {
  const panel = element('div', 'radar-panel');
  const toggle = element('button', 'radar-toggle', 'Профиль рисков');
  toggle.type = 'button';
  toggle.setAttribute('aria-label', 'Профиль рисков');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', `radar-${prefix}`);
  const figure = element('figure', 'risk-radar');
  figure.id = `radar-${prefix}`;
  const svg = svgElement('svg', { viewBox: '0 0 170 150', role: 'img',
    'aria-label': 'Профиль рисков: финансы, суды, взыскания, регуляторные риски. Три контура: низкий, средний, высокий уровень. Все направления пока не оценены.' });
  [14, 28, 42].forEach((r) => svg.appendChild(svgElement('polygon', {
    points: `85,${76-r} ${85+r},76 85,${76+r} ${85-r},76`, class: 'radar-ring',
  })));
  svg.append(svgElement('path', { d: 'M85 34V118 M43 76H127', class: 'radar-axis' }));
  const labels = [['Финансы', 85, 21, 'middle'], ['Суды', 133, 79, 'start'],
    ['Взыскания', 85, 135, 'middle'], ['Регул.', 36, 74, 'end'], ['риски', 36, 86, 'end']];
  labels.forEach(([label, x, y, anchor]) => {
    const text = svgElement('text', { x, y, 'text-anchor': anchor, class: 'radar-label' });
    text.textContent = label;
    svg.appendChild(text);
  });
  figure.append(svg, element('figcaption', null, 'Уровни не оценены'));
  toggle.addEventListener('click', () => {
    const expanded = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(expanded));
    panel.classList.toggle('radar-expanded', expanded);
  });
  panel.append(toggle, figure);
  return panel;
}

export function renderDashboard(block, context) {
  const card = element('section', 'rich-block company-summary');
  card.setAttribute('aria-label', 'Кратко о компании');
  const copy = element('div', 'summary-copy');
  const identity = element('div', 'company-summary-identity');
  identity.appendChild(element('h2', null, block.name || 'Контрагент'));
  const reportUrl = /^\/report\?inn=\d{10}(?:\d{2})?$/.test(String(block.report_url || ''))
    ? block.report_url : /^\d{10}(?:\d{2})?$/.test(String(block.inn || '')) ? `/report?inn=${block.inn}` : null;
  if (reportUrl) {
    const link = element('a', 'company-report-link', 'Полный анализ ↗');
    link.href = reportUrl;
    identity.appendChild(link);
    context.onReportUrl?.(reportUrl);
  }
  const meta = element('div', 'summary-meta');
  meta.append(element('span', null, `ИНН ${block.inn || 'не указан'}`),
    element('span', null, block.status === 'CURRENT' ? 'Действующая' : block.status || 'Статус неизвестен'));
  if (block.years_from_registration != null) meta.appendChild(element('span', null, `Возраст: ${block.years_from_registration} лет`));
  const indicators = element('div', 'summary-indicators');
  indicators.append(indicator('Банк', block.bank_risk_level, BANK), indicator('ЗСК', block.zsk_risk_level, ZSK));
  copy.append(identity, meta, indicators);
  const metrics = safeArray(block.metrics).slice(0, 4);
  if (metrics.length) {
    const grid = element('div', 'summary-metrics');
    metrics.forEach((metric) => {
      const item = element('div', 'summary-metric');
      item.appendChild(element('span', 'summary-metric-label', metric.label));
      const value = numericValue(metric.value);
      const display = metric.state === 'data'
        ? Number.isFinite(value) ? compactNumber(value, metric.unit) : metric.display_value
        : 'Нет данных';
      const button = metric.evidence_id ? context.evidenceButton?.(metric.evidence_id) : null;
      if (button) {
        button.className = 'summary-metric-value summary-metric-source';
        button.textContent = display;
        button.setAttribute('aria-label', `${metric.label}: ${metric.display_value}. Показать источник`);
        button.title = `${metric.display_value} · Источник`;
        item.appendChild(button);
      } else item.appendChild(element('strong', 'summary-metric-value', display));
      grid.appendChild(item);
    });
    copy.appendChild(grid);
  }
  card.append(copy, emptyRadar(context.prefix));
  return card;
}
