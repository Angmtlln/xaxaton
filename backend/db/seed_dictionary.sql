-- Справочник кодов репутационных меток отчёта.
-- severity: 3 — стоп-фактор, 2 — требует уточнения, 1 — информационная.
-- is_hard_stop — «жёсткий факт», который обязан подниматься наверх
-- независимо от цвета светофора банка (гипотеза H3).

INSERT INTO core.risk_code_dictionary (code, chapter, title_ru, severity, is_hard_stop, block) VALUES
  ('liquidationStatus',       'reestrs',     'Процедура банкротства или ликвидации',                    3, true,  'reliability'),
  ('fnsBlocking',             'reestrs',     'Блокировка счетов по постановлению ФНС',                  3, true,  'reliability'),
  ('invalidAddress',          'reestrs',     'Фиктивный адрес регистрации',                             3, true,  'reliability'),
  ('invalidRegistrationData', 'reestrs',     'Недостоверные регистрационные данные',                    3, true,  'reliability'),
  ('invalidAuthpersonsData',  'manager',     'Номинальный руководитель или недостоверные данные о нём', 3, true,  'reliability'),
  ('dishonestProvider',       'reestrs',     'Реестр недобросовестных поставщиков',                     3, true,  'reliability'),
  ('disqualifiedAuthpersons', 'manager',     'Дисквалифицированный руководитель',                       3, true,  'reliability'),
  ('taxArrears',              'reestrs',     'Задолженность по налогам',                                2, false, 'reliability'),
  ('taxReporting',            'reestrs',     'Непредставление налоговой отчётности',                    2, false, 'reliability'),
  ('massAddress',             'reestrs',     'Массовый адрес регистрации',                              2, false, 'reliability'),
  ('massAuthpersons',         'manager',     'Массовый директор или учредитель',                        2, false, 'reliability'),
  ('executionProceedings',    'execproc',    'Действующие исполнительные производства',                 2, false, 'reliability'),
  ('arbitrationDefendant',    'arbitr',      'Арбитражные дела в роли ответчика',                       2, false, 'reliability'),
  ('аrbitrationDefendant',    'arbitr',      'Арбитражные дела в роли ответчика (вариант кода)',        2, false, 'reliability'),
  ('massOkved',               'okved',       'Большое количество кодов ОКВЭД',                          1, false, 'identity'),
  ('profit',                  'finance',     'Финансовый результат (прибыль или убыток)',               2, false, 'finance'),
  ('proceeds',                'finance',     'Выручка',                                                 1, false, 'finance'),
  ('currentAssets',           'finance',     'Оборотные активы',                                        2, false, 'finance'),
  ('uncurrentAssets',         'finance',     'Внеоборотные активы',                                     1, false, 'finance'),
  ('relatedCompanies',        'relatedComp', 'Связанные компании',                                      1, false, 'identity'),
  ('governmentContract',      'reestrs',     'Государственные контракты',                               1, false, 'experience'),
  ('licenses',                'license',     'Лицензии',                                                1, false, 'experience'),
  ('webSite',                 'site',        'Сайт компании',                                           1, false, 'identity'),
  ('branchesInfo',            'filials',     'Филиалы',                                                 1, false, 'identity')
ON CONFLICT (code) DO UPDATE
  SET chapter = EXCLUDED.chapter,
      title_ru = EXCLUDED.title_ru,
      severity = EXCLUDED.severity,
      is_hard_stop = EXCLUDED.is_hard_stop,
      block = EXCLUDED.block;
