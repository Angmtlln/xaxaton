/* Узкое безопасное форматирование текста. Никакого HTML и ссылок из прозы. */
import { element } from '../shared/dom.js';

function inline(parent, text) {
  String(text).split(/(\*\*[^*\n]+\*\*)/g).forEach((part) => {
    parent.appendChild(part.startsWith('**') && part.endsWith('**')
      ? element('strong', null, part.slice(2, -2)) : document.createTextNode(part));
  });
}

export function appendProse(parent, text) {
  let paragraph = null;
  let list = null;
  String(text).split('\n').forEach((line) => {
    if (!line.trim()) { paragraph = null; list = null; return; }
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
