/* Секции отчёта. Каждая возвращает разметку, состояние живёт в facts.js. */
import { EVIDENCE_TYPE, SIGNAL, VERDICT, blockLabel, esc, evidenceType, factText, gapWord, icon, money, nf, signalWord } from './format.js';
import { factOf, valueOf } from './facts.js';
import { chartCard, courtChart, emptyChart, profitChart, revenueChart, sparkline } from './charts.js';

export function compactMetrics() {
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

export function narrativeBullets(summary) {
  const modelPoints = Array.isArray(summary.narrative_points)
    ? summary.narrative_points.map((point) => String(point || '').trim()).filter(Boolean)
    : [];
  const fallbackPoints = String(summary.narrative || '')
    .replace(/([.!?])\s+(?=[А-ЯЁ])/g, '$1\n')
    .split('\n')
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  const points = (modelPoints.length ? modelPoints : fallbackPoints).slice(0, 3);
  if (!points.length) return '<li>Итоговое пояснение не сформировано.</li>';
  return points.map((point) => {
    const compact = point.length > 180 ? `${point.slice(0, 179).trimEnd()}…` : point;
    return `<li>${esc(compact)}</li>`;
  }).join('');
}

/* ------------------------------ секции ----------------------------- */

export function companyHead(data) {
  const c = data.company;
  return `
    <section class="company-heading" aria-labelledby="company-name">
      <span class="eyebrow">Отчёт о контрагенте</span>
      <h1 id="company-name">${esc(c.short_name || 'Контрагент')}</h1>
    </section>`;
}

export function summaryPanel(data) {
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
        <ul>${narrativeBullets(s)}</ul>
        ${notes ? `<p>Защитный слой: ${esc(notes)}.</p>` : ''}
      </div>
    </section>`;
}

export function attentionSection(data) {
  const risks = data.summary.top_risks || [];
  if (!risks.length) return '';
  return `
    <section class="section-block">
      <div class="section-heading">
        <div><span class="eyebrow">Главное</span><h2>На что обратить внимание</h2></div>
        <p>Каждый пункт посчитан из полей исходной карточки отчёта.</p>
      </div>
      <div class="attention-list">
        ${risks.map((r) => {
          const fact = factOf(r.fact_id);
          const sev = r.severity === 'high' ? 'high' : (r.severity === 'low' ? 'low' : 'medium');
          const ico = sev === 'high' ? 'circleAlert' : (sev === 'low' ? 'circleCheck' : 'triangleAlert');
          const ref = fact ? fact.field_ref : 'ссылка не подтверждена';
          return `<div class="attention-row sev-${sev}" title="Поле карточки: ${esc(ref)}">
            <span class="attention-icon">${icon(ico)}</span>
            <p>${esc(r.text)}</p>
          </div>`;
        }).join('')}
      </div>
    </section>`;
}

export function blockCard(block, index) {
  const s = SIGNAL[block.signal] || SIGNAL.NO_DATA;
  const cited = new Set();
  const rows = (block.findings || []).map((f) => {
    const fact = factOf(f.fact_id);
    if (fact) cited.add(fact.id);
    const type = fact ? evidenceType(fact) : 'derived_metric';
    const ref = fact ? fact.field_ref : 'ссылка не подтверждена';
    return `<div class="evidence-row" title="Поле карточки: ${esc(ref)}">
      <div><span class="evidence-type type-${type}">${EVIDENCE_TYPE[type]}</span>
        <b>${esc(fact ? fact.label : 'Наблюдение агента')}</b></div>
      <p>${esc(f.text)}</p>
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

export function signalsSection(data) {
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

export function financeSection(data) {
  const ids = ['fin.proceeds_last', 'fin.profit_last', 'execproc.active_count',
    'execproc.active_amount', 'court.defendant_count', 'court.defendant_amount',
    'okved.total_count', 'positive.count'];
  const tiles = ids.map(factOf).filter(Boolean).slice(0, 4).map((f) => `
    <div class="stat-cell" title="Поле карточки: ${esc(f.field_ref)}">
      <span class="stat-label">${esc(f.label)}</span>
      <span class="stat-value">${esc(factText(f))}</span>
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

export function execCard() {
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

export function coverageSection(data) {
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

export function questionsSection(data) {
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

export function runMeta(data) {
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
