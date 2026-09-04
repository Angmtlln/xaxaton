/* Отчёт по одному ИНН. Разметка повторяет прототип фронтендера,
   данные приходят из POST /api/v1/checks. */

/* ------------------------------ иконки ----------------------------- */

const PATHS = {
  shieldCheck: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path><path d="m9 12 2 2 4-4"></path>',
  shieldAlert: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path><path d="M12 8v4"></path><path d="M12 16h.01"></path>',
  triangleAlert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>',
  circleAlert: '<circle cx="12" cy="12" r="10"></circle><path d="M12 8v4"></path><path d="M12 16h.01"></path>',
  circleCheck: '<circle cx="12" cy="12" r="10"></circle><path d="m9 12 2 2 4-4"></path>',
  circleHelp: '<circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><path d="M12 17h.01"></path>',
  check: '<path d="M20 6 9 17l-5-5"></path>',
  building: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"></path><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"></path><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"></path><path d="M10 6h4"></path><path d="M10 10h4"></path><path d="M10 14h4"></path>',
  chevronDown: '<path d="m6 9 6 6 6-6"></path>',
};

const icon = (name, cls = '') =>
  `<svg ${cls ? `class="${cls}" ` : ''}viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name]}</svg>`;

/* ------------------------------ словари ---------------------------- */

const VERDICT = {
  STOP: { theme: 'intensive', icon: 'shieldAlert', label: 'Стоп до устранения' },
  ENHANCED_CHECK: { theme: 'review', icon: 'triangleAlert', label: 'Усиленная проверка' },
  CONDITIONALLY_OK: { theme: 'positive', icon: 'shieldCheck', label: 'Условно допустим' },
  NO_DATA: { theme: 'review', icon: 'circleHelp', label: 'Данных недостаточно' },
};

const SIGNAL = {
  RISK: { state: 'attention', icon: 'circleAlert', label: 'Жёсткие факты' },
  ATTENTION: { state: 'unknown', icon: 'triangleAlert', label: 'Требует уточнения' },
  NORM: { state: 'clear', icon: 'circleCheck', label: 'Без стоп-факторов' },
  NO_DATA: { state: 'none', icon: 'circleHelp', label: 'Мало данных' },
};

const EVIDENCE_TYPE = {
  raw_fact: 'Исходный факт',
  derived_metric: 'Расчётная метрика',
  source_signal: 'Сигнал источника',
};

/* ------------------------------ формат ----------------------------- */

const nf = new Intl.NumberFormat('ru-RU');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function money(value) {
  if (value === null || value === undefined || value === '') return 'нет данных';
  const n = Number(value);
  if (!isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(abs >= 1e10 ? 0 : 1).replace('.', ',') + ' млрд ₽';
  if (abs >= 1e6) return (n / 1e6).toFixed(abs >= 1e7 ? 0 : 1).replace('.', ',') + ' млн ₽';
  if (abs >= 1e3) return nf.format(Math.round(n)) + ' ₽';
  return nf.format(Math.round(n * 100) / 100) + ' ₽';
}

function plain(v) {
  if (v === null || v === undefined || v === '') return 'нет данных';
  if (typeof v === 'boolean') return v ? 'да' : 'нет';
  if (typeof v === 'number') return nf.format(v);
  if (Array.isArray(v)) {
    if (!v.length) return 'нет';
    return v.map((x) => (x && typeof x === 'object'
      ? (x.meaning || x.code || x.name || x.label || Object.values(x).join(' '))
      : x)).join(', ');
  }
  if (typeof v === 'object') {
    return Object.entries(v).map(([k, val]) => `${k}: ${plain(val)}`).join(', ');
  }
  return String(v);
}

const factText = (fact) => (fact.unit === 'руб' ? money(fact.value) : plain(fact.value));

function evidenceType(fact) {
  if ((fact.field_ref || '').includes('reputationalRisks')) return 'source_signal';
  return fact.source === 'raw' ? 'raw_fact' : 'derived_metric';
}

/* ------------------------------- данные ---------------------------- */

let factIndex = {};
const factOf = (id) => factIndex[id];
const valueOf = (id) => (factIndex[id] ? factIndex[id].value : undefined);

/* ------------------------------ графики ---------------------------- */

const W = 520, H = 186, PAD_TOP = 26, PAD_BOTTOM = 26, PAD_BOTTOM_NEG = 46;

function bar(x, y, w, h) {
  if (h <= 0.5) return `M${x} ${y + h} h${w}`;
  return `M${x} ${y + h} V${y} h${w} V${y + h} Z`;
}

/* Колонки от одной базовой линии, значения подписаны на шапках. */
function columnChart(items, opts = {}) {
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
function groupedChart(groups, series) {
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

const chartCard = (title, sub, body, legend = '') =>
  `<article class="chart-card"><h3>${esc(title)}</h3><p>${esc(sub)}</p>${body}${legend}</article>`;

const emptyChart = (text) => `<div class="chart-empty">${esc(text)}</div>`;

function revenueChart() {
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

function profitChart() {
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

function courtChart() {
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

function sparkline(values, label, color = 'var(--series-ink)') {
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

function compactMetrics() {
  const rows = Array.isArray(valueOf('fin.series')) ? valueOf('fin.series') : [];
  const revenue = rows.filter((row) => row.proceeds !== null && row.proceeds !== undefined);
  const profit = rows.filter((row) => row.profit !== null && row.profit !== undefined);
  const defendant = Number(valueOf('court.defendant_count')) || 0;
  const plaintiff = Number(valueOf('court.plaintiff_count')) || 0;
  const courtRows = Array.isArray(valueOf('court.by_year')) ? valueOf('court.by_year') : [];
  const maxCases = Math.max(1, defendant, plaintiff);
  const change = valueOf('fin.proceeds_change_pct');

  return `
    <div class="summary-viz" aria-label="Ключевые показатели компании">
      <article class="mini-viz-card">
        <div><span>Выручка</span><strong>${esc(money(valueOf('fin.proceeds_last')))}</strong>
          <small>${change === null || change === undefined ? 'Динамика не рассчитана'
            : `${change > 0 ? '+' : ''}${String(change).replace('.', ',')} % год к году`}</small></div>
        ${sparkline(revenue.map((row) => row.proceeds),
          `Выручка по годам: ${revenue.map((row) => `${row.year} — ${money(row.proceeds)}`).join(', ')}`)}
      </article>
      <article class="mini-viz-card">
        <div><span>Прибыль</span><strong>${esc(money(valueOf('fin.profit_last')))}</strong>
          <small>${!profit.length ? 'Данных в карточке нет'
            : (profit.some((row) => Number(row.profit) < 0) ? 'Есть убыточные годы' : 'По доступным годам')}</small></div>
        ${sparkline(profit.map((row) => row.profit),
          `Прибыль по годам: ${profit.map((row) => `${row.year} — ${money(row.profit)}`).join(', ')}`,
          profit.some((row) => Number(row.profit) < 0) ? 'var(--series-red)' : 'var(--series-green)')}
      </article>
      <article class="mini-viz-card mini-court-card">
        <div><span>Арбитраж в карточке</span><strong>${courtRows.length ? nf.format(defendant + plaintiff) : '0 записей'}</strong>
          <small>${courtRows.length ? 'По ролям в доступных записях' : 'Не означает отсутствия судебных дел'}</small></div>
        ${courtRows.length ? `<div class="mini-bars" role="img"
          aria-label="Ответчик — ${defendant}, истец — ${plaintiff}">
          <span><i style="height:${Math.max(3, defendant / maxCases * 44)}px"></i><b>${defendant}</b><small>ответчик</small></span>
          <span><i style="height:${Math.max(3, plaintiff / maxCases * 44)}px"></i><b>${plaintiff}</b><small>истец</small></span>
        </div>` : '<span class="mini-viz-empty">Записей о делах в карточке нет</span>'}
      </article>
    </div>`;
}

function narrativeBullets(text) {
  const sentences = String(text || '')
    .replace(/([.!?])\s+(?=[А-ЯЁ])/g, '$1\n')
    .split('\n')
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  if (!sentences.length) return '<li>Итоговое пояснение не сформировано.</li>';
  return sentences.map((sentence) => `<li>${esc(sentence)}</li>`).join('');
}

function blockLabel(title) {
  return ({
    'Кто это': 'Профиль компании',
    'Надёжность и правовые риски': 'Правовые риски',
    'Финансовое состояние': 'Финансовые риски',
    'Опыт и позитивные сигналы': 'Опыт и репутация',
  })[title] || title;
}

function signalWord(count) {
  const mod10 = count % 10, mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'сигнал';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'сигнала';
  return 'сигналов';
}

function gapWord(count) {
  const mod10 = count % 10, mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'пробел';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'пробела';
  return 'пробелов';
}

/* ------------------------------ секции ----------------------------- */

function companyHead(data) {
  const c = data.company;
  return `
    <section class="company-heading" aria-labelledby="company-name">
      <span class="eyebrow">Отчёт о контрагенте</span>
      <h1 id="company-name">${esc(c.short_name || 'Контрагент')}</h1>
    </section>`;
}

function summaryPanel(data) {
  const s = data.summary;
  const v = VERDICT[s.verdict_group] || VERDICT.NO_DATA;
  const c = data.company;
  const risk = (c.risk_level || 'UNKNOWN').toLowerCase();
  const zsk = (c.zsk_risk_level || 'UNKNOWN').toLowerCase();

  const blockLinks = (data.blocks || []).map((block, index) => {
    const meta = SIGNAL[block.signal] || SIGNAL.NO_DATA;
    const count = (block.findings || []).filter((finding) =>
      finding.grounded !== false && (finding.severity === 'high' || finding.severity === 'medium')).length;
    const gaps = (block.cannot_assess || []).length;
    const countText = block.signal === 'NO_DATA' ? 'нет данных'
      : (count === 0 && gaps ? `${gaps} ${gapWord(gaps)}` : `${count} ${signalWord(count)}`);
    return `<a class="block-pill state-${meta.state}" href="#block-${index + 1}">
      <span>${esc(blockLabel(block.title))}</span>
      <strong>${esc(countText)}</strong>
      ${icon('chevronDown')}
    </a>`;
  }).join('');

  const notes = (data.guardrail_notes || []).join('; ');
  return `
    <section id="summary" class="summary-panel summary-${v.theme}">
      <div class="summary-top">
        <div class="summary-copy">
          <span class="summary-icon">${icon(v.icon)}</span>
          <div>
            <span class="eyebrow">Вывод агента по доступным фактам</span>
            <span class="summary-result-label">${esc(v.label)}</span>
            <h2>${esc(s.headline)}</h2>
          </div>
        </div>
        <aside class="summary-side">
          <div class="bank-risks" aria-label="Оценки банка без пересчёта"
            title="Оценки банка приводятся без изменений и не пересчитываются">
            <span class="bank-risks-label">Оценки банка</span>
            <span>Банк <b class="risk-level risk-${esc(risk)}">${esc(c.risk_level || '—')}</b></span>
            <span>ЗСК <b class="zsk-level zsk-${esc(zsk)}">${esc(c.zsk_risk_level || '—')}</b></span>
          </div>
          <nav class="block-pills" aria-label="Перейти к подробному разбору">${blockLinks}</nav>
        </aside>
      </div>
      ${compactMetrics()}
      <div class="summary-note">
        <span class="eyebrow">Почему такой вывод</span>
        <ul>${narrativeBullets(s.narrative)}</ul>
        ${notes ? `<p>Защитный слой: ${esc(notes)}.</p>` : ''}
      </div>
    </section>`;
}

function attentionSection(data) {
  const risks = data.summary.top_risks || [];
  if (!risks.length) return '';
  return `
    <section class="section-block">
      <div class="section-heading">
        <div><span class="eyebrow">Главное</span><h2>На что обратить внимание</h2></div>
        <p>Каждый пункт со ссылкой на поле исходной карточки отчёта.</p>
      </div>
      <div class="attention-list">
        ${risks.map((r) => {
          const fact = factOf(r.fact_id);
          const sev = r.severity === 'high' ? 'high' : (r.severity === 'low' ? 'low' : 'medium');
          const ico = sev === 'high' ? 'circleAlert' : (sev === 'low' ? 'circleCheck' : 'triangleAlert');
          return `<div class="attention-row sev-${sev}">
            <span class="attention-icon">${icon(ico)}</span>
            <p>${esc(r.text)}</p>
            <code>${esc(fact ? fact.field_ref : 'ссылка не подтверждена')}</code>
          </div>`;
        }).join('')}
      </div>
    </section>`;
}

function blockCard(block, index) {
  const s = SIGNAL[block.signal] || SIGNAL.NO_DATA;
  const cited = new Set();
  const rows = (block.findings || []).map((f) => {
    const fact = factOf(f.fact_id);
    if (fact) cited.add(fact.id);
    const type = fact ? evidenceType(fact) : 'derived_metric';
    return `<div class="evidence-row">
      <div><span class="evidence-type type-${type}">${EVIDENCE_TYPE[type]}</span>
        <b>${esc(fact ? fact.label : 'Наблюдение агента')}</b></div>
      <p>${esc(f.text)}</p>
      <code>${esc(fact ? fact.field_ref : 'ссылка не подтверждена')}</code>
    </div>`;
  }).join('');

  const gaps = (block.cannot_assess || []).slice(0, 3).map((g) =>
    `<div class="evidence-row">
       <div><span class="evidence-type">Нет данных</span><b>Оценить невозможно</b></div>
       <p>${esc(g)}</p></div>`).join('');

  const rest = (block.facts || []).filter((f) => !cited.has(f.id)
    && f.value !== null && f.value !== undefined && f.value !== '');

  const table = rest.length ? `
    <details class="evidence-more">
      <summary>Все факты блока, ${block.facts.length}</summary>
      <table class="fact-table">
        <thead><tr><th>Идентификатор</th><th>Факт</th><th>Значение</th><th>Поле отчёта</th></tr></thead>
        <tbody>${rest.map((f) => `<tr>
          <td>${esc(f.id)}</td><td>${esc(f.label)}</td>
          <td>${esc(factText(f))}</td><td>${esc(f.field_ref)}</td></tr>`).join('')}</tbody>
      </table>
    </details>` : '';

  return `
    <details id="block-${index + 1}" class="risk-card state-${s.state}"
      ${block.signal === 'RISK' ? 'open' : ''}>
      <summary>
        <span class="risk-card-icon">${icon(s.icon)}</span>
        <span class="risk-card-copy">
          <span class="risk-card-label">${esc(s.label)}</span>
          <strong>${esc(block.title)}</strong>
          <span>${esc(block.facts_sentence)}</span>
        </span>
        ${icon('chevronDown', 'risk-card-chevron')}
      </summary>
      <div class="evidence-list">
        <div class="evidence-row">
          <div><span class="evidence-type">Вывод агента</span><b>${esc(block.headline || block.title)}</b></div>
          <p>${esc(block.interpretation)}</p>
          <code>модель ${esc(block.model)}</code>
        </div>
        ${rows}${gaps}
      </div>
      ${table}
    </details>`;
}

function signalsSection(data) {
  return `
    <section id="signals" class="signals-section">
      <div class="section-heading">
        <div><span class="eyebrow">Детальный разбор</span><h2>Факты по направлениям</h2></div>
        <p>Каждый блок разбирает отдельная модель и видит только свои факты.
           Раскройте карточку, чтобы увидеть значения и путь в исходном JSON.</p>
      </div>
      <div class="risk-grid">${(data.blocks || []).map(blockCard).join('')}</div>
    </section>`;
}

function financeSection(data) {
  const ids = ['fin.proceeds_last', 'fin.profit_last', 'execproc.active_count',
    'execproc.active_amount', 'court.defendant_count', 'court.defendant_amount',
    'okved.total_count', 'positive.count'];
  const tiles = ids.map(factOf).filter(Boolean).slice(0, 4).map((f) => `
    <div class="stat-cell">
      <span class="stat-label">${esc(f.label)}</span>
      <span class="stat-value">${esc(factText(f))}</span>
      <code>${esc(f.field_ref)}</code>
    </div>`).join('');

  return `
    <section id="finance" class="section-block">
      <div class="section-heading">
        <div><span class="eyebrow">Цифры</span><h2>Показатели и динамика</h2></div>
        <p>Все значения посчитаны кодом из сырых полей карточки, готовые формулировки
           отчёта в расчёт не берутся.</p>
      </div>
      ${tiles ? `<div class="stat-grid" style="margin-bottom:14px">${tiles}</div>` : ''}
      <div class="chart-grid">${revenueChart()}${profitChart()}${courtChart()}${execCard()}</div>
    </section>`;
}

function execCard() {
  const total = valueOf('execproc.total_count');
  if (total === undefined) {
    return chartCard('Исполнительные производства', 'report.executionProceedings[]',
      emptyChart('Исполнительных производств в карточке нет'));
  }
  const unknown = valueOf('execproc.amount_unknown_count') ?? 0;
  const cells = [
    ['Действующих', nf.format(valueOf('execproc.active_count') ?? 0)],
    ['Сумма действующих', money(valueOf('execproc.active_amount'))],
    ['Всего производств', nf.format(total)],
    ['Без суммы в источнике', nf.format(unknown)],
  ].map(([label, value]) => `<div class="stat-cell">
      <span class="stat-label">${esc(label)}</span>
      <span class="stat-value">${esc(value)}</span></div>`).join('');
  return chartCard('Исполнительные производства',
    unknown ? `У ${unknown} записей сумма не раскрыта — в суммы они не вошли`
            : 'Суммы посчитаны по всем записям',
    `<div class="stat-grid" style="grid-template-columns:repeat(2,minmax(0,1fr));margin-top:18px">${cells}</div>`);
}

function coverageSection(data) {
  const cov = data.coverage;
  return `
    <section id="coverage" class="section-block">
      <div class="section-heading">
        <div><span class="eyebrow">Полнота</span><h2>Что есть в карточке, а чего нет</h2></div>
        <p>Заполнено ${cov.filled_blocks} из ${cov.total_blocks} блоков данных.
           Пустой блок агент не додумывает, а прямо говорит «оценить невозможно».</p>
      </div>
      <div class="coverage-grid">
        ${cov.blocks.map((b) => `<div class="coverage-cell" data-filled="${b.filled}">
          <span class="coverage-dot"></span><span>${esc(b.title)}</span>
          <span>${b.filled ? b.items + ' зап.' : 'нет данных'}</span></div>`).join('')}
      </div>
    </section>`;
}

function questionsSection(data) {
  const qs = data.summary.questions_to_ask || [];
  if (!qs.length) return '';
  return `
    <section class="assistant-box">
      <div class="assistant-heading">
        <span>${icon('building')}</span>
        <div><small>Следующий шаг</small><h2>Что спросить у контрагента до сделки</h2></div>
      </div>
      <div class="questions-list">
        ${qs.map((q, i) => `<div><b>0${i + 1}</b><span>${esc(q)}</span></div>`).join('')}
      </div>
      <p class="assistant-disclaimer">Вопросы собраны по фактам отчёта. Агент не выносит
        приговор и не заменяет юридическую проверку.</p>
    </section>`;
}

function runMeta(data) {
  const g = data.grounding;
  const models = [...new Set((data.blocks || []).map((b) => b.model).filter(Boolean))];
  const failures = (data.blocks || []).filter((b) => b.error);
  return `
    <div class="run-meta">
      <span>Утверждений со ссылкой на факт: <b>${g.grounded} из ${g.statements}</b> (${g.grounded_pct} %)</span>
      <span>Ссылок на несуществующие факты: <b>${g.unverified}</b></span>
      <span>Модели блоков: <b>${esc(models.join(', '))}</b></span>
      <span>Итог: <b>${esc(data.summary.model || data.llm.summary_model)}</b></span>
      <span>Время: <b>${nf.format(data.llm.latency_ms)} мс</b></span>
      ${data.run_id ? `<span>Прогон: <b>${esc(data.run_id.slice(0, 8))}</b></span>` : ''}
      ${failures.length ? `<p class="run-note">Модель ответила не по всем блокам, для них показан
        детерминированный разбор: ${esc(failures.map((b) => b.title).join(', '))}.</p>` : ''}
    </div>`;
}

/* ------------------------------- сборка ---------------------------- */

function render(data) {
  factIndex = {};
  (data.blocks || []).forEach((b) => (b.facts || []).forEach((f) => { factIndex[f.id] = f; }));

  document.title = `${data.company.short_name || data.inn} — отчёт о контрагенте`;
  document.getElementById('report-content').innerHTML = [
    companyHead(data),
    summaryPanel(data),
    attentionSection(data),
    signalsSection(data),
    financeSection(data),
    coverageSection(data),
    questionsSection(data),
    runMeta(data),
  ].join('');

  const badge = document.getElementById('mode-badge');
  badge.textContent = data.llm.mode === 'groq' ? 'Groq' : 'Без модели';
  document.getElementById('mode-note').textContent =
    `Блоки: ${data.llm.block_model}. Итог: ${data.llm.summary_model}. `
    + 'Ответ строится только по данным отчёта банка.';

  bindTooltips();
  bindBlockLinks();
  bindScrollSpy();
}

function bindBlockLinks() {
  document.querySelectorAll('.block-pill').forEach((link) => {
    link.addEventListener('click', () => {
      const target = document.querySelector(link.getAttribute('href'));
      if (target instanceof HTMLDetailsElement) target.open = true;
    });
  });
}

function bindTooltips() {
  document.querySelectorAll('.chart-canvas').forEach((canvas) => {
    const tip = document.createElement('div');
    tip.className = 'chart-tooltip';
    canvas.appendChild(tip);
    canvas.querySelectorAll('rect[data-tip]').forEach((hit) => {
      hit.addEventListener('mouseenter', () => { tip.textContent = hit.dataset.tip; tip.style.opacity = '1'; });
      hit.addEventListener('mousemove', (e) => {
        const box = canvas.getBoundingClientRect();
        tip.style.left = `${Math.min(Math.max(e.clientX - box.left, 70), box.width - 70)}px`;
        tip.style.top = `${e.clientY - box.top - 12}px`;
      });
      hit.addEventListener('mouseleave', () => { tip.style.opacity = '0'; });
    });
  });
}

function bindScrollSpy() {
  const links = [...document.querySelectorAll('#sidebar-nav a')];
  const sections = links
    .map((a) => document.querySelector(a.getAttribute('href')))
    .filter(Boolean);
  if (!sections.length) return;
  const observer = new IntersectionObserver((entries) => {
    entries.filter((e) => e.isIntersecting).forEach((entry) => {
      links.forEach((a) => a.classList.toggle('active',
        a.getAttribute('href') === `#${entry.target.id}`));
    });
  }, { rootMargin: '-120px 0px -60% 0px' });
  sections.forEach((s) => observer.observe(s));
}

function markStep(step) {
  const row = document.querySelector(`.loading-step[data-step="${step}"]`);
  if (!row || row.dataset.state === 'done') return;
  row.dataset.state = 'done';
  row.firstElementChild.outerHTML =
    `<span class="step-mark">${icon('circleCheck')}</span>`;
}

function showError(title, text) {
  document.getElementById('report-content').innerHTML = `
    <section class="company-heading"><div>
      <span class="eyebrow">Отчёт о контрагенте</span>
      <h1>${esc(title)}</h1></div></section>
    <div class="report-error"><b>${esc(title)}</b><p>${esc(text)}</p></div>
    <p style="margin-top:22px"><a class="pbtn pbtn-primary" href="/">Вернуться к поиску</a></p>`;
}

(async function run() {
  const inn = new URLSearchParams(window.location.search).get('inn');
  if (!inn || !/^\d{10,12}$/.test(inn)) {
    showError('Нужен ИНН', 'Откройте отчёт по ссылке вида /report?inn=6165169320 или начните с поиска.');
    return;
  }
  document.getElementById('loading-title').textContent = `Собираем отчёт по ИНН ${inn}`;
  setTimeout(() => markStep('facts'), 500);
  setTimeout(() => markStep('agents'), 3500);

  try {
    const resp = await fetch('/api/v1/checks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ inn }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showError('Отчёт не найден', data.detail || 'Проверка не выполнена');
      return;
    }
    markStep('facts'); markStep('agents'); markStep('summary');
    setTimeout(() => render(data), 250);
  } catch (e) {
    showError('Сервис недоступен', String(e.message || e));
  }
})();
