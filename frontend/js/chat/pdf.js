import { buildAssistantMessage } from './artifacts.js';

window.renderPdf = async (payload) => {
  const root = document.getElementById('pdf-content');
  root.replaceChildren(buildAssistantMessage({ ...payload, suggested_actions: [], attachments: [] }));
  root.querySelectorAll('button.summary-metric-value').forEach(button => {
    const value = document.createElement('strong');
    value.className = 'summary-metric-value';
    value.textContent = button.textContent;
    button.replaceWith(value);
  });
  root.querySelectorAll('.company-summary').forEach(card => {
    const explanation = card.querySelector('.radar-explanation');
    if (explanation) card.appendChild(explanation);
  });
  // Materialize disclosures: offscreen <details> can lose content across PDF pages.
  root.querySelectorAll('details').forEach(node => {
    const section = document.createElement('section');
    section.className = node.className;
    const summary = node.querySelector(':scope > summary');
    if (summary) {
      const title = document.createElement('div');
      title.className = 'pdf-disclosure-title';
      title.append(...summary.childNodes);
      summary.replaceWith(title);
    }
    section.append(...node.childNodes);
    node.replaceWith(section);
  });
  root.querySelectorAll('[title]').forEach(node => {
    if (node.classList.contains('comparison-value') && node.title) {
      const note = document.createElement('small');
      note.className = 'pdf-cell-note';
      note.textContent = node.title;
      node.after(note);
    }
  });
  await document.fonts.ready;
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
};
