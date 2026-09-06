/* Навигация по диалогу: проверенные контрагенты и сравнения текущей беседы.
   Подписи берутся из готового ответа backend, а не из прозы ассистента. */
import { element, safeArray } from '../shared/dom.js';

const sidebar = document.getElementById('chat-nav');
const companyList = document.getElementById('chat-nav-companies');
const comparisonList = document.getElementById('chat-nav-comparisons');
const companySection = document.getElementById('chat-nav-companies-section');
const comparisonSection = document.getElementById('chat-nav-comparisons-section');
const toggle = document.getElementById('chat-nav-toggle');

let entries = [];
let counter = 0;

function shortName(value) {
  // Названия приходят как ООО "ИМЯ": кавычки и форма собственности только шумят.
  return String(value || '')
    .replace(/^(?:ООО|АО|ПАО|ЗАО|ИП|НКО|АНО|ФГУП|МУП|АССОЦИАЦИЯ|СРО)\s+/iu, '')
    .replace(/["«»]/g, '')
    .trim() || 'Контрагент';
}

function anchorFor(article) {
  if (!article.id) {
    counter += 1;
    article.id = `turn-${counter}`;
  }
  return article.id;
}

/** Регистрирует ход диалога, если в нём есть проверка или сравнение. */
export function registerTurn(payload, article) {
  if (!payload || !article) return;
  const leading = payload.leading_artifact;
  if (leading && leading.type === 'company_summary' && leading.inn) {
    add({
      kind: 'company', key: `company:${leading.inn}`,
      label: shortName(leading.name), hint: `ИНН ${leading.inn}`,
      anchor: anchorFor(article),
    });
  }
  safeArray(payload.blocks)
    .filter((block) => block && block.type === 'comparison_table')
    .forEach((block) => {
      const columns = safeArray(block.columns);
      if (columns.length < 2) return;
      add({
        kind: 'comparison',
        key: `comparison:${columns.map((c) => c.inn).join('-')}`,
        label: columns.map((c) => shortName(c.name)).join(' vs '),
        hint: `${columns.length} компании`,
        anchor: anchorFor(article),
      });
    });
  render();
}

function add(entry) {
  // Повторная проверка той же компании обновляет ссылку на последний ход.
  const existing = entries.findIndex((item) => item.key === entry.key);
  if (existing >= 0) entries[existing] = entry;
  else entries.push(entry);
}

export function resetNavigation() {
  entries = [];
  counter = 0;
  render();
}

function scrollTo(anchor) {
  const target = document.getElementById(anchor);
  if (!target) return;
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  target.classList.add('turn-highlight');
  window.setTimeout(() => target.classList.remove('turn-highlight'), 1600);
  if (window.matchMedia('(max-width: 900px)').matches) closeSidebar();
}

function itemFor(entry) {
  const item = element('li');
  const button = element('button', 'chat-nav-item');
  button.type = 'button';
  button.append(
    element('span', 'chat-nav-label', entry.label),
    element('span', 'chat-nav-hint', entry.hint),
  );
  button.addEventListener('click', () => scrollTo(entry.anchor));
  item.appendChild(button);
  return item;
}

function render() {
  if (!sidebar) return;
  const companies = entries.filter((entry) => entry.kind === 'company');
  const comparisons = entries.filter((entry) => entry.kind === 'comparison');
  companyList.replaceChildren(...companies.map(itemFor));
  comparisonList.replaceChildren(...comparisons.map(itemFor));
  companySection.hidden = !companies.length;
  comparisonSection.hidden = !comparisons.length;
  sidebar.hidden = !entries.length;
  document.body.classList.toggle('has-chat-nav', entries.length > 0);
}

function closeSidebar() {
  document.body.classList.remove('chat-nav-open');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
}

if (toggle) {
  toggle.addEventListener('click', () => {
    const open = document.body.classList.toggle('chat-nav-open');
    toggle.setAttribute('aria-expanded', String(open));
  });
}
