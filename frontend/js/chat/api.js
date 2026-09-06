/* Chat result and public stage stream. A truncated stream is never a success. */
export async function sendChatMessage(message, conversationId, onProgress) {
  const response = await fetch('/api/v1/chat/messages/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  if (!response.ok || !response.headers.get('content-type')?.includes('application/x-ndjson')) {
    let payload = null;
    try { payload = await response.json(); } catch { /* safe error below */ }
    return { ok: response.ok, payload };
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', result = null, streamError = null;
  function line(text) {
    if (!text.trim()) return;
    const event = JSON.parse(text);
    if (event.type === 'progress') onProgress?.(event);
    if (event.type === 'result') result = event.payload;
    if (event.type === 'error') streamError = event.detail;
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
      }
      if (done) break;
    }
    if (buffer.trim()) line(buffer);
  } finally { await reader.cancel(); reader.releaseLock(); }
  if (streamError || !result) return { ok: false, payload: { detail: streamError || 'Соединение прервалось до получения ответа. Повторите запрос.' } };
  return { ok: true, payload: result };
}

export function requestErrorText(payload) {
  const detail = payload && payload.detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).filter(Boolean).join('. ');
  return detail || 'Сервис временно не ответил. Попробуйте ещё раз.';
}
