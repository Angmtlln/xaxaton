/* Сборка страницы отчёта: запрос прогона, рендер секций и подписки. */
import { esc, icon } from './format.js';
import { setFacts } from './facts.js';
import { attentionSection, companyHead, coverageSection, financeSection, questionsSection, runMeta, signalsSection, summaryPanel } from './sections.js';

function render(data) {
  setFacts(data.blocks);

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
