/* Новости остаются отдельным внешним материалом текущего full-check ответа. */
import { element, safeArray } from '../shared/dom.js';

function newsUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function renderNews(payload, prefix) {
  if (!payload.external_news_status) return null;
  const section = element('section', 'news-section');
  section.setAttribute('aria-label', 'Новости из внешних источников');
  const heading = element('div', 'news-heading');
  const title = element('div');
  title.append(element('h3', null, 'Важные новости'), element('span', null, 'Внешние источники'));
  heading.appendChild(title);
  section.appendChild(heading);
  const items = safeArray(payload.external_news).slice(0, 4)
    .filter((item) => item && newsUrl(item.url));
  if (!items.length) {
    const message = payload.external_news_status === 'completed' ? 'Подходящие новости не отобраны.'
      : payload.external_news_status === 'partial' ? 'Подборка неполная. Подтверждённых материалов нет.'
      : 'Подборка новостей недоступна.';
    section.appendChild(element('p', 'news-empty', message));
    return section;
  }
  const strip = element('div', 'news-strip');
  strip.id = `news-${prefix}`;
  strip.tabIndex = 0;
  strip.setAttribute('role', 'region');
  strip.setAttribute('aria-label', 'Новостные карточки, прокрутка по горизонтали');
  items.forEach((item) => {
    const card = element('article', 'news-card');
    const meta = element('div', 'news-meta');
    const date = /^\d{4}-\d{2}-\d{2}$/.test(String(item.date)) ? new Date(`${item.date}T00:00:00Z`) : null;
    const time = element('time', null, date && Number.isFinite(date.getTime())
      ? date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
      : 'Дата не указана');
    if (date && Number.isFinite(date.getTime())) time.dateTime = item.date;
    meta.append(element('span', null, item.source || new URL(item.url).hostname), time);
    const h = element('h4');
    const link = element('a', null, item.title || 'Открыть публикацию');
    link.href = newsUrl(item.url);
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    h.appendChild(link);
    card.append(meta, h, element('p', null, item.summary || ''));
    strip.appendChild(card);
  });
  const controls = element('div', 'news-controls');
  for (const [label, text, direction] of [['Предыдущие новости', '←', -1], ['Следующие новости', '→', 1]]) {
    const button = element('button', null, text);
    button.type = 'button';
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-controls', strip.id);
    button.addEventListener('click', () => strip.scrollBy({ left: direction * (strip.firstElementChild.offsetWidth + 12),
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }));
    controls.appendChild(button);
  }
  const update = () => {
    controls.children[0].disabled = strip.scrollLeft < 2;
    controls.children[1].disabled = strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 2;
  };
  strip.addEventListener('scroll', update, { passive: true });
  // main.js освобождает observer при сбросе диалога.
  const observer = new ResizeObserver(update);
  observer.observe(strip);
  section.dispose = () => observer.disconnect();
  heading.appendChild(controls);
  section.appendChild(strip);
  if (payload.external_news_status === 'partial') section.appendChild(element('p', 'news-empty', 'Подборка неполная: часть материалов не удалось подтвердить.'));
  return section;
}
