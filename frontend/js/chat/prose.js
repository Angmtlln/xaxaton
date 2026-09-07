/* Узкое безопасное форматирование текста. Никакого HTML и ссылок из прозы. */
import { element } from '../shared/dom.js';

function inline(parent, text) {
  String(text).split(/(\*\*[^*\n]+\*\*)/g).forEach((part) => {
    parent.appendChild(part.startsWith('**') && part.endsWith('**')
      ? element('strong', null, part.slice(2, -2)) : document.createTextNode(part));
  });
}

function cells(line) {
  const value = line.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '');
  return value.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, '|'));
}

export function appendProse(parent, text) {
  let paragraph = null;
  let list = null;
  const lines = String(text).split('\n');
  let consumed = -1;
  lines.forEach((line, index) => {
    if (index <= consumed) return;
    const headers = cells(line);
    const separator = cells(lines[index + 1] || '');
    if (line.includes('|') && headers.length > 1 && separator.length === headers.length
        && separator.every((cell) => /^:?-+:?$/.test(cell))) {
      paragraph = null; list = null;
      const wrap = element('div', 'prose-table-wrap');
      wrap.tabIndex = 0;
      wrap.setAttribute('role', 'region');
      wrap.setAttribute('aria-label', 'Таблица из ответа');
      const table = element('table', 'chart-data-table prose-table');
      const thead = element('thead');
      const tr = element('tr');
      headers.forEach((value) => {
        const cell = element('th'); cell.scope = 'col'; inline(cell, value); tr.appendChild(cell);
      });
      thead.appendChild(tr);
      const tbody = element('tbody');
      consumed = index + 1;
      for (let next = index + 2; next < lines.length && lines[next].includes('|'); next += 1) {
        const values = cells(lines[next]);
        if (values.length !== headers.length) break;
        const row = element('tr');
        values.forEach((value) => { const cell = element('td'); inline(cell, value); row.appendChild(cell); });
        tbody.appendChild(row); consumed = next;
      }
      table.append(thead, tbody); wrap.appendChild(table); parent.appendChild(wrap);
      return;
    }
    if (!line.trim()) { paragraph = null; list = null; return; }
    const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      paragraph = null; list = null;
      // A message is a section of the page, never a second page-level h1.
      const node = element(heading[1].length <= 2 ? 'h2' : 'h3');
      inline(node, heading[2]);
      parent.appendChild(node);
      return;
    }
    const match = line.match(/^\s*(?:([-*•])\s+|(\d+)[.)]\s+)(.+)$/);
    if (match) {
      paragraph = null;
      const tag = match[2] ? 'OL' : 'UL';
      if (!list || list.tagName !== tag) {
        list = element(tag.toLowerCase());
        if (match[2]) list.start = Number(match[2]);
        parent.appendChild(list);
      }
      const item = element('li');
      inline(item, match[3]);
      list.appendChild(item);
    } else {
      list = null;
      if (!paragraph) { paragraph = element('p'); parent.appendChild(paragraph); }
      else paragraph.appendChild(document.createTextNode('\n'));
      inline(paragraph, line);
    }
  });
}
