/* Единственная точка обращения к chat API. */

export async function sendChatMessage(message, conversationId) {
  const response = await fetch('/api/v1/chat/messages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }
  return { ok: response.ok, payload };
}

export function requestErrorText(payload) {
  const detail = payload && payload.detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).filter(Boolean).join('. ');
  return detail || 'Сервис временно не ответил. Попробуйте ещё раз.';
}
