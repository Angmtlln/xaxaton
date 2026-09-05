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
