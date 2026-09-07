/* Interactive canvas: all nodes/edges come from the verified backend graph. */
import { element, svgElement, safeArray } from '../shared/dom.js';

export function renderConnections(block, context) {
  const graph = block.graph || {};
  const nodes = safeArray(graph.nodes);
  const edges = safeArray(graph.edges);
  const groups = new Map();
  edges.forEach(edge => {
    const key = [edge.source, edge.target].sort().join(':');
    if (!groups.has(key)) groups.set(key, { ...edge, reasons: [] });
    groups.get(key).reasons.push(edge.label);
  });
  const links = [...groups.values()];
  const height = Math.max(180, (nodes.length - 1) * 76 + 40);
  const panel = element('section', 'rich-block connection-panel');
  panel.append(element('h3', null, 'Связи компании'));
  const toolbar = element('div', 'connection-toolbar');
  const surface = element('div', 'connection-canvas');
  const svg = svgElement('svg', { viewBox: `0 0 780 ${height}`, role: 'img',
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
    layer.setAttribute('transform', `translate(${390 * (1 - zoom)} ${height / 2 * (1 - zoom)}) scale(${zoom})`);
  }
  toolbar.append(button('−', () => setZoom(zoom - 0.2)), button('+', () => setZoom(zoom + 0.2)),
    button('Сбросить вид', () => { setZoom(1); reset(); draw(); }));
  const detail = element('div', 'connection-detail');
  detail.hidden = true;
  detail.setAttribute('aria-live', 'polite');
  const positions = new Map();
  function reset() {
    nodes.forEach((node, i) => positions.set(node.inn, i === 0
      ? { x: 160, y: height / 2 } : { x: 600, y: (i - 0.5) * height / Math.max(1, nodes.length - 1) }));
  }
  reset();
  function select(node) {
    detail.hidden = false;
    nodeElements.forEach((g, inn) => g.classList.toggle('is-selected', inn === node.inn));
    detail.replaceChildren(element('h4', null, node.name), element('p', 'muted', `ИНН ${node.inn}`));
    const reasons = [...new Set(edges.filter(e => e.source === node.inn || e.target === node.inn).map(e => e.label))];
    const list = element('ul', 'connection-reasons');
    reasons.forEach(reason => list.append(element('li', null, reason)));
    detail.append(list);
    if (node.report_date) detail.append(element('p', 'muted', `Данные на ${String(node.report_date).slice(0, 10).split('-').reverse().join('.')}`));
    if (['partial', 'unavailable'].includes(node.review_state)) detail.append(element('p', 'muted', 'Данные о связанной компании неполные.'));
    if (node.inn !== graph.root_inn && /^\d{10}(?:\d{2})?$/.test(node.inn)) detail.append(button('Проверить компанию', () => context.onSuggestion?.({ label: 'Проверить компанию', prompt: `Проверь контрагента ${node.inn}`, mode: 'submit' })));
    detail.append(button('Скрыть', () => { detail.hidden = true; }));
  }
  const nodeElements = new Map();
  const paths = links.map((edge) => {
    const path = svgElement('path', { class: `connection-edge edge-${edge.kind}` });
    const title = svgElement('title'); title.textContent = [...new Set(edge.reasons)].join(' · ');
    path.append(title); layer.append(path);
    const text = svgElement('text', { class: 'connection-edge-number', 'text-anchor': 'middle' });
    text.textContent = edge.reasons.length === 1 ? edge.label : `${edge.reasons.length} оснований связи`; layer.append(text);
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
        y: Math.max(35, Math.min(height - 35, dragged.pos.y + p.y - dragged.start.y)) });
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
      const bend = 0;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 + bend;
      path.setAttribute('d', `M ${a.x} ${a.y} Q ${mx} ${my + bend} ${b.x} ${b.y}`);
      text.setAttribute('x', mx); text.setAttribute('y', my - 5);
    });
  }
  draw();
  panel.append(toolbar, surface, detail);
  if (graph.state === 'partial') panel.append(element('p', 'muted', 'Показана только часть найденных связей.'));
  panel.append(element('p', 'muted', 'Выберите компанию, чтобы посмотреть основания связи. Связи за пределами доступных данных могут быть не учтены.'));
  return panel;
}
