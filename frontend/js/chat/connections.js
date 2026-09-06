/* Interactive canvas: all nodes/edges come from the verified backend graph. */
import { element, svgElement, safeArray } from '../shared/dom.js';

export function renderConnections(block, context) {
  const graph = block.graph || {};
  const nodes = safeArray(graph.nodes);
  const edges = safeArray(graph.edges);
  const panel = element('section', 'rich-block connection-panel');
  panel.append(element('h3', null, block.title || 'Связи компаний'));
  const toolbar = element('div', 'connection-toolbar');
  const surface = element('div', 'connection-canvas');
  const svg = svgElement('svg', { viewBox: '0 0 780 440', role: 'img',
    'aria-label': `Граф: ${nodes.length} компаний, ${edges.length} оснований связи` });
  const layer = svgElement('g');
  svg.append(layer);
  surface.append(svg);
  let zoom = 1;
  function button(label, callback) {
    const b = element('button', 'connection-control', label);
    b.type = 'button'; b.addEventListener('click', callback); return b;
  }
  function setZoom(value) {
    zoom = Math.min(2, Math.max(0.6, value));
    layer.setAttribute('transform', `translate(${390 * (1 - zoom)} ${220 * (1 - zoom)}) scale(${zoom})`);
  }
  toolbar.append(button('−', () => setZoom(zoom - 0.2)), button('+', () => setZoom(zoom + 0.2)),
    button('Сбросить вид', () => { setZoom(1); reset(); draw(); }),
    element('span', null, 'Узлы можно перетаскивать'));
  const detail = element('div', 'connection-detail');
  detail.setAttribute('aria-live', 'polite');
  const positions = new Map();
  function reset() {
    nodes.forEach((node, i) => positions.set(node.inn, i === 0
      ? { x: 160, y: 220 } : { x: 600, y: 55 + (i - 0.5) * 330 / Math.max(1, nodes.length - 1) }));
  }
  reset();
  function select(node) {
    detail.replaceChildren(element('h4', null, node.name), element('p', null, `ИНН ${node.inn}`));
    if (node.report_date) detail.append(element('p', 'muted', `Снимок: ${String(node.report_date).slice(0, 10)}`));
    if (node.review_state !== 'root') {
      const state = { reviewed: 'Краткий срез получен', partial: 'Краткий срез неполный', unavailable: 'Краткий обзор недоступен' };
      detail.append(element('p', null, state[node.review_state] || 'Нет данных'));
      safeArray(node.observations).forEach((fact) => {
        const row = element('details', 'connection-observation');
        const value = fact.value == null ? 'Нет данных' : typeof fact.value === 'object'
          ? JSON.stringify(fact.value) : String(fact.value);
        row.append(element('summary', null, `${fact.label}: ${value} ${fact.unit || ''}`),
          element('small', null, `Источник: ${fact.field_ref}`));
        detail.append(row);
      });
      if (safeArray(node.gaps).length) {
        const gaps = element('details');
        gaps.append(element('summary', null, 'Ограничения данных'));
        node.gaps.forEach((text) => gaps.append(element('p', null, text)));
        detail.append(gaps);
      }
    }
    if (/^\d{10}(?:\d{2})?$/.test(node.inn)) {
      detail.append(button('Отдельный отчёт', () => context.onSuggestion?.({
        label: 'Отдельный отчёт', prompt: `Проверь контрагента ${node.inn}`, mode: 'submit',
      })));
    }
  }
  const nodeElements = new Map();
  const paths = edges.map((edge, index) => {
    const path = svgElement('path', { class: `connection-edge edge-${edge.kind}` });
    const title = svgElement('title'); title.textContent = `${index + 1}. ${edge.label}${edge.via ? ': ' + edge.via : ''}`;
    path.append(title); layer.append(path);
    const text = svgElement('text', { class: 'connection-edge-number', 'text-anchor': 'middle' });
    text.textContent = String(index + 1); layer.append(text);
    return { path, text, edge };
  });
  let dragged = null;
  function point(event) {
    const p = new DOMPoint(event.clientX, event.clientY);
    return p.matrixTransform(layer.getScreenCTM().inverse());
  }
  nodes.forEach((node) => {
    const g = svgElement('g', { class: `connection-node${node.inn === graph.root_inn ? ' is-root' : ''}`,
      tabindex: '0', role: 'button', 'aria-label': `${node.name}, ИНН ${node.inn}. Показать сведения` });
    g.append(svgElement('rect', { x: -105, y: -28, width: 210, height: 56, rx: 14 }));
    const title = svgElement('text', { y: -3, 'text-anchor': 'middle' });
    title.textContent = node.name.length > 24 ? node.name.slice(0, 23) + '…' : node.name;
    const inn = svgElement('text', { y: 16, 'text-anchor': 'middle', class: 'connection-node-inn' });
    inn.textContent = node.inn; g.append(title, inn);
    g.addEventListener('click', () => select(node));
    g.addEventListener('keydown', (event) => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); select(node); } });
    g.addEventListener('pointerdown', (event) => {
      dragged = { inn: node.inn, start: point(event), pos: { ...positions.get(node.inn) } };
      g.setPointerCapture(event.pointerId);
    });
    g.addEventListener('pointermove', (event) => {
      if (!dragged || dragged.inn !== node.inn) return;
      const p = point(event);
      positions.set(node.inn, { x: Math.max(110, Math.min(670, dragged.pos.x + p.x - dragged.start.x)),
        y: Math.max(35, Math.min(405, dragged.pos.y + p.y - dragged.start.y)) });
      draw();
    });
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach((name) => g.addEventListener(name, () => { dragged = null; }));
    layer.append(g); nodeElements.set(node.inn, g);
  });
  function draw() {
    nodeElements.forEach((g, inn) => { const p = positions.get(inn); g.setAttribute('transform', `translate(${p.x} ${p.y})`); });
    paths.forEach(({ path, text, edge }) => {
      const a = positions.get(edge.source), b = positions.get(edge.target);
      if (!a || !b) return;
      const group = edges.filter(e => e.source === edge.source && e.target === edge.target);
      const bend = (group.indexOf(edge) - (group.length - 1) / 2) * 36;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 + bend;
      path.setAttribute('d', `M ${a.x} ${a.y} Q ${mx} ${my + bend} ${b.x} ${b.y}`);
      text.setAttribute('x', mx); text.setAttribute('y', my - 5);
    });
  }
  draw();
  const list = element('details', 'connection-source-list');
  list.open = true;
  list.append(element('summary', null, `Основания связи (${edges.length})`));
  const ol = element('ol');
  edges.forEach(edge => {
    const li = element('li');
    li.append(element('span', null, `${edge.label}${edge.via ? ' · ' + edge.via : ''}`));
    const refs = element('details');
    refs.append(element('summary', null, `${edge.source} ↔ ${edge.target} · Источники`));
    safeArray(edge.field_refs).forEach(ref => refs.append(element('small', null, ref)));
    li.append(refs); ol.append(li);
  });
  list.append(ol);
  const nodeList = element('div', 'connection-company-list');
  nodes.forEach(node => nodeList.append(button(`${node.name} · ${node.inn}`, () => select(node))));
  const note = graph.state === 'partial'
    ? `Показано ${nodes.length - 1} из ${graph.total_companies} соседей, ${edges.length} из ${graph.total_edges} связей. ${graph.note}` : graph.note;
  panel.append(toolbar, surface, nodeList, detail, list, element('p', 'muted', note));
  if (graph.external_references) panel.append(element('p', 'muted', `Связанных ИНН вне датасета: ${graph.external_references}. Их карточки не проверены.`));
  if (nodes[1]) select(nodes[1]);
  return panel;
}
