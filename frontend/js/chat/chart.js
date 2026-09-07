/* SVG-график динамики. Данные приходят проверенными с бэкенда. */
import { element, svgElement, safeArray, numericValue } from '../shared/dom.js';

const moneyFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const integerFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

export function buildChart(series, unit) {
  const width = 760;
  const height = 200;
  const padding = { top: 16, right: 24, bottom: 32, left: 74 };
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

export function compactNumber(value, unit) {
  const abs = Math.abs(value);
  let text;
  if (abs >= 1e9) text = `${moneyFormat.format(value / 1e9)} млрд`;
  else if (abs >= 1e6) text = `${moneyFormat.format(value / 1e6)} млн`;
  else if (abs >= 1e3) text = `${moneyFormat.format(value / 1e3)} тыс`;
  else text = integerFormat.format(value);
  return unit === 'руб' ? `${text} ₽` : text;
}

export function formatChartValue(value, unit) {
  const text = moneyFormat.format(value);
  return unit === 'руб' ? `${text} ₽` : text;
}


export function buildBarChart(series, unit) {
  const chart = buildChart(series, unit);
  const labels = series[0].points;
  const width = 760, left = 130, right = 110;
  const groupHeight = series.length * 18 + 10;
  const height = labels.length * groupHeight + 24;
  const values = series.flatMap((item) => item.points).map((point) => numericValue(point.value)).filter(Number.isFinite);
  const max = Math.max(1, ...values);
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': 'Количество судебных дел по годам: ответчик и истец' });
  labels.forEach((point, index) => {
    const label = svgElement('text', { x: 12, y: 27 + index * groupHeight, class: 'chart-axis-label' });
    label.textContent = point.x; svg.appendChild(label);
    series.forEach((item, si) => {
      const value = numericValue(item.points[index]?.value);
      const y = 10 + index * groupHeight + si * 18;
      const barWidth = Number.isFinite(value) ? value / max * (width - left - right) : 0;
      const rect = svgElement('rect', { x: left, y, width: barWidth, height: 13, rx: 2,
        fill: si % 2 ? '#ef3124' : '#111111' });
      const title = svgElement('title');
      title.textContent = `${point.x}, ${item.label}: ${Number.isFinite(value) ? formatChartValue(value, unit) : 'Нет данных'}`;
      rect.appendChild(title); svg.appendChild(rect);
      const number = svgElement('text', { x: left + barWidth + 8, y: y + 11, class: 'chart-axis-label' });
      number.textContent = Number.isFinite(value) ? formatChartValue(value, unit) : 'Нет данных';
      svg.appendChild(number);
    });
  });
  chart.figure.replaceChild(svg, chart.figure.querySelector('svg'));
  return chart;
}
