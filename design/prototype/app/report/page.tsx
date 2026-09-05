'use client';
/* oxlint-disable next/no-html-link-for-pages */

import {
  ArrowLeft,
  Building2,
  Check,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  CircleHelp,
  Database,
  FileText,
  Search,
  Send,
  ShieldAlert,
  ShieldCheck,
  Shuffle,
  TriangleAlert,
} from 'lucide-react';
import { useState } from 'react';
import type { SyntheticEvent } from 'react';

import { ProductButton } from '@/components/product-button';

type CardState = 'attention' | 'clear' | 'unknown';

type Evidence = {
  type: 'RAW_FACT' | 'DERIVED_METRIC' | 'SOURCE_SIGNAL';
  label: string;
  value: string;
  source: string;
};

type RiskCard = {
  id: string;
  title: string;
  state: CardState;
  label: string;
  summary: string;
  evidence: Evidence[];
};

type Scenario = {
  company: string;
  legalName: string;
  inn: string;
  kpp: string;
  location: string;
  status: string;
  updated: string;
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN';
  zskRiskLevel: 'GREEN' | 'YELLOW' | 'RED';
  summaryTheme: 'positive' | 'review' | 'intensive';
  summaryLabel: string;
  headline: string;
  note: string;
  summaryItems: {
    label: string;
    value: string;
    detail: string;
  }[];
  factsCount: number;
  cards: RiskCard[];
};

const scenarios: Scenario[] = [
  {
    company: 'ООО «Вектор Про»',
    legalName: 'Общество с ограниченной ответственностью «Вектор Про»',
    inn: '7707421865',
    kpp: '770701001',
    location: 'Москва',
    status: 'Действующая организация',
    updated: 'сегодня, 14:32',
    riskLevel: 'MEDIUM',
    zskRiskLevel: 'GREEN',
    summaryTheme: 'review',
    summaryLabel: 'Проверить 3 наблюдения',
    headline: 'Ликвидность снизилась, есть взыскание и расхождение по адресу',
    note: 'Банковские оценки показаны отдельно. Вывод собран только по фактам мокового отчёта.',
    summaryItems: [
      {
        label: 'Текущая ликвидность',
        value: '0,82',
        detail: 'Снизилась с 1,14; краткосрочные обязательства выросли на 37%.',
      },
      {
        label: 'Открытое взыскание',
        value: '128 400 ₽',
        detail: 'Одно незавершённое исполнительное производство.',
      },
      {
        label: 'Расхождение источников',
        value: '5 юрлиц по адресу',
        detail: 'Источник помечает адрес как массовый — факт требует сверки.',
      },
    ],
    factsCount: 18,
    cards: [
      {
        id: 'finance',
        title: 'Финансовое состояние',
        state: 'attention',
        label: 'Требует внимания',
        summary: 'Краткосрочные обязательства растут быстрее оборотных активов.',
        evidence: [
          {
            type: 'DERIVED_METRIC',
            label: 'Коэффициент текущей ликвидности',
            value: '0,82 за 2025 год; 1,14 за 2024 год',
            source: 'coefficient.currentLiquidity',
          },
          {
            type: 'RAW_FACT',
            label: 'Краткосрочные обязательства',
            value: '184,2 млн ₽, рост на 37% год к году',
            source: 'finReports[2025].shortTermLiabilities',
          },
        ],
      },
      {
        id: 'legal',
        title: 'Суды и арбитраж',
        state: 'clear',
        label: 'Фактов не найдено',
        summary: 'В доступном периоде нет дел, где компания выступает ответчиком.',
        evidence: [
          {
            type: 'RAW_FACT',
            label: 'Дела в роли ответчика',
            value: '0 дел за последние 24 месяца',
            source: 'arbitrationByStatus.defendant',
          },
        ],
      },
      {
        id: 'enforcement',
        title: 'Исполнительные производства',
        state: 'attention',
        label: 'Найден факт',
        summary: 'Есть одно открытое производство на небольшую сумму.',
        evidence: [
          {
            type: 'RAW_FACT',
            label: 'Открытое производство',
            value: '1 производство на 128 400 ₽',
            source: 'executionProceedings.items[0]',
          },
          {
            type: 'SOURCE_SIGNAL',
            label: 'Сигнал источника',
            value: 'Наличие незавершённого взыскания',
            source: 'reputationalRisks.executionProceedings',
          },
        ],
      },
      {
        id: 'ownership',
        title: 'Владельцы и руководство',
        state: 'clear',
        label: 'Без изменений',
        summary: 'Директор и состав учредителей не менялись более трёх лет.',
        evidence: [
          {
            type: 'RAW_FACT',
            label: 'Генеральный директор',
            value: 'Смирнов Алексей Олегович, с 18.04.2022',
            source: 'foundersInfo.director',
          },
          {
            type: 'RAW_FACT',
            label: 'Учредители',
            value: '2 физических лица, доли раскрыты полностью',
            source: 'foundersInfo.founders',
          },
        ],
      },
      {
        id: 'reputation',
        title: 'Комплаенс и репутация',
        state: 'unknown',
        label: 'Нужна сверка',
        summary: 'Сигнал источника не подтверждается открытыми фактами отчёта.',
        evidence: [
          {
            type: 'SOURCE_SIGNAL',
            label: 'Сигнал источника',
            value: 'Возможная массовая регистрация',
            source: 'reputationalRisks.massRegistration',
          },
          {
            type: 'RAW_FACT',
            label: 'Адрес регистрации',
            value: '5 действующих юрлиц по адресу',
            source: 'baseInfo.registrationAddress',
          },
        ],
      },
      {
        id: 'procurement',
        title: 'Закупки и контракты',
        state: 'clear',
        label: 'Стабильная история',
        summary: 'Исполнено 12 контрактов, расторгнутых по вине поставщика нет.',
        evidence: [
          {
            type: 'DERIVED_METRIC',
            label: 'Исполнение контрактов',
            value: '12 из 12 завершены без нарушений',
            source: 'procurements.contracts',
          },
        ],
      },
    ],
  },
  {
    company: 'АО «Северный Порт»',
    legalName: 'Акционерное общество «Северный Порт»',
    inn: '7805129047',
    kpp: '780501001',
    location: 'Санкт-Петербург',
    status: 'Действующая организация',
    updated: 'сегодня, 14:18',
    riskLevel: 'LOW',
    zskRiskLevel: 'GREEN',
    summaryTheme: 'positive',
    summaryLabel: 'Негативных фактов не найдено',
    headline: 'Финансы растут, судебных взысканий и открытых производств нет',
    note: 'Это не гарантия благонадёжности: вывод ограничен составом и датой моковых данных.',
    summaryItems: [
      {
        label: 'Выручка за 2025 год',
        value: '2,4 млрд ₽',
        detail: 'Рост на 12% год к году, рентабельность продаж — 9,7%.',
      },
      {
        label: 'Дела в роли ответчика',
        value: '0',
        detail: 'За доступный период найдено только одно дело в роли истца.',
      },
      {
        label: 'Открытые производства',
        value: '0',
        detail: 'Исполнительные производства в моковом отчёте отсутствуют.',
      },
    ],
    factsCount: 21,
    cards: [
      {
        id: 'finance',
        title: 'Финансовое состояние',
        state: 'clear',
        label: 'Показатели устойчивы',
        summary: 'Выручка и чистые активы растут, просроченная задолженность не указана.',
        evidence: [
          { type: 'RAW_FACT', label: 'Выручка', value: '2,4 млрд ₽, рост 12%', source: 'finReports[2025].revenue' },
          { type: 'DERIVED_METRIC', label: 'Рентабельность продаж', value: '9,7%', source: 'finReports[2025].derived.salesMargin' },
        ],
      },
      {
        id: 'legal',
        title: 'Суды и арбитраж',
        state: 'clear',
        label: 'Низкая активность',
        summary: 'Одно завершённое дело в роли истца, дел в роли ответчика нет.',
        evidence: [
          { type: 'RAW_FACT', label: 'Арбитражные дела', value: '1 дело завершено в пользу компании', source: 'arbitrationCases.items[0]' },
        ],
      },
      {
        id: 'enforcement',
        title: 'Исполнительные производства',
        state: 'clear',
        label: 'Фактов не найдено',
        summary: 'Открытые исполнительные производства отсутствуют.',
        evidence: [
          { type: 'RAW_FACT', label: 'Открытые производства', value: '0', source: 'executionProceedings.activeCount' },
        ],
      },
      {
        id: 'ownership',
        title: 'Владельцы и руководство',
        state: 'clear',
        label: 'Структура раскрыта',
        summary: 'Руководитель действует пять лет, сведения об акционерах доступны.',
        evidence: [
          { type: 'RAW_FACT', label: 'Руководитель', value: 'Кузнецова Елена Игоревна, с 2021 года', source: 'foundersInfo.director' },
        ],
      },
      {
        id: 'reputation',
        title: 'Комплаенс и репутация',
        state: 'clear',
        label: 'Сигналов нет',
        summary: 'В источнике репутационных сигналов значимые записи отсутствуют.',
        evidence: [
          { type: 'SOURCE_SIGNAL', label: 'Репутационные сигналы', value: '0 активных записей', source: 'reputationalRisks' },
        ],
      },
      {
        id: 'procurement',
        title: 'Закупки и контракты',
        state: 'clear',
        label: 'Есть опыт',
        summary: 'Компания регулярно исполняет крупные транспортные контракты.',
        evidence: [
          { type: 'RAW_FACT', label: 'Контракты', value: '37 контрактов на 1,1 млрд ₽', source: 'procurements.summary' },
        ],
      },
    ],
  },
  {
    company: 'ООО «Городская Линия»',
    legalName: 'Общество с ограниченной ответственностью «Городская Линия»',
    inn: '6671198032',
    kpp: '667101001',
    location: 'Екатеринбург',
    status: 'Действующая организация',
    updated: 'вчера, 18:46',
    riskLevel: 'HIGH',
    zskRiskLevel: 'YELLOW',
    summaryTheme: 'intensive',
    summaryLabel: 'Сначала проверить эти факты',
    headline: 'Убыток, судебные требования и открытые взыскания',
    note: 'Оценка банка и ЗСК не объединяются. AI показывает наблюдения, но не присваивает свой уровень риска.',
    summaryItems: [
      {
        label: 'Чистый результат',
        value: '−14,8 млн ₽',
        detail: 'Второй убыточный год подряд, выручка снизилась на 19%.',
      },
      {
        label: 'Требования к компании',
        value: '26,4 млн ₽',
        detail: 'Четыре арбитражных дела в роли ответчика.',
      },
      {
        label: 'Открытые взыскания',
        value: '8,7 млн ₽',
        detail: 'Семь незавершённых исполнительных производств.',
      },
    ],
    factsCount: 24,
    cards: [
      {
        id: 'finance',
        title: 'Финансовое состояние',
        state: 'attention',
        label: 'Убыток',
        summary: 'Два года подряд фиксируется чистый убыток при снижении выручки.',
        evidence: [
          { type: 'RAW_FACT', label: 'Чистая прибыль', value: '−14,8 млн ₽', source: 'finReports[2025].netProfit' },
          { type: 'DERIVED_METRIC', label: 'Динамика выручки', value: '−19% год к году', source: 'finReports[2025].derived.revenueGrowth' },
        ],
      },
      {
        id: 'legal',
        title: 'Суды и арбитраж',
        state: 'attention',
        label: '4 дела ответчиком',
        summary: 'Сумма требований заметна относительно годовой выручки.',
        evidence: [
          { type: 'RAW_FACT', label: 'Требования к компании', value: '26,4 млн ₽ по 4 делам', source: 'arbitrationByStatus.defendant' },
          { type: 'DERIVED_METRIC', label: 'Доля требований в выручке', value: '18,2%', source: 'arbitrationCases.derived.claimsToRevenue' },
        ],
      },
      {
        id: 'enforcement',
        title: 'Исполнительные производства',
        state: 'attention',
        label: '7 открытых',
        summary: 'Часть взысканий связана с неисполненными судебными решениями.',
        evidence: [
          { type: 'RAW_FACT', label: 'Открытые взыскания', value: '7 производств на 8,7 млн ₽', source: 'executionProceedings.summary' },
        ],
      },
      {
        id: 'ownership',
        title: 'Владельцы и руководство',
        state: 'unknown',
        label: 'Частые изменения',
        summary: 'Генеральный директор менялся дважды за последние 12 месяцев.',
        evidence: [
          { type: 'RAW_FACT', label: 'История руководителей', value: '3 записи с августа 2025 года', source: 'foundersInfo.directorHistory' },
        ],
      },
      {
        id: 'reputation',
        title: 'Комплаенс и репутация',
        state: 'attention',
        label: 'Есть сигналы',
        summary: 'Источник указывает на недостоверность адреса и массового руководителя.',
        evidence: [
          { type: 'SOURCE_SIGNAL', label: 'Недостоверный адрес', value: 'Запись активна', source: 'reputationalRisks.invalidAddress' },
          { type: 'RAW_FACT', label: 'Связанные компании директора', value: 'Руководитель указан ещё в 12 юрлицах', source: 'relatedCompanies.byDirector' },
        ],
      },
      {
        id: 'procurement',
        title: 'Закупки и контракты',
        state: 'unknown',
        label: 'Мало данных',
        summary: 'В отчёте есть только один завершённый контракт.',
        evidence: [
          { type: 'RAW_FACT', label: 'Контракты', value: '1 контракт на 2,1 млн ₽', source: 'procurements.summary' },
        ],
      },
    ],
  },
];

const stateIcons = {
  attention: CircleAlert,
  clear: CircleCheck,
  unknown: CircleHelp,
};

const summaryIcons = {
  positive: ShieldCheck,
  review: TriangleAlert,
  intensive: ShieldAlert,
};

const typeLabels = {
  RAW_FACT: 'Исходный факт',
  DERIVED_METRIC: 'Расчётная метрика',
  SOURCE_SIGNAL: 'Сигнал источника',
};

export default function ReportPage() {
  const [scenarioIndex, setScenarioIndex] = useState(0);
  const [openCards, setOpenCards] = useState<Set<string>>(() => new Set(['finance']));
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const scenario = scenarios[scenarioIndex];
  const SummaryIcon = summaryIcons[scenario.summaryTheme];
  const attentionCount = scenario.cards.filter((card) => card.state === 'attention').length;

  function shuffleScenario() {
    setScenarioIndex((current) => {
      const step = 1 + Math.floor(Math.random() * (scenarios.length - 1));
      return (current + step) % scenarios.length;
    });
    setOpenCards(new Set(['finance']));
    setAnswer('');
  }

  function askQuestion(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim()) return;
    setAnswer(
      `В моковом отчёте по ${scenario.company} найдено ${attentionCount} ${attentionCount === 1 ? 'блок с фактом' : 'блока с фактами'}, требующими внимания. Раскройте карточки ниже, чтобы увидеть значения и JSON-пути источников.`,
    );
    setQuestion('');
  }

  return (
    <main className="report-shell">
      <header className="report-header">
        <div className="report-header-inner">
          <a className="brand brand-dark" href="/" aria-label="Альфа-Банк — поиск контрагентов">
            <span className="brand-mark brand-mark-red" aria-hidden="true">A</span>
            <span>
              <b>Альфа-Банк</b>
              <small>Проверка контрагентов</small>
            </span>
          </a>

          <nav className="report-actions" aria-label="Действия с отчётом">
            <a className="new-search-link" href="/">
              <Search aria-hidden="true" />
              Новый поиск
            </a>
            <ProductButton
              view="secondary"
              controlSize={48}
              onClick={shuffleScenario}
              leftAddons={<Shuffle aria-hidden="true" />}
            >
              Перемешать пример
            </ProductButton>
          </nav>
        </div>
      </header>

      <div className="report-layout">
        <aside className="report-sidebar" aria-label="Разделы отчёта">
          <a className="back-link" href="/">
            <ArrowLeft aria-hidden="true" />
            К поиску
          </a>
          <p className="sidebar-label">В отчёте</p>
          <nav>
            <a className="active" href="#summary"><ShieldCheck aria-hidden="true" />Сводка</a>
            <a href="#signals"><FileText aria-hidden="true" />Факты и сигналы</a>
            <a href="#sources"><Database aria-hidden="true" />Источники</a>
          </nav>
          <div className="sidebar-note">
            <span>Моковые данные</span>
            <p>Прототип не используется для принятия реальных решений.</p>
          </div>
        </aside>

        <div className="report-content">
          <section className="company-heading" aria-labelledby="company-name">
            <div>
              <span className="eyebrow">Отчёт о контрагенте</span>
              <h1 id="company-name">{scenario.company}</h1>
              <p>{scenario.legalName}</p>
            </div>
            <div className="company-status">
              <span><Check aria-hidden="true" />{scenario.status}</span>
              <small>Данные обновлены {scenario.updated}</small>
            </div>
          </section>

          <dl className="company-meta">
            <div><dt>ИНН</dt><dd>{scenario.inn}</dd></div>
            <div><dt>КПП</dt><dd>{scenario.kpp}</dd></div>
            <div><dt>Регион</dt><dd>{scenario.location}</dd></div>
            <div><dt>Фактов в контексте</dt><dd>{scenario.factsCount}</dd></div>
          </dl>

          <section
            id="summary"
            className={`summary-panel summary-${scenario.summaryTheme}`}
          >
            <div className="summary-top">
              <div className="summary-copy">
                <span className="summary-icon"><SummaryIcon aria-hidden="true" /></span>
                <div>
                  <span className="eyebrow">AI-сводка по доступным фактам</span>
                  <span className="summary-result-label">{scenario.summaryLabel}</span>
                  <h2>{scenario.headline}</h2>
                </div>
              </div>
              <div className="bank-risks" aria-label="Банковские оценки">
                <div>
                  <span>Риск-уровень банка</span>
                  <b className={`risk-level risk-${scenario.riskLevel.toLowerCase()}`}>
                    {scenario.riskLevel}
                  </b>
                </div>
                <div>
                  <span>ЗСК</span>
                  <b className={`zsk-level zsk-${scenario.zskRiskLevel.toLowerCase()}`}>
                    {scenario.zskRiskLevel}
                  </b>
                </div>
                <small>Независимые оценки — не объединяются</small>
              </div>
            </div>

            <div className="summary-facts" aria-label="Главные факты отчёта">
              {scenario.summaryItems.map((item, index) => (
                <article className="summary-fact" key={item.label}>
                  <span className="summary-fact-number" aria-hidden="true">0{index + 1}</span>
                  <div>
                    <small>{item.label}</small>
                    <strong>{item.value}</strong>
                    <p>{item.detail}</p>
                  </div>
                </article>
              ))}
            </div>

            <p className="summary-note">{scenario.note}</p>
          </section>

          <section id="signals" className="signals-section" aria-labelledby="signals-title">
            <div className="section-heading">
              <div>
                <span className="eyebrow">Детальный разбор</span>
                <h2 id="signals-title">Факты по направлениям</h2>
              </div>
              <p>Нажмите на карточку, чтобы увидеть доказательства и путь в исходном JSON.</p>
            </div>

            <div className="risk-grid">
              {scenario.cards.map((card) => {
                const StateIcon = stateIcons[card.state];
                return (
                  <details
                    className={`risk-card state-${card.state}`}
                    key={`${scenario.company}-${card.id}`}
                    open={openCards.has(card.id)}
                    onToggle={(event) => {
                      const isOpen = event.currentTarget.open;
                      setOpenCards((current) => {
                        const next = new Set(current);
                        if (isOpen) next.add(card.id);
                        else next.delete(card.id);
                        return next;
                      });
                    }}
                  >
                    <summary>
                      <span className="risk-card-icon"><StateIcon aria-hidden="true" /></span>
                      <span className="risk-card-copy">
                        <span className="risk-card-label">{card.label}</span>
                        <strong>{card.title}</strong>
                        <span>{card.summary}</span>
                      </span>
                      <ChevronDown className="risk-card-chevron" aria-hidden="true" />
                    </summary>
                    <div className="evidence-list" id={card.id === 'reputation' ? 'sources' : undefined}>
                      {card.evidence.map((evidence) => (
                        <div className="evidence-row" key={`${evidence.source}-${evidence.label}`}>
                          <div>
                            <span className={`evidence-type type-${evidence.type.toLowerCase()}`}>
                              {typeLabels[evidence.type]}
                            </span>
                            <b>{evidence.label}</b>
                          </div>
                          <p>{evidence.value}</p>
                          <code>{evidence.source}</code>
                        </div>
                      ))}
                    </div>
                  </details>
                );
              })}
            </div>
          </section>

          <section className="assistant-box" aria-labelledby="assistant-title">
            <div className="assistant-heading">
              <span><Building2 aria-hidden="true" /></span>
              <div>
                <small>Моковый AI-помощник</small>
                <h2 id="assistant-title">Задайте вопрос по отчёту</h2>
              </div>
            </div>
            <form onSubmit={askQuestion} className="assistant-form">
              <label className="sr-only" htmlFor="report-question">Вопрос по отчёту</label>
              <input
                id="report-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Например: почему стоит проверить ликвидность?"
              />
              <button type="submit" aria-label="Отправить вопрос"><Send aria-hidden="true" /></button>
            </form>
            {answer && <div className="mock-answer"><b>Пример ответа</b><p>{answer}</p></div>}
            <p className="assistant-disclaimer">Ответ демонстрационный: реальный LLM и внешние источники не подключены.</p>
          </section>
        </div>
      </div>
    </main>
  );
}
