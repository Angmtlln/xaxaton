/* Создание узлов и мелкие приведения типов. Общее для чата и отчёта. */

export const SVG_NS = 'http://www.w3.org/2000/svg';

export function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

export function svgElement(tag, attributes = {}) {
  const item = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([key, value]) => item.setAttribute(key, String(value)));
  return item;
}

export function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

export function numericValue(value) {
  if (value === null || value === undefined || value === '') return NaN;
  return Number(value);
}
