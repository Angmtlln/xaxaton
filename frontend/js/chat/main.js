/* Экран диалога: состояние беседы, отправка сообщений и подписки на события.
   Рендер артефактов живёт в artifacts.js, сетевой вызов — в api.js. */
import { attachCompanySearch } from './company-search.js';
import { element } from '../shared/dom.js';
import { buildAssistantMessage } from './artifacts.js';
import { requestErrorText, sendChatMessage } from './api.js';
import { registerTurn, resetNavigation } from './navigation.js';

const form = document.getElementById('chat-form');
const input = document.getElementById('chat-input');
const companySearch = attachCompanySearch(input);
const composerHint = document.getElementById('composer-hint');
const MESSAGE_LIMIT = 4000;
const sendButton = document.getElementById('send-button');
const intro = document.getElementById('chat-intro');
const thread = document.getElementById('chat-thread');
const lastReportLink = document.getElementById('last-report-link');
const activeCompanyBar = document.getElementById('active-company-bar');
const activeCompanyLabel = document.getElementById('active-company-label');
const newConversationButton = document.getElementById('new-conversation');
const CHAT_STORAGE_KEY = 'counterparty-current-conversation-v1';
let conversationId = null;
let activeCompany = null;
let conversationHistory = [];
let sessionExpired = false;
const composerShell = document.querySelector('.chat-composer-shell');
const landingSlot = document.getElementById('landing-composer-slot');

function syncLayout() {
  const landing = !conversationHistory.length;
  document.body.classList.toggle('landing-mode', landing);
  (landing ? landingSlot : composerShell).appendChild(form);
  intro.hidden = !landing;
  thread.hidden = landing;
  resizeInput();
}

function updateActions() {
  thread.querySelectorAll('.export-actions button').forEach(button => { button.disabled = form.hasAttribute('aria-busy'); });
  thread.querySelectorAll('.company-choice').forEach((block) => {
    block.querySelectorAll('button').forEach((button) => { button.disabled = form.hasAttribute('aria-busy') || block.closest('article') !== thread.lastElementChild; });
  });
  const rows = [...thread.querySelectorAll('.suggested-actions')];
  rows.forEach((row, index) => {
    row.hidden = index !== rows.length - 1 || row.closest('article') !== thread.lastElementChild;
    row.querySelectorAll('button').forEach((button) => { button.disabled = form.hasAttribute('aria-busy'); });
  });
}

function saveConversation() {
  try {
    sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({
      conversationId, activeCompany, sessionExpired, draft: input.value, messages: conversationHistory.slice(-24),
    }));
  } catch (error) { /* Диалог остаётся доступен при запрете/переполнении storage. */ }
}

function showActiveCompany() {
  activeCompanyBar.hidden = !conversationId && !conversationHistory.length;
  input.placeholder = activeCompany ? 'Уточните по компании'
    : 'Название, ИНН или вопрос…';
  activeCompanyLabel.textContent = sessionExpired
    ? 'Сессия истекла · следующий запрос начнёт новый диалог, укажите название или ИНН'
    : activeCompany
    ? `${activeCompany.name || 'Контрагент'} · ИНН ${activeCompany.inn}`
    : 'Компания ещё не выбрана';
}

function resetConversation() {
  companySearch.close();
  if (form.hasAttribute('aria-busy')) return;
  const draft = sessionExpired ? input.value : '';
  conversationId = null;
  activeCompany = null;
  sessionExpired = false;
  conversationHistory = [];
  resetNavigation();
  thread.querySelectorAll('.news-section').forEach((section) => section.dispose?.());
  thread.replaceChildren();
  thread.hidden = true;
  intro.hidden = false;
  lastReportLink.hidden = true;
  input.value = draft;
  syncLayout();
  showActiveCompany();
  saveConversation();
  input.focus();
}

function resizeInput() {
  const length = Array.from(input.value.trim()).length;
  const invalid = length > MESSAGE_LIMIT;
  input.setCustomValidity(invalid ? `Максимум ${MESSAGE_LIMIT} символов. Сейчас ${length}. Сократите запрос.` : '');
  input.setAttribute('aria-invalid', String(invalid));
  composerHint.textContent = invalid ? `${length} / ${MESSAGE_LIMIT} · сократите запрос, текст сохранён`
    : 'Название или ИНН · @ — подсказки внутри вопроса';
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
}

function setBusy(value) {
  if (value) companySearch.close();
  input.disabled = value;
  sendButton.disabled = value;
  newConversationButton.disabled = value;
  form.toggleAttribute('aria-busy', value);
  updateActions();
}

function scrollToLatest() {
  window.requestAnimationFrame(() => {
    window.scrollTo({ top: document.body.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  });
}

function appendUserMessage(message) {
  const article = element('article', 'chat-message chat-message-user');
  article.setAttribute('aria-label', 'Вы');
  const bubble = element('div', 'user-bubble', message);
  article.appendChild(bubble);
  thread.appendChild(article);
}

function appendLoading() {
  const article = element('article', 'chat-message chat-message-assistant loading-message');
  article.id = 'active-loading';
  const avatar = element('div', 'assistant-avatar');
  avatar.setAttribute('aria-hidden', 'true');
  const body = element('div', 'assistant-content assistant-loading');
  body.setAttribute('role', 'status');
  body.setAttribute('aria-live', 'polite');
  const title = element('strong', 'loading-title', 'Отправляю запрос');
  const note = element('span', 'loading-detail', 'Жду подтверждения от сервиса');
  const dots = element('span', 'loading-dots');
  dots.setAttribute('aria-hidden', 'true');
  dots.append(element('i'), element('i'), element('i'));
  body.append(title, note, dots);
  article.append(avatar, body);
  thread.appendChild(article);
  return article;
}

function appendAssistantMessage(payload) {
  const article = buildAssistantMessage(payload, {
    index: thread.children.length,
    onCompanyChoice: (company, searchId) => sendMessage(`Выбираю ${company.name}, ИНН ${company.inn}`, { search_id: searchId, inn: company.inn }),
    onReportUrl: (url) => {
      lastReportLink.href = url;
      lastReportLink.hidden = false;
    },
    onSuggestion: (action) => {
      if (form.hasAttribute('aria-busy')) return;
      if (action.type === 'export_pdf') { exportPdf(payload.conversation_id, action.result_id); return; }
      if (action.mode === 'submit') { sendMessage(action.prompt); return; }
      input.value = action.prompt;
      resizeInput();
      input.focus();
    },
  });
  thread.appendChild(article);
  registerTurn(payload, article);
  updateActions();
}

async function exportPdf(cid, resultId) {
  if (form.hasAttribute('aria-busy')) return;
  setBusy(true);
  const loading = appendLoading();
  loading.querySelector('.loading-title').textContent = 'Готовим PDF';
  loading.querySelector('.loading-detail').textContent = 'Сохраняем результат с графиками и источниками';
  scrollToLatest();
  let payload;
  try {
    const response = await fetch(`/api/v1/chat/${encodeURIComponent(cid)}/exports`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ result_id: resultId }),
    });
    const file = await response.json();
    if (!response.ok) throw new Error(typeof file.detail === 'string' ? file.detail : 'Не удалось создать PDF. Попробуйте ещё раз.');
    payload = { message: 'PDF готов.', conversation_id: cid, attachments: [file],
      metadata: { status: 'completed', agent_run_id: `pdf-${file.id}` } };
  } catch (error) {
    payload = { message: error.message || 'Не удалось создать PDF.', conversation_id: cid,
      suggested_actions: [{ type: 'export_pdf', label: 'Повторить экспорт PDF', result_id: resultId }],
      metadata: { status: 'error', agent_run_id: `pdf-error-${Date.now()}` } };
  } finally {
    loading.remove();
    setBusy(false);
  }
  appendAssistantMessage(payload);
  conversationHistory.push({ role: 'assistant', payload });
  saveConversation();
  scrollToLatest();
}

function appendRequestError(message) {
  const payload = {
    message,
    blocks: [],
    evidence: [],
    suggested_actions: [],
    metadata: { status: 'error', agent_run_id: `client-${Date.now()}` },
  };
  appendAssistantMessage(payload);
  conversationHistory.push({ role: 'assistant', payload });
  saveConversation();
}

async function sendMessage(message, companySelection = null) {
  const text = String(message || '').trim();
  if (!text || form.hasAttribute('aria-busy')) return;
  if (Array.from(text).length > MESSAGE_LIMIT) {
    input.value = String(message);
    resizeInput();
    input.reportValidity();
    return;
  }
  intro.hidden = true;
  thread.hidden = false;
  appendUserMessage(text);
  conversationHistory.push({ role: 'user', message: text });
  syncLayout();
  saveConversation();
  showActiveCompany();
  input.value = '';
  resizeInput();
  setBusy(true);
  const loading = appendLoading();
  scrollToLatest();

  try {
    const { ok, payload } = await sendChatMessage(text, conversationId, (event) => {
      loading.querySelector('.loading-title').textContent = event.title;
      loading.querySelector('.loading-detail').textContent = event.detail;
      loading.dataset.stage = event.stage;
    }, companySelection);
    loading.remove();
    if (!ok) {
      appendRequestError(requestErrorText(payload));
    } else {
      if (payload && payload.metadata && payload.metadata.error_code === 'unknown_conversation') {
        conversationId = null;
        activeCompany = null;
        sessionExpired = true;
        input.value = text;
        resizeInput();
        resetNavigation();
        lastReportLink.hidden = true;
      } else if (payload && payload.conversation_id) {
        conversationId = payload.conversation_id;
        sessionExpired = false;
        activeCompany = payload.active_company || null;
      }
      appendAssistantMessage(payload || {});
      conversationHistory.push({ role: 'assistant', payload: payload || {} });
      saveConversation();
      showActiveCompany();
    }
  } catch (error) {
    loading.remove();
    appendRequestError('Не удалось связаться с сервисом. Проверьте соединение и повторите запрос.');
  } finally {
    setBusy(false);
    input.focus({ preventScroll: true });
    const latest = thread.lastElementChild;
    if (latest) window.requestAnimationFrame(() => latest.scrollIntoView({
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start',
    }));
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener('input', () => { resizeInput(); saveConversation(); });
window.addEventListener('resize', resizeInput);
input.addEventListener('keydown', (event) => {
  if (!event.defaultPrevented && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll('[data-prompt]').forEach((button) => {
  button.addEventListener('click', () => {
    const prompt = button.dataset.prompt || '';
    input.value = prompt;
    resizeInput();
    input.focus();
  });
});

resizeInput();

newConversationButton.addEventListener('click', resetConversation);
try {
  const saved = JSON.parse(sessionStorage.getItem(CHAT_STORAGE_KEY) || 'null');
  if (saved && Array.isArray(saved.messages)) {
    conversationId = typeof saved.conversationId === 'string' ? saved.conversationId : null;
    activeCompany = saved.activeCompany || null;
    sessionExpired = saved.sessionExpired === true;
    input.value = typeof saved.draft === 'string' ? saved.draft : '';
    conversationHistory = saved.messages.slice(-24);
    conversationHistory.forEach((item) => {
      if (item.role === 'user') appendUserMessage(item.message);
      else if (item.role === 'assistant' && item.payload) appendAssistantMessage(item.payload);
    });
    intro.hidden = conversationHistory.length > 0;
    thread.hidden = !conversationHistory.length;
    showActiveCompany();
  }
} catch (error) { /* Некорректный сохранённый диалог не мешает начать новый. */ }
syncLayout();
updateActions();
