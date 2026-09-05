'use client';
/* oxlint-disable next/no-html-link-for-pages */

import { ChevronDown, Search, SlidersHorizontal } from 'lucide-react';
import { useState } from 'react';

import { ProductButton } from '@/components/product-button';

export default function Home() {
  const [query, setQuery] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);

  return (
    <main className="landing-shell">
      <header className="landing-header" aria-label="Шапка сервиса">
        <a className="brand" href="/" aria-label="Альфа-Банк — на главную">
          <span className="brand-mark" aria-hidden="true">A</span>
          <span>
            <b>Альфа-Банк</b>
            <small>Бизнес</small>
          </span>
        </a>
        <span className="prototype-pill">AI-прототип</span>
      </header>

      <section className="search-stage" aria-labelledby="landing-title">
        <div className="landing-kicker">Проверка контрагентов</div>
        <h1 id="landing-title">Узнайте, с кем ведёте бизнес</h1>
        <p>
          Факты о компании, банковские оценки и объяснение каждого сигнала —
          в одном отчёте.
        </p>

        <form action="/report" className="search-form" aria-label="Поиск контрагента">
          <div className="hero-search">
            <label className="sr-only" htmlFor="company-search">
              ИНН или название юридического лица
            </label>
            <Search aria-hidden="true" className="hero-search-icon" />
            <input
              id="company-search"
              name="q"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="hero-search-input"
              placeholder="ИНН или название юридического лица"
              autoComplete="off"
            />
            <ProductButton
              className="hero-search-submit"
              type="submit"
              view="primary"
              controlSize={56}
              aria-label="Найти компанию"
            >
              Найти →
            </ProductButton>
          </div>

          {advancedOpen && (
            <div className="advanced-panel" aria-label="Критерии расширенного поиска">
              <label>
                <span>Регион</span>
                <select name="region" defaultValue="all">
                  <option value="all">Все регионы</option>
                  <option value="77">Москва</option>
                  <option value="78">Санкт-Петербург</option>
                  <option value="66">Свердловская область</option>
                </select>
              </label>
              <label>
                <span>Основной ОКВЭД</span>
                <input name="okved" placeholder="Например, 62.01" />
              </label>
              <label>
                <span>Статус компании</span>
                <select name="status" defaultValue="active">
                  <option value="active">Действующая</option>
                  <option value="all">Любой статус</option>
                </select>
              </label>
              <label>
                <span>Выручка от, млн ₽</span>
                <input name="revenue" inputMode="numeric" placeholder="0" />
              </label>
            </div>
          )}
        </form>

        <button
          className="advanced-link"
          type="button"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen((current) => !current)}
        >
          <SlidersHorizontal aria-hidden="true" />
          Расширенный поиск
          <ChevronDown className="advanced-chevron" aria-hidden="true" />
        </button>
      </section>

      <footer className="landing-footer">
        <span>Только данные из отчёта</span>
        <span>•</span>
        <span>Каждый вывод с источником</span>
      </footer>
    </main>
  );
}
