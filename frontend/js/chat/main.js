/* Экран диалога: состояние беседы, отправка сообщений и подписки на события.
   Рендер артефактов живёт в artifacts.js, сетевой вызов — в api.js. */
import { element } from '../shared/dom.js';
import { buildAssistantMessage } from './artifacts.js';
import { requestErrorText, sendChatMessage } from './api.js';

const form = document.getElementById('chat-form');
const input = document.getElementById('chat-input');
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

function saveConversation() {
  try {
    sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({
      conversationId, activeCompany, messages: conversationHistory.slice(-24),
    }));
  } catch (error) { /* Диалог остаётся доступен при запрете/переполнении storage. */ }
}

function showActiveCompany() {
  activeCompanyBar.hidden = !conversationId && !conversationHistory.length;
  input.placeholder = activeCompany ? 'Уточните по компании'
    : 'ИНН или вопрос о компании';
  activeCompanyLabel.textContent = activeCompany
    ? `${activeCompany.name || 'Контрагент'} · ИНН ${activeCompany.inn}`
    : 'Компания ещё не выбрана';
}

function resetConversation() {
  if (form.hasAttribute('aria-busy')) return;
  conversationId = null;
  activeCompany = null;
  conversationHistory = [];
  thread.querySelectorAll('.news-section').forEach((section) => section.dispose?.());
  thread.replaceChildren();
  thread.hidden = true;
  intro.hidden = false;
  lastReportLink.hidden = true;
  showActiveCompany();
  saveConversation();
  input.focus();
}

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
}

function setBusy(value) {
  input.disabled = value;
  sendButton.disabled = value;
  newConversationButton.disabled = value;
  form.toggleAttribute('aria-busy', value);
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
  const avatar = element('div', 'assistant-avatar', 'A');
  avatar.setAttribute('aria-hidden', 'true');
  const body = element('div', 'assistant-content assistant-loading');
  const title = element('strong', null, 'Разбираюсь в вашем вопросе');
  const note = element('span', null, 'Проверяю доступные данные и готовлю ответ');
  const dots = element('span', 'loading-dots');
  dots.setAttribute('aria-hidden', 'true');
  dots.append(element('i'), element('i'), element('i'));
  body.append(title, note, dots);
  article.append(avatar, body);
  thread.appendChild(article);
  return article;
}

function appendAssistantMessage(payload) {
  thread.appendChild(buildAssistantMessage(payload, {
    index: thread.children.length,
    onReportUrl: (url) => {
      lastReportLink.href = url;
      lastReportLink.hidden = false;
    },
    onSuggestion: (text) => {
      input.value = text;
      resizeInput();
      input.focus();
    },
  }));
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

async function sendMessage(message) {
  const text = String(message || '').trim();
  if (!text || form.hasAttribute('aria-busy')) return;
  intro.hidden = true;
  thread.hidden = false;
  appendUserMessage(text);
  conversationHistory.push({ role: 'user', message: text });
  saveConversation();
  showActiveCompany();
  input.value = '';
  resizeInput();
  setBusy(true);
  const loading = appendLoading();
  scrollToLatest();

  try {
    const { ok, payload } = await sendChatMessage(text, conversationId);
    loading.remove();
    if (!ok) {
      appendRequestError(requestErrorText(payload));
    } else {
      if (payload && payload.metadata && payload.metadata.error_code === 'unknown_conversation') {
        conversationId = null;
        activeCompany = null;
        lastReportLink.hidden = true;
      } else if (payload && payload.conversation_id) {
        conversationId = payload.conversation_id;
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

input.addEventListener('input', resizeInput);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
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
    if (/\d{10,12}/.test(prompt)) sendMessage(prompt);
  });
});

resizeInput();

newConversationButton.addEventListener('click', resetConversation);
try {
  const saved = JSON.parse(sessionStorage.getItem(CHAT_STORAGE_KEY) || 'null');
  if (saved && Array.isArray(saved.messages)) {
    conversationId = typeof saved.conversationId === 'string' ? saved.conversationId : null;
    activeCompany = saved.activeCompany || null;
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
