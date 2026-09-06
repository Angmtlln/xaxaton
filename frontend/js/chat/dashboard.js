/* Компактная сводка: метрики принадлежат backend, роза — отдельное качественное мнение AI. */
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

function renderRadar(prefix, profile) {
  const panel = element('div', 'radar-panel');
  const toggle = element('button', 'radar-toggle', 'Профиль рисков');
  toggle.type = 'button';
  toggle.setAttribute('aria-label', 'Профиль рисков');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', `radar-${prefix}`);
  const figure = element('figure', 'risk-radar');
  figure.id = `radar-${prefix}`;
  const svg = svgElement('svg', { viewBox: '0 0 170 150', role: 'img',
    'aria-label': 'Качественное мнение AI: финансы, суды, взыскания, регуляторные риски. Серый означает, что оценки нет.' });
  [14, 28, 42].forEach((r) => svg.appendChild(svgElement('polygon', {
    points: `85,${76-r} ${85+r},76 85,${76+r} ${85-r},76`, class: 'radar-ring',
  })));
  svg.append(svgElement('path', { d: 'M85 34V118 M43 76H127', class: 'radar-axis' }));
  const axes = [['finance', 'Финансы', 0, -1], ['courts', 'Суды', 1, 0],
    ['enforcement', 'Взыскания', 0, 1], ['regulatory', 'Регуляторные риски', -1, 0]];
  const levels = { low: 'Низкий', medium: 'Умеренный', high: 'Высокий', unknown: 'Не оценено' };
  const description = [];
  axes.forEach(([key, label, dx, dy]) => {
    const axis = profile?.[key] || { level: 'unknown', reason: 'Оценка отсутствует.' };
    const level = Object.hasOwn(levels, axis.level) ? axis.level : 'unknown';
    const r = { low: 18, medium: 30, high: 42, unknown: 9 }[level];
    const p = (x, y) => `${85 + x},${76 + y}`;
    const points = [p(0, 0), p((dx - dy) * r / 2, (dy + dx) * r / 2),
      p(dx * r, dy * r), p((dx + dy) * r / 2, (dy - dx) * r / 2)].join(' ');
    const sector = svgElement('polygon', { points, class: `radar-sector radar-${level}` });
    const title = svgElement('title');
    title.textContent = `${label}: ${levels[level]}. ${axis.reason}`;
    sector.append(title); svg.append(sector);
    description.push(`${label}: ${levels[level]}. ${axis.reason}`);
  });
  svg.setAttribute('aria-label', 'Мнение AI, не рейтинг. ' + description.join(' '));

  const labels = [['Финансы', 85, 21, 'middle'], ['Суды', 133, 79, 'start'],
    ['Взыскания', 85, 135, 'middle'], ['Регул.', 36, 74, 'end'], ['риски', 36, 86, 'end']];
  labels.forEach(([label, x, y, anchor]) => {
    const text = svgElement('text', { x, y, 'text-anchor': anchor, class: 'radar-label' });
    text.textContent = label;
    svg.appendChild(text);
  });
  const explanation = element('details', 'radar-explanation');
  explanation.append(element('summary', null, 'Мнение AI · почему?'));
  explanation.append(element('p', null, 'Качественный ориентир по отчёту, не рейтинг и не банковская оценка. Серый — недостаточно данных или оценка недоступна.'));
  description.forEach(text => explanation.append(element('p', null, text)));
  figure.append(svg, explanation);
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
  card.append(copy, renderRadar(context.prefix, block.risk_profile));
  return card;
}
