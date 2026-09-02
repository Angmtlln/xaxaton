"""Заполняет шаблон Jupyter Notebook воспроизводимым EDA."""

from pathlib import Path

import nbformat as nbf


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "output/jupyter-notebook/contractor-risk-eda.ipynb"


def markdown(text: str):
    return nbf.v4.new_markdown_cell(text.strip())


def code(text: str):
    return nbf.v4.new_code_cell(text.strip())


cells = [
    markdown(
        """
# EDA: связь факторов риска с банковскими risk-level

## tl;dr

Анализ охватывает **200 разных контрагентов**: 100 из JSON и 100 из flattened CSV.

- `baseInfo.riskLevel` и `zskRiskLevel` связаны слабо: ordinal Spearman `ρ = 0.165`, quadratic weighted kappa `= 0.253`. Это не два названия одного и того же светофора.
- **Суды не повышают банковские risk-level в этой выборке.** При наличии raw-данных о делах ответчиком доля `MEDIUM/HIGH` равна примерно 12% против 19% без таких дел; для `YELLOW/RED` — 16% против 25%. Обратное направление не является «защитным эффектом»: оно статистически неустойчиво и смешано с возрастом/масштабом компании.
- **Активные исполнительные производства также не показывают устойчивой связи:** для `baseInfo` 19% против 15%, для ZSK 18% против 22%; точные тесты незначимы.
- Самая воспроизводимая связь с `baseInfo.riskLevel` — готовый negative signal `massAddress`: `MEDIUM/HIGH` у 43.5% компаний с сигналом против 12.3% без него, OR `= 5.49`, Fisher `p = 0.0007`. Направление повторяется и в JSON, и в CSV.
- Negative signals домена **ФНС / реестры** в целом связаны с `baseInfo.riskLevel` (36.6% против 10.5%), но почти не связаны с ZSK (23.8% против 20.3%). Это может быть частью алгоритма `baseInfo.riskLevel`, то есть потенциальной target leakage.
- Для ZSK заметна только предварительная гипотеза о возрасте: более молодые компании чаще имеют `YELLOW/RED` (`ρ = -0.202`, `p = 0.004`), но результат не проходит FDR 5% после проверки набора признаков.
- После FDR-коррекции ни один **сырой** судебный, исполнительный или финансовый признак не сохраняет уверенную связь с обоими target-полями.

Вывод: банковские risk-level и юридико-финансовые факторы отчёта отражают разные слои риска. Для продукта корректнее показывать их рядом, а не пытаться восстановить банковский светофор по данным отчёта.
"""
    ),
    markdown(
        """
## Context & Methods

Цель — проверить статистические связи между доступными признаками отчёта и двумя полями риска. Это **наблюдательный EDA**, поэтому далее используется слово «связь», а не причинное «влияние».

### Key Assumptions

1. Одна запись — один контрагент.
2. `UNKNOWN` в `baseInfo.riskLevel` исключается из ordinal- и binary-тестов: неизвестное значение не равно высокому риску.
3. Из-за редких классов (`HIGH=5`, `RED=2`) основные устойчивые endpoints бинарные:
   - base elevated: `MEDIUM/HIGH` против `LOW`;
   - ZSK elevated: `YELLOW/RED` против `GREEN`.
4. Slot-колонки CSV сначала собираются обратно на уровень компании. Индексы `[0]`, `[1]` не анализируются как самостоятельные признаки.
5. Готовые `reputationalRisks` отделены от raw facts: они могут уже участвовать в расчёте одного из target-полей.
6. Для финансов берётся последнее **доступное для конкретной метрики** значение и сохраняется его год.
7. Spearman `ρ` измеряет монотонную связь от `-1` до `1`. Fisher exact сравнивает доли для бинарных признаков. OR выше 1 означает большую долю elevated target при наличии признака. FDR/q-value снижает риск случайных находок при множественных тестах.
"""
    ),
    markdown("## Data\n\n### 1. Setup and reproducibility"),
    code(
        """
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import statsmodels.formula.api as smf
from IPython.display import display
from scipy.stats import spearmanr
from statsmodels.stats.contingency_tables import StratifiedTable

project_root = next(
    candidate
    for candidate in [Path.cwd(), *Path.cwd().parents]
    if (candidate / "analysis/contractor_risk_eda.py").exists()
)
sys.path.insert(0, str(project_root))

from analysis.contractor_risk_eda import (
    binary_associations,
    build_feature_table,
    dataset_quality_summary,
    label_relationship,
    negative_domain_columns,
    numeric_associations,
    raw_binary_columns,
    raw_numeric_columns,
    signal_columns,
    top_replicated_associations,
)

JSON_PATH = Path("/Users/exoldoff/Downloads/contractors_audit.snapshot.json")
CSV_PATH = Path("/Users/exoldoff/Downloads/contractors_audit.snapshot_C12613591.csv")

assert JSON_PATH.exists(), JSON_PATH
assert CSV_PATH.exists(), CSV_PATH

pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 160)
pd.options.display.float_format = "{:,.3f}".format
sns.set_theme(style="whitegrid", font_scale=0.95)
"""
    ),
    markdown("### 2. Load and normalize both sources"),
    code(
        """
features = build_feature_table(JSON_PATH, CSV_PATH)
quality = dataset_quality_summary(features)

print(f"Компаний: {len(features)}")
print(f"Пересечение по ИНН между JSON и CSV: "
      f"{len(set(features.loc[features.dataset.eq('JSON'), 'inn']) & set(features.loc[features.dataset.eq('CSV'), 'inn']))}")
display(quality.round(1))
"""
    ),
    markdown(
        """
Оба источника имеют одинаковый grain и не пересекаются по ИНН. Финансовые блоки разрежены: коэффициенты доступны лишь у 19% JSON и 28% CSV, прибыль — у 33% и 39%. Поэтому финансовые сравнения используют меньшую подвыборку и не равны анализу всех 200 компаний.
"""
    ),
    markdown("### 3. Target distribution"),
    code(
        """
base_distribution = pd.crosstab(features["dataset"], features["base_risk"])
zsk_distribution = pd.crosstab(features["dataset"], features["zsk_risk"])
display(base_distribution)
display(zsk_distribution)

fig, axes = plt.subplots(1, 2, figsize=(11, 4))
sns.countplot(data=features, x="base_risk", order=["LOW", "MEDIUM", "HIGH", "UNKNOWN"], ax=axes[0], color="#6D5DFB")
sns.countplot(data=features, x="zsk_risk", order=["GREEN", "YELLOW", "RED"], ax=axes[1], color="#F15B4A")
axes[0].set(title="baseInfo.riskLevel: сильный дисбаланс", xlabel="", ylabel="Число компаний")
axes[1].set(title="zskRiskLevel: RED встречается дважды", xlabel="", ylabel="Число компаний")
plt.tight_layout()
plt.show()
"""
    ),
    markdown(
        """
Редкие `HIGH` и `RED` нельзя анализировать как самостоятельные устойчивые классы. Любая «закономерность» по двум RED-компаниям будет зависеть от отдельных строк.
"""
    ),
    markdown("## Results\n\n### 4. Связь двух risk-level между собой"),
    code(
        """
relationship = label_relationship(features)
display(relationship["contingency"])
mapped_matches = (
    ((features.base_risk == "LOW") & (features.zsk_risk == "GREEN"))
    | ((features.base_risk == "MEDIUM") & (features.zsk_risk == "YELLOW"))
    | ((features.base_risk == "HIGH") & (features.zsk_risk == "RED"))
)
known_base = features.base_score.notna()

pd.DataFrame({
    "metric": ["N с известными targets", "Spearman rho", "Spearman p", "Quadratic weighted kappa", "Прямое соответствие классов, %"],
    "value": [relationship["n"], relationship["spearman_rho"], relationship["spearman_p"], relationship["weighted_kappa"], 100 * mapped_matches[known_base].mean()],
})
"""
    ),
    markdown(
        """
Связь положительная, но слабая. В частности, 31 компания имеет `LOW + YELLOW`, а 18 — `MEDIUM + GREEN`. Полный Cramér's V здесь не выносится в headline: таблица содержит очень редкие ячейки, и две пары `HIGH + RED` искусственно усиливают категориальную метрику.
"""
    ),
    markdown("### 5. Raw risk domains against elevated targets"),
    code(
        """
raw_binary = raw_binary_columns(features)
raw_base_binary = binary_associations(features, "base_elevated", raw_binary)
raw_zsk_binary = binary_associations(features, "zsk_elevated", raw_binary)

columns = ["feature_ru", "n", "support", "elevated_rate_with", "elevated_rate_without", "delta_pp", "risk_ratio", "p_value", "q_value"]
print("baseInfo elevated: лучшие raw binary связи")
display(raw_base_binary[columns].head(12).round(3))
print("ZSK elevated: лучшие raw binary связи")
display(raw_zsk_binary[columns].head(12).round(3))
"""
    ),
    markdown(
        """
Ни один raw binary-признак не проходит FDR 5%. Некоторые признаки показывают отрицательную связь (например, проверки или лицензии), но трактовать их как защитные нельзя: такие факты чаще встречаются у более зрелых и крупных компаний.
"""
    ),
    markdown("### 6. Суды и исполнительные производства — прямой spot check"),
    code(
        """
legal_features = [
    "has_arbitration_defendant",
    "has_arbitration_defendant_yearly",
    "has_arbitration_defendant_status",
    "has_pending_defendant_cases",
    "has_active_execution",
]

legal_tables = []
for target, target_name in [("base_elevated", "base MEDIUM/HIGH"), ("zsk_elevated", "ZSK YELLOW/RED")]:
    table = binary_associations(features, target, legal_features)
    table.insert(0, "target_name", target_name)
    legal_tables.append(table)
legal = pd.concat(legal_tables, ignore_index=True)
display(legal[["target_name", "feature_ru", "n", "support", "elevated_rate_with", "elevated_rate_without", "delta_pp", "odds_ratio", "p_value", "q_value"]].round(3))
"""
    ),
    markdown(
        """
Три судебных определения показаны отдельно:

- `yearly` — суммы `defendantCount` в `arbitrationCases`;
- `status` — finished + pending + appealed из `arbitrationByStatus`;
- общий флаг — наличие хотя бы в одном raw-блоке.

Во всех вариантах ожидаемой положительной связи с risk-level нет. При этом `arbitrationByStatus` находит больше компаний, чем готовый signal, поэтому бизнес-правило signal нельзя восстанавливать простым `count > 0` по всем судебным полям.
"""
    ),
    markdown("### 7. Existing signals and domain-level associations"),
    code(
        """
domain_features = negative_domain_columns(features)
domain_base = binary_associations(features, "base_elevated", domain_features)
domain_zsk = binary_associations(features, "zsk_elevated", domain_features)
domain_base.insert(0, "target", "base MEDIUM/HIGH")
domain_zsk.insert(0, "target", "ZSK YELLOW/RED")
domain_results = pd.concat([domain_base, domain_zsk], ignore_index=True)
display(domain_results[["target", "feature_ru", "support", "elevated_rate_with", "elevated_rate_without", "delta_pp", "risk_ratio", "p_value", "q_value"]].round(3))

plot_data = domain_results.copy()
plot_data["target"] = plot_data["target"].replace({"base MEDIUM/HIGH": "base", "ZSK YELLOW/RED": "ZSK"})
fig, ax = plt.subplots(figsize=(9, 5))
sns.barplot(data=plot_data, y="feature_ru", x="delta_pp", hue="target", ax=ax)
ax.axvline(0, color="black", linewidth=0.8)
ax.set(title="Изменение доли elevated target при наличии negative signal", xlabel="Разница, процентные пункты", ylabel="")
plt.tight_layout()
plt.show()
"""
    ),
    markdown(
        """
Главное различие targets видно на домене реестров: он сильно связан с `baseInfo.riskLevel`, но не с ZSK. Это согласуется с тем, что два поля оценивают разные стороны риска.
"""
    ),
    markdown("### 8. `massAddress`: проверка повторяемости на двух источниках"),
    code(
        """
mass_address = "neg_signal__massaddress"
mass_rows = []
strata = []
for dataset, subset in features.groupby("dataset"):
    result = binary_associations(subset, "base_elevated", [mass_address], min_support=5)
    row = result.iloc[0].to_dict()
    row["dataset"] = dataset
    mass_rows.append(row)

    exact = subset[[mass_address, "base_elevated"]].dropna().astype(int)
    table = pd.crosstab(exact[mass_address], exact["base_elevated"]).reindex(index=[1, 0], columns=[1, 0], fill_value=0)
    strata.append(table.to_numpy())

mass_by_source = pd.DataFrame(mass_rows)
display(mass_by_source[["dataset", "n", "support", "elevated_rate_with", "elevated_rate_without", "odds_ratio", "p_value"]].round(4))

mh = StratifiedTable(strata)
pd.DataFrame({
    "metric": ["Mantel-Haenszel OR", "95% CI low", "95% CI high", "p-value", "heterogeneity p"],
    "value": [mh.oddsratio_pooled, *mh.oddsratio_pooled_confint(), mh.test_null_odds().pvalue, mh.test_equal_odds().pvalue],
}).round(4)
"""
    ),
    markdown(
        """
Связь `massAddress` с base target повторяется в обоих файлах и почти одинакова по силе. Но signal уже рассчитан поставщиком данных и может входить в формулу `baseInfo.riskLevel`. Поэтому это прежде всего **проверка согласованности слоёв**, а не новый независимый предиктор.
"""
    ),
    markdown("### 9. Numeric features and financial scale"),
    code(
        """
numeric_base = numeric_associations(features, "base_score", raw_numeric_columns(features))
numeric_zsk = numeric_associations(features, "zsk_score", raw_numeric_columns(features))

numeric_columns = ["feature_ru", "n", "rho", "p_value", "q_value"]
print("baseInfo.riskLevel")
display(numeric_base[numeric_columns].head(12).round(3))
print("zskRiskLevel")
display(numeric_zsk[numeric_columns].head(12).round(3))

top_numeric = pd.concat([
    numeric_base.assign(target="base").head(8),
    numeric_zsk.assign(target="ZSK").head(8),
])
fig, ax = plt.subplots(figsize=(9, 6))
sns.barplot(data=top_numeric, y="feature_ru", x="rho", hue="target", ax=ax)
ax.axvline(0, color="black", linewidth=0.8)
ax.set(title="Сильнейшие описательные Spearman-связи", xlabel="Spearman rho", ylabel="")
plt.tight_layout()
plt.show()
"""
    ),
    markdown(
        """
После FDR ни один raw numeric-признак не достигает `q < 0.05`. Отрицательные коэффициенты у активов, выручки и возраста скорее отражают связь повышенных уровней с меньшими/молодыми компаниями, а не самостоятельный эффект каждой суммы.
"""
    ),
    markdown("### 10. Возраст компании и возможный confounding"),
    code(
        """
age_summary = features.groupby("zsk_risk")["company_age_years"].agg(["count", "median", "mean"])
display(age_summary.reindex(["GREEN", "YELLOW", "RED"]).round(2))

age_by_source = []
for dataset, subset in features.groupby("dataset"):
    rho, p = spearmanr(subset["company_age_years"], subset["zsk_score"], nan_policy="omit")
    age_by_source.append({"dataset": dataset, "rho": rho, "p_value": p})
display(pd.DataFrame(age_by_source).round(4))

age_bins = pd.cut(features["company_age_years"], bins=[-0.1, 0, 3, 7, np.inf], labels=["0", "1-3", "4-7", "8+"])
age_rates = features.assign(age_group=age_bins).groupby("age_group", observed=True)["zsk_elevated"].agg(["count", "mean"])
age_rates["elevated_pct"] = 100 * age_rates["mean"]
display(age_rates[["count", "elevated_pct"]].round(1))

confounding = features.groupby("has_inspections")["company_age_years"].agg(["count", "median", "mean"])
print("Возраст компаний без/с проверками:")
display(confounding.round(2))
"""
    ),
    markdown(
        """
Возраст показывает одинаковое отрицательное направление в обоих источниках, но после FDR остаётся гипотезой. Проверки, суды, лицензии и исполнительные производства чаще встречаются у старых компаний; поэтому их «обратная» связь с ZSK частично объясняется смешением факторов.
"""
    ),
    markdown("### 11. Signal consistency and missing-data risks"),
    code(
        """
consistency = pd.DataFrame([
    {
        "comparison": "active execution raw vs negative signal",
        "raw_positive": int(features["has_active_execution"].sum()),
        "signal_positive": int(features["neg_signal__executionproceedings"].sum()),
        "agreement_pct": 100 * (features["has_active_execution"] == features["neg_signal__executionproceedings"]).mean(),
    },
    {
        "comparison": "arbitration yearly raw vs negative signal",
        "raw_positive": int(features["has_arbitration_defendant_yearly"].sum()),
        "signal_positive": int(features["neg_signal__arbitrationdefendant"].sum()),
        "agreement_pct": 100 * (features["has_arbitration_defendant_yearly"] == features["neg_signal__arbitrationdefendant"]).mean(),
    },
    {
        "comparison": "arbitration status raw vs negative signal",
        "raw_positive": int(features["has_arbitration_defendant_status"].sum()),
        "signal_positive": int(features["neg_signal__arbitrationdefendant"].sum()),
        "agreement_pct": 100 * (features["has_arbitration_defendant_status"] == features["neg_signal__arbitrationdefendant"]).mean(),
    },
])
display(consistency.round(1))

raw_csv = pd.read_csv(CSV_PATH, dtype=str)
csv_missingness = raw_csv.isna().mean()
report_dates = pd.to_datetime(features["report_date"], errors="coerce", utc=True)

quality_facts = pd.DataFrame({
    "check": [
        "Средняя доля пропусков в flattened CSV",
        "CSV-колонки с >=95% пропусков",
        "CSV-колонки с >=80% пропусков",
        "Минимальная дата отчёта",
        "Максимальная дата отчёта",
    ],
    "value": [
        f"{100 * csv_missingness.mean():.1f}%",
        int((csv_missingness >= 0.95).sum()),
        int((csv_missingness >= 0.80).sum()),
        str(report_dates.min().date()),
        str(report_dates.max().date()),
    ],
})
display(quality_facts)

heavy_tail = features.groupby("dataset")[["execution_total_count", "inspection_count", "related_companies_count", "other_okved_count"]].quantile([0.5, 0.9, 0.99, 1.0]).unstack(0)
display(heavy_tail.round(1))
"""
    ),
    markdown(
        """
Flat CSV разрежен конструктивно: массивы разложены на тысячи slot-колонок. Прямой перебор всех 2654 полей дал бы множество ложных находок и артефактов порядка элементов.

Отдельно важна неоднозначность missing/zero. Для `arbitrationCases` yearly-флаг полностью совпадает с готовым signal, но `arbitrationByStatus` находит дополнительные raw cases. Следовательно, разные блоки имеют разные правила/охват, и «пусто» нельзя безоговорочно считать доказанным отсутствием риска.
"""
    ),
    markdown(
        """
## Takeaways

1. **Не использовать `baseInfo.riskLevel` и `zskRiskLevel` как взаимозаменяемые labels.** Их связь слабая, а бизнес-определения пока неизвестны.
2. **Не обучать scoring на 2654 CSV-колонках.** Сначала нормализовать до компании и заранее определить 20–30 осмысленных агрегатов.
3. **Суды и исполнительные производства показывать отдельным AI-слоем**, даже если банковский risk-level зелёный/низкий: отсутствие корреляции здесь и есть продуктовая ценность дополнительного анализа.
4. **Реестровые negative signals связаны с `baseInfo.riskLevel`**, особенно `massAddress`, но это потенциальная target leakage. Нужно спросить кейсодателя, участвуют ли signals в расчёте поля.
5. **ZSK почти не объясняется юридическими и репутационными данными отчёта.** Предварительная связь с молодым возрастом требует подтверждения на большей выборке.
6. **Для deterministic analytics хранить availability/evidence рядом с каждой метрикой:** source path, год, raw value и правило агрегации.
7. Для будущих evals анализировать не «угадал ли агент светофор», а корректно ли он нашёл raw facts, выделил domain risks, сослался на evidence и честно обработал missing data.

### Что нужно уточнить у кейсодателя

- Какое поле является банковским светофором и как определяется второе?
- Входит ли `reputationalRisks` в расчёт `baseInfo.riskLevel`?
- Чем объясняется различие `arbitrationCases`, `arbitrationByStatus` и готового arbitration signal?
- Означает ли отсутствие блока «факт отсутствует» или «данные не получены»?
"""
    ),
]

notebook = nbf.read(NOTEBOOK, as_version=4)
notebook.cells = cells
notebook.metadata.kernelspec = {
    "display_name": "Python 3",
    "language": "python",
    "name": "python3",
}
notebook.metadata.language_info = {"name": "python", "version": "3.13"}
nbf.write(notebook, NOTEBOOK)
print(f"Updated {NOTEBOOK}")
