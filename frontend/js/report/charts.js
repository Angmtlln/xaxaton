/* Графики отчёта: колонки, парные серии и спарклайны по проверенным фактам. */
import { esc, money, nf } from './format.js';
import { valueOf } from './facts.js';

/* ------------------------------ графики ---------------------------- */

export const W = 520, H = 186, PAD_TOP = 26, PAD_BOTTOM = 26, PAD_BOTTOM_NEG = 46;

export function bar(x, y, w, h) {
  if (h <= 0.5) return `M${x} ${y + h} h${w}`;
  return `M${x} ${y + h} V${y} h${w} V${y + h} Z`;
}

/* Колонки от одной базовой линии, значения подписаны на шапках. */
export function columnChart(items, opts = {}) {
  const { colorFor = () => 'var(--series-ink)', allowNegative = false } = opts;
  const values = items.map((i) => Number(i.value) || 0);
  const maxV = Math.max(0, ...values);
  const minV = allowNegative ? Math.min(0, ...values) : 0;
  const span = (maxV - minV) || 1;
  const padBottom = minV < 0 ? PAD_BOTTOM_NEG : PAD_BOTTOM;
  const plotH = H - PAD_TOP - padBottom;
  const zeroY = PAD_TOP + (maxV / span) * plotH;
  const band = W / items.length;
  const barW = Math.min(26, band * 0.42);

  const marks = items.map((item, i) => {
    const v = Number(item.value) || 0;
    const x = i * band + (band - barW) / 2;
    const h = Math.abs(v) / span * plotH;
    const y = v >= 0 ? zeroY - h : zeroY;
    const labelY = v >= 0 ? y - 8 : y + h + 15;
    return `
      <path d="${bar(x, y, barW, h)}" fill="${colorFor(item, i)}"></path>
      <text x="${x + barW / 2}" y="${labelY}" text-anchor="middle" font-size="12"
            font-weight="700" fill="#111111">${esc(item.label)}</text>
      <text x="${x + barW / 2}" y="${H - 6}" text-anchor="middle" font-size="12"
            fill="#777777">${esc(item.x)}</text>
      <rect x="${i * band}" y="0" width="${band}" height="${H}" fill="transparent"
            data-tip="${esc(item.tip)}"></rect>`;
  }).join('');

  return `<svg viewBox="0 0 ${W} ${H}" role="img">
    <line x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" stroke="#d8d8d8" stroke-width="1"></line>
    ${marks}</svg>`;
}

/* Две серии рядом, между колонками зазор 2px поверхностью. */
export function groupedChart(groups, series) {
  const maxV = Math.max(1, ...groups.flatMap((g) => series.map((s) => Number(g[s.key]) || 0)));
  const plotH = H - PAD_TOP - PAD_BOTTOM;
  const zeroY = PAD_TOP + plotH;
  const band = W / groups.length;
  const barW = Math.min(22, (band - 16) / series.length - 2);

  const marks = groups.map((g, i) => {
    const groupW = barW * series.length + 2 * (series.length - 1);
    const startX = i * band + (band - groupW) / 2;
    const inner = series.map((s, j) => {
      const v = Number(g[s.key]) || 0;
      const h = (v / maxV) * plotH;
      const x = startX + j * (barW + 2);
      const y = zeroY - h;
      return `<path d="${bar(x, y, barW, h)}" fill="${s.color}"></path>
        ${v > 0 ? `<text x="${x + barW / 2}" y="${y - 8}" text-anchor="middle" font-size="12"
              font-weight="700" fill="#111111">${nf.format(v)}</text>` : ''}`;
    }).join('');
    return `${inner}
      <text x="${i * band + band / 2}" y="${H - 6}" text-anchor="middle" font-size="12"
            fill="#777777">${esc(g.x)}</text>
      <rect x="${i * band}" y="0" width="${band}" height="${H}" fill="transparent"
            data-tip="${esc(g.tip)}"></rect>`;
  }).join('');

  return `<svg viewBox="0 0 ${W} ${H}" role="img">
    <line x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" stroke="#d8d8d8" stroke-width="1"></line>
    ${marks}</svg>`;
}

export const chartCard = (title, sub, body, legend = '') =>
  `<article class="chart-card"><h3>${esc(title)}</h3><p>${esc(sub)}</p>${body}${legend}</article>`;

export const emptyChart = (text) => `<div class="chart-empty">${esc(text)}</div>`;

export function revenueChart() {
  const rows = (valueOf('fin.series') || []).filter((r) => r.proceeds !== null && r.proceeds !== undefined);
  if (!rows.length) {
    return chartCard('Выручка по годам', 'report.finReports[].common.proceeds',
      emptyChart('Финансовой отчётности в карточке нет — динамику построить невозможно'));
  }
  const change = valueOf('fin.proceeds_change_pct');
  const items = rows.map((r) => ({
    x: r.year, value: r.proceeds, label: money(r.proceeds),
    tip: `${r.year}: выручка ${money(r.proceeds)}`,
  }));
  return chartCard('Выручка по годам',
    change === undefined || change === null
      ? 'Одна точка отчётности, динамики нет'
      : `Год к году ${change > 0 ? '+' : ''}${String(change).replace('.', ',')} %`,
    `<div class="chart-canvas">${columnChart(items)}</div>`);
}

export function profitChart() {
  const rows = (valueOf('fin.series') || []).filter((r) => r.profit !== null && r.profit !== undefined);
  if (!rows.length) {
    return chartCard('Прибыль по годам', 'report.finReports[].common.profit',
      emptyChart('Данных о прибыли в карточке нет'));
  }
  const items = rows.map((r) => ({
    x: r.year, value: r.profit,
    label: (r.profit < 0 ? '−' : '') + money(Math.abs(r.profit)),
    tip: `${r.year}: ${r.profit < 0 ? 'убыток' : 'прибыль'} ${money(Math.abs(r.profit))}`,
  }));
  return chartCard('Прибыль по годам',
    rows.some((r) => r.profit < 0) ? 'Убыточные годы — ниже нулевой линии' : 'Все годы прибыльные',
    `<div class="chart-canvas">${columnChart(items, {
      allowNegative: true,
      colorFor: (i) => (Number(i.value) < 0 ? 'var(--series-red)' : 'var(--series-green)'),
    })}</div>`,
    `<div class="chart-legend">
       <span><i class="legend-key" style="background:var(--series-green)"></i>прибыль</span>
       <span><i class="legend-key" style="background:var(--series-red)"></i>убыток</span></div>`);
}

export function courtChart() {
  const byYear = valueOf('court.by_year');
  if (!Array.isArray(byYear) || !byYear.length) {
    return chartCard('Арбитраж по годам', 'report.arbitrationCases[]',
      emptyChart('Судебных дел в карточке нет. Отсутствие записей не означает отсутствия судов'));
  }
  const groups = byYear.map((r) => ({
    x: r.year,
    defendant: r.defendant_count || 0,
    plaintiff: r.plaintiff_count || 0,
    tip: `${r.year}: ответчик ${r.defendant_count || 0} на ${money(r.defendant_amount)}, `
       + `истец ${r.plaintiff_count || 0} на ${money(r.plaintiff_amount)}`,
  }));
  const series = [
    { key: 'defendant', color: 'var(--series-red)', label: 'ответчик' },
    { key: 'plaintiff', color: 'var(--series-green)', label: 'истец' },
  ];
  return chartCard('Арбитраж по годам', 'Количество дел по ролям',
    `<div class="chart-canvas">${groupedChart(groups, series)}</div>`,
    `<div class="chart-legend">${series.map((s) =>
      `<span><i class="legend-key" style="background:${s.color}"></i>${s.label}</span>`).join('')}</div>`);
}

export function sparkline(values, label, color = 'var(--series-ink)') {
  const nums = values.map(Number).filter(Number.isFinite);
  if (!nums.length) return `<span class="mini-viz-empty">Нет данных для динамики</span>`;

  const width = 220, height = 54, pad = 4;
  const min = Math.min(0, ...nums);
  const max = Math.max(0, ...nums);
  const span = (max - min) || 1;
  const step = nums.length > 1 ? (width - pad * 2) / (nums.length - 1) : 0;
  const y = (value) => pad + ((max - value) / span) * (height - pad * 2);
  const points = nums.map((value, i) => `${pad + i * step},${y(value)}`).join(' ');
  const zeroY = y(0);

  return `<svg class="mini-sparkline" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="${esc(label)}">
    <line x1="${pad}" y1="${zeroY}" x2="${width - pad}" y2="${zeroY}"
      stroke="currentColor" stroke-opacity=".16"></line>
    ${nums.length > 1
      ? `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="3"
          stroke-linecap="round" stroke-linejoin="round"></polyline>`
      : `<circle cx="${width / 2}" cy="${y(nums[0])}" r="4" fill="${color}"></circle>`}
    ${nums.map((value, i) => `<circle cx="${nums.length > 1 ? pad + i * step : width / 2}"
      cy="${y(value)}" r="3" fill="${color}"></circle>`).join('')}
  </svg>`;
}
