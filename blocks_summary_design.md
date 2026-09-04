# Разбиение отчёта о контрагенте на блоки для summary

Документ описывает, как поделить данные `GetFullReportResponse` на смысловые блоки, чтобы по каждому генерировать короткое summary, и какие реальные атрибуты из снимка `contractors_audit.snapshot.json` попадают в каждый блок.

## 1. Принцип деления

Резать отчёт по разделам спецификации не оптимально, потому что спецификация сгруппирована по источникам данных, а не по вопросам пользователя. Деление строится по двум признакам одновременно.

1. Внутри блока данные приходят одним куском и имеют общую логику анализа.
2. Блок отвечает на один вопрос, который предприниматель реально себе задаёт.

## 2. Детальная схема из семи блоков

Подходит, если нужен подробный разбор и много места в интерфейсе.

| Блок | Что отвечает | Что входит |
|---|---|---|
| 1. Карточка компании | Кто это и работает ли сейчас | baseInfo, status, phones, kindsOfActivityInfo, taxSystem, branchesInfo |
| 2. Владельцы и связи | Кто управляет и через кого | foundersInfo, parentOrganizations, relatedCompanies |
| 3. Риск-флаги банка | Как компанию оценил банк | riskLevel, zskRiskLevel, reputationalRisks |
| 4. Суды | Насколько компания судится | arbitrationCases, arbitrationByStatus |
| 5. Долги и надзор | Платит ли по решениям и вправе ли работать | executionProceedings, inspections, licenses |
| 6. Финансы | В каком состоянии бизнес | finReports, coefficient |
| 7. Госзакупки | Есть ли подтверждённый опыт | procurements |

## 3. Рабочая схема из четырёх блоков

Рекомендуемый вариант. Семь блоков сжимаются без потери смысла, потому что часть из них отвечает на один и тот же вопрос пользователя.

### Блок 1. Кто это

Карточка компании вместе с владельцами и связями. Один смысловой вопрос, реально ли перед нами живая компания с понятными бенефициарами.

### Блок 2. Надёжность и правовые риски

Сюда уходят сразу три прежних блока, риск-флаги банка, суды и долги с проверками. Предприниматель не разделяет эти вещи в голове, его волнует одно, есть ли повод не связываться. Внутри блока порядок идёт от готовых метрик банка к их подтверждению фактами по судам и исполнительным производствам.

### Блок 3. Финансовое состояние

Остаётся отдельным обязательно. Это единственные данные с динамикой по годам и единственные, где нужны вычисления, а не пересказ.

### Блок 4. Опыт и позитивные сигналы

Госзакупки, действующие лицензии и позитивные репутационные факторы. Блок балансирует отчёт, где всё остальное про риски.

### Нижняя граница

Три блока тоже возможны, если убрать четвёртый и растащить его содержимое по первому и второму. Но тогда позитивная информация растворяется среди негативной и отчёт читается тревожнее, чем есть на самом деле.

Ниже трёх опускаться не стоит. При двух блоках финансы неизбежно склеиваются с юридическими рисками, а у них разная логика анализа. В одном случае считаем тренды и сравниваем годы, в другом перечисляем факты и оцениваем тяжесть. Модель на таком склеенном промпте начинает терять часть данных, обычно именно финансовые.

## 4. Раскладка реальных атрибутов снимка по блокам

В файле 100 записей. Каждая имеет служебную обёртку `_id.ogrn` и `_id.date.$date`, сам отчёт лежит внутри `report`. Поле `report.reportDate.$date` относится к метаданным снимка и в блоки не входит.

### Блок 1. Кто это

Идентификация и статус.

- `report.baseInfo.inn`, `ogrn`, `kpp`, `okpo`
- `report.baseInfo.shortName`, `fullName`, `address`, `email`, `website`, `companySize`
- `report.baseInfo.registrationInfo.registrationDate.$date`, `registrationInfo.yearsFromRegistration`
- `report.status.status`, `status.reasonName`, `status.date.$date`
- `report.phones[]` с полями `phoneCode`, `phoneNumber`
- `report.kindsOfActivityInfo.mainKindOfActivity` с `code` и `description`
- `report.kindsOfActivityInfo.otherKindsOfActivity[]` с `code` и `description`
- `report.taxSystem[]` с `fullName`, `shortName`
- `report.branchesInfo.branchesCount`, `branchesInfo.branches[]` с `name`, `address`

Владельцы и связи.

- `report.foundersInfo.shareCapital`
- `report.foundersInfo.authPerson` с `name`, `positionName`, `inn`, `positionDate.$date`
- `report.foundersInfo.cofounders[]` с `name`, `inn`, `amount`, `share`, `dateFrom.$date`, `active`
- `report.relatedCompanies[]` с `inn`, `ogrn`, `name`, `registrationDate.$date`, `authPersonName`, `authPersonPosition`
- `report.relatedCompanies[].parentOrganizations[]` с `inn`, `ogrn`, `fullName`, `parentDate.$date`

### Блок 2. Надёжность и правовые риски

Готовые оценки банка.

- `report.baseInfo.riskLevel`
- `report.zskRiskLevel`
- `report.reputationalRisks.negative[]` с `code`, `name`, `chapter`

Суды.

- `report.arbitrationCases[]` с `year`, `plaintiffCount`, `plaintiffAmount`, `defendantCount`, `defendantAmount`
- `report.arbitrationByStatus.commonCount`, `commonAmount`
- `report.arbitrationByStatus.plaintiffArbitration.plaintiffArbitrationFinished` с `pfCount`, `pfAmount`
- `report.arbitrationByStatus.plaintiffArbitration.plaintiffArbitrationAppealed` с `paCount`, `paAmount`
- `report.arbitrationByStatus.plaintiffArbitration.plaintiffArbitrationPending` с `ppCount`, `ppAmount`
- `report.arbitrationByStatus.defandantArbitration.defandantArbitrationFinished` с `dfCount`, `dfAmount`
- `report.arbitrationByStatus.defandantArbitration.defandantArbitrationAppealed` с `daCount`, `daAmount`
- `report.arbitrationByStatus.defandantArbitration.defandantArbitrationPending` с `dpCount`, `dpAmount`

Долги и надзор.

- `report.executionProceedings[]` с `active`, `number`, `date.$date`, `amount`
- `report.inspections[]` с `erpId`, `type`, `form`, `authorityName`, `startDate`, `endDate`, `inspectionStatus`

### Блок 3. Финансовое состояние

- `report.finReports[].common` с `year`, `proceeds`, `profit`
- `report.finReports[].assets.totalAssets`
- `report.finReports[].assets.currentAssets` с `total`, `stocks`, `receivables`, `bankroll`
- `report.finReports[].assets.uncurrentAssets` с `total`, `fixedAssets`
- `report.finReports[].liabilities.totalLiabilities`, `liabilities.capitals`
- `report.finReports[].liabilities.longTermDuties` с `total`, `others`
- `report.finReports[].liabilities.shortTermLiabilities` с `total`, `borrowedFunds`, `accountsPayable`
- `report.coefficient` с `year`, `sustainability`, `solvency`, `profitability`

### Блок 4. Опыт и позитивные сигналы

- `report.procurements[]` с `procurementsYear`, `federalLawCode`, `tenderWinnerCnt`, `contractSignedCnt`, `contractSignedAmt`
- `report.licenses[]` с `number`, `name`, `issuingAuthority`, `issueDate.$date`, `endDate.$date`, `status`
- `report.reputationalRisks.positive[]` с `code`, `name`, `chapter`

## 5. Расхождения снимка со спецификацией

Файл беднее PDF, поэтому парсер надо писать по факту, а не по спецификации.

| Поле в спецификации | Что в снимке |
|---|---|
| `baseInfo.staff` | отсутствует полностью |
| `foundersInfo.parentOrganizations` | отсутствует на верхнем уровне, есть только внутри `relatedCompanies[]` |
| `phones[].phoneType` | отсутствует |
| `procurements[].tenderAdmittedCnt` | отсутствует |
| `foundersInfo.cofounders[].isActive` | называется `active` |

## 6. Заполненность полей по 100 записям

Цифры напрямую влияют на дизайн summary.

| Атрибут | Заполнено |
|---|---|
| `baseInfo.riskLevel` | 100 |
| `zskRiskLevel` | 100 |
| `reputationalRisks.positive` | 100 |
| `kindsOfActivityInfo.otherKindsOfActivity` | 97 |
| `taxSystem` | 75 |
| `foundersInfo.cofounders` | 72 |
| `finReports` | 67 |
| `relatedCompanies` | 61 |
| `reputationalRisks.negative` | 58 |
| `executionProceedings` | 53 |
| `arbitrationCases` | 44 |
| `baseInfo.email` | 35 |
| `inspections` | 30 |
| `phones` | 29 |
| `coefficient` | 19 |
| `baseInfo.website` | 11 |
| `licenses` | 9 |
| `procurements` | 8 |
| `status.reasonName` | 3 |
| `branchesInfo.branches` | 2 |
| `baseInfo.staff` | 0 |

Распределение по риску, LOW 78, MEDIUM 15, HIGH 4, UNKNOWN 3. По ЗСК, GREEN 81, YELLOW 18, RED 1. Все 100 компаний имеют статус CURRENT. Финансовая отчётность за три года есть у 62 компаний, у 33 её нет совсем.

Вывод для продукта. Третий и четвёртый блоки у большинства контрагентов будут почти пустыми, поэтому агент обязан честно писать «данных нет». Иначе именно там он начнёт галлюцинировать.

## 7. Технические замечания по реализации

Формат данных. Это дамп MongoDB, поэтому большие числа приходят как `{"$numberLong": "..."}`, а даты как `{"$date": "..."}`, причём непоследовательно. Одно и то же поле бывает и обычным числом, и обёрткой. Перед подачей в модель это надо разворачивать, иначе суммы по судам и выручка будут читаться как строки и сравнение по годам сломается.

Финансовый блок при большом числе лет стоит дробить дополнительно, иначе он один съест больше контекста, чем остальные блоки вместе.

Пустые блоки надо явно помечать, а не выкидывать молча.

Шаблон summary внутри блока должен быть одинаковым. Например, одна фраза с фактами, одна с интерпретацией и метка сигнала из трёх значений, норма, внимание, риск. Тогда блоки можно сравнивать и складывать в общий вывод.

Общий вывод по контрагенту стоит делать отдельным шагом уже поверх готовых блочных summary, а не по сырому отчёту.
