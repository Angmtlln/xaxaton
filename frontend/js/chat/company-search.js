/* Deterministic suggestions: no model call while the user is typing. */
import { element } from '../shared/dom.js';

export function searchFragment(value, cursor = value.length) {
  const before = value.slice(0, cursor);
  const at = before.lastIndexOf('@');
  const start = at < 0 ? 0 : at;
  const end = at < 0 ? value.length : cursor;
  const query = value.slice(at < 0 ? 0 : at + 1, end).trim();
  return query.length >= 2 && query.length <= 256 ? { query, start, end } : null;
}

export function insertCompany(value, fragment, company) {
  const text = `${company.name} (ИНН ${company.inn})`;
  return { value: value.slice(0, fragment.start) + text + value.slice(fragment.end),
    cursor: fragment.start + text.length };
}

export function attachCompanySearch(input) {
  const popup = element('div', 'company-suggestions');
  popup.id = 'company-suggestions';
  popup.hidden = true;
  popup.setAttribute('role', 'listbox');
  popup.setAttribute('aria-label', 'Компании из нашей базы');
  input.parentElement.appendChild(popup);
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', popup.id);
  input.setAttribute('aria-expanded', 'false');
  const status = element('span', 'sr-only');
  status.setAttribute('role', 'status');
  input.parentElement.appendChild(status);
  let timer, request, revision = 0, selected = -1, rows = [], fragment, draft;

  function close() {
    clearTimeout(timer);
    request?.abort();
    revision += 1;
    popup.hidden = true;
    popup.replaceChildren();
    rows = []; selected = -1;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function highlight(index) {
    selected = index;
    [...popup.children].forEach((node, i) => node.setAttribute('aria-selected', String(i === index)));
    input.setAttribute('aria-activedescendant', `${popup.id}-${index}`);
    popup.children[index]?.scrollIntoView({ block: 'nearest' });
  }

  function choose(index) {
    if (!rows[index] || input.value !== draft || input.disabled) return close();
    const inserted = insertCompany(input.value, fragment, rows[index]);
    close();
    input.value = inserted.value;
    input.setSelectionRange(inserted.cursor, inserted.cursor);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    close();
    input.focus();
  }

  function schedule() {
    close();
    fragment = searchFragment(input.value, input.selectionStart);
    if (!fragment || input.disabled || /ИНН\s*\d{10,12}/i.test(fragment.query)) return;
    const current = revision;
    draft = input.value;
    timer = setTimeout(async () => {
      request = new AbortController();
      try {
        const response = await fetch(`/api/v1/companies/search?${new URLSearchParams({ q: fragment.query, limit: '5' })}`,
          { signal: request.signal });
        if (!response.ok) throw new Error('search unavailable');
        const found = await response.json();
        if (current !== revision || draft !== input.value || input.disabled || document.activeElement !== input) return;
        rows = found.rows || [];
        status.textContent = rows.length ? `Найдено компаний: ${found.total}` : 'В нашей базе совпадений нет';
        if (!rows.length) return;
        rows.forEach((company, index) => {
          const option = element('div', 'company-option');
          option.id = `${popup.id}-${index}`;
          option.setAttribute('role', 'option');
          option.setAttribute('aria-selected', 'false');
          option.append(element('strong', null, company.name),
            element('span', null, `ИНН ${company.inn}${company.address ? ` · ${company.address}` : ''}`));
          option.addEventListener('mousedown', (event) => event.preventDefault());
          option.addEventListener('click', () => choose(index));
          popup.appendChild(option);
        });
        popup.hidden = false;
        input.setAttribute('aria-expanded', 'true');
      } catch (error) {
        if (error.name !== 'AbortError' && current === revision) status.textContent = 'Подсказки временно недоступны. Можно отправить сообщение.';
      }
    }, 250);
  }

  input.addEventListener('input', (event) => { if (!event.isComposing) schedule(); });
  input.addEventListener('compositionend', schedule);
  input.addEventListener('blur', close);
  input.addEventListener('keydown', (event) => {
    if (event.isComposing || popup.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      highlight(selected < 0 ? (event.key === 'ArrowDown' ? 0 : rows.length - 1)
        : (selected + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length);
    } else if (event.key === 'Enter' && !event.shiftKey && selected >= 0) {
      event.preventDefault(); choose(selected);
    }
  });
  return { close };
}
