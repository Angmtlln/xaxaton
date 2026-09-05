/* Иконки, словари кодов и форматирование значений отчёта. */

/* ------------------------------ иконки ----------------------------- */

export const PATHS = {
  shieldCheck: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path><path d="m9 12 2 2 4-4"></path>',
  shieldAlert: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path><path d="M12 8v4"></path><path d="M12 16h.01"></path>',
  triangleAlert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>',
  circleAlert: '<circle cx="12" cy="12" r="10"></circle><path d="M12 8v4"></path><path d="M12 16h.01"></path>',
  circleCheck: '<circle cx="12" cy="12" r="10"></circle><path d="m9 12 2 2 4-4"></path>',
  circleHelp: '<circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><path d="M12 17h.01"></path>',
  check: '<path d="M20 6 9 17l-5-5"></path>',
  building: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"></path><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"></path><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"></path><path d="M10 6h4"></path><path d="M10 10h4"></path><path d="M10 14h4"></path>',
  chevronDown: '<path d="m6 9 6 6 6-6"></path>',
};

export const icon = (name, cls = '') =>
  `<svg ${cls ? `class="${cls}" ` : ''}viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name]}</svg>`;

/* ------------------------------ словари ---------------------------- */

export const VERDICT = {
  STOP: { theme: 'intensive', icon: 'shieldAlert', label: 'Стоп до устранения' },
  ENHANCED_CHECK: { theme: 'review', icon: 'triangleAlert', label: 'Усиленная проверка' },
  CONDITIONALLY_OK: { theme: 'positive', icon: 'shieldCheck', label: 'Условно допустим' },
  NO_DATA: { theme: 'review', icon: 'circleHelp', label: 'Данных недостаточно' },
};

export const SIGNAL = {
  RISK: { state: 'attention', icon: 'circleAlert', label: 'Жёсткие факты' },
  ATTENTION: { state: 'unknown', icon: 'triangleAlert', label: 'Требует уточнения' },
  NORM: { state: 'clear', icon: 'circleCheck', label: 'Без стоп-факторов' },
  NO_DATA: { state: 'none', icon: 'circleHelp', label: 'Мало данных' },
};

export const EVIDENCE_TYPE = {
  raw_fact: 'Исходный факт',
  derived_metric: 'Расчётная метрика',
  source_signal: 'Сигнал источника',
};

/* ------------------------------ формат ----------------------------- */

export const nf = new Intl.NumberFormat('ru-RU');
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function money(value) {
  if (value === null || value === undefined || value === '') return 'нет данных';
  const n = Number(value);
  if (!isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(abs >= 1e10 ? 0 : 1).replace('.', ',') + ' млрд ₽';
  if (abs >= 1e6) return (n / 1e6).toFixed(abs >= 1e7 ? 0 : 1).replace('.', ',') + ' млн ₽';
  if (abs >= 1e3) return nf.format(Math.round(n)) + ' ₽';
  return nf.format(Math.round(n * 100) / 100) + ' ₽';
}

export function plain(v) {
  if (v === null || v === undefined || v === '') return 'нет данных';
  if (typeof v === 'boolean') return v ? 'да' : 'нет';
  if (typeof v === 'number') return nf.format(v);
  if (Array.isArray(v)) {
    if (!v.length) return 'нет';
    return v.map((x) => (x && typeof x === 'object'
      ? (x.meaning || x.code || x.name || x.label || Object.values(x).join(' '))
      : x)).join(', ');
  }
  if (typeof v === 'object') {
    return Object.entries(v).map(([k, val]) => `${k}: ${plain(val)}`).join(', ');
  }
  return String(v);
}

export const factText = (fact) => (fact.unit === 'руб' ? money(fact.value) : plain(fact.value));

export function evidenceType(fact) {
  if ((fact.field_ref || '').includes('reputationalRisks')) return 'source_signal';
  return fact.source === 'raw' ? 'raw_fact' : 'derived_metric';
}

export function blockLabel(title) {
  return ({
    'Кто это': 'Профиль компании',
    'Надёжность и правовые риски': 'Правовые риски',
    'Финансовое состояние': 'Финансовые риски',
    'Опыт и позитивные сигналы': 'Опыт и репутация',
  })[title] || title;
}

export function signalWord(count) {
  const mod10 = count % 10, mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'сигнал';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'сигнала';
  return 'сигналов';
}

export function gapWord(count) {
  const mod10 = count % 10, mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'пробел';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'пробела';
  return 'пробелов';
}
