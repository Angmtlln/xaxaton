"""Export compact review plus a compressed complete evidence archive for git."""
import argparse
import gzip
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from .bank import SUITES


def export(run, destination):
    out, dest = Path(run), Path(destination)
    dest.mkdir(parents=True, exist_ok=True)
    summary = json.loads((out / "latest.json").read_text())
    if not summary.get("finished_at"):
        raise ValueError("Cannot publish an incomplete run as complete")
    bank = json.loads((out / "scenarios.json").read_text())
    regraded = json.loads((out / "regraded.json").read_text()) if (out / "regraded.json").exists() else None
    checks = {r["case_id"]: r["checks"] for r in (regraded or summary)["rows"]}
    semantic_path = out / "semantic-full.json"
    semantic = json.loads(semantic_path.read_text()) if semantic_path.exists() else {"results": [], "counts": {}}
    judgments = {r["case_id"]: r for r in semantic["results"]}
    manual_path = dest / "manual_review.json"
    manual = json.loads(manual_path.read_text()) if manual_path.exists() else {"results": []}
    reviewed = {r["case_id"]: r for r in manual["results"]}
    by_id = {r["case_id"]: r for r in summary["rows"]}
    for r in manual["results"]:
        for finding in r.get("findings", []):
            if finding["quote"] not in by_id[r["case_id"]]["message"]:
                raise ValueError("Manual review quote does not exist: " + r["case_id"])
    archive = dest / "traces.jsonl.gz"
    with gzip.open(archive, "wt", encoding="utf-8", compresslevel=9) as f:
        for row in summary["rows"]:
            with gzip.open(out / row["trace"], "rt") as source:
                trace = json.load(source)
            trace["regraded_checks"] = checks[row["case_id"]]
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")
    for name in ("scenarios.json", "latest.json", "regraded.json", "semantic-full.json"):
        if (out / name).exists():
            with gzip.open(dest / (name + ".gz"), "wt", encoding="utf-8", compresslevel=9) as f:
                f.write((out / name).read_text())
    raw_judge = list(out.glob("judge-full-*.json"))
    if raw_judge:
        with gzip.open(dest / "judge-outputs.jsonl.gz", "wt", encoding="utf-8") as f:
            for path in sorted(raw_judge):
                f.write(json.dumps(json.loads(path.read_text()), ensure_ascii=False) + "\n")
    table = []
    for suite in SUITES:
        ids = {t["id"] for s in bank["sessions"] for t in s["turns"] if suite in t["suites"]}
        rows = [r for r in summary["rows"] if r["scored"] and r["case_id"] in ids]
        failed = sum(any(c["status"] == "FAIL" for c in checks[r["case_id"]]) for r in rows)
        sem = Counter(judgments.get(r["case_id"], {"status": "NOT_REVIEWED"})["status"] for r in rows)
        table.append({"suite": suite, "planned": len(ids), "completed": len(rows), "technical_pass": len(rows) - failed, "technical_fail": failed, "semantic": dict(sem)})
    scored = [r for r in summary["rows"] if r["scored"]]
    latencies = sorted(r["wall_ms"] for r in scored)
    errors = Counter((r.get("metadata") or {}).get("error_code") for r in summary["rows"] if (r.get("metadata") or {}).get("error_code"))
    failures = Counter(c["name"] for r in scored for c in checks[r["case_id"]] if c["status"] == "FAIL")
    manifest = {k: v for k, v in summary.items() if k != "rows"}
    manifest.update(suites=table, technical_failure_checks=dict(failures), explicit_runtime_errors=dict(errors),
                    manual_review_counts=dict(Counter(r["status"] for r in manual["results"])),
                    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                    latency_p50_ms=latencies[len(latencies)//2], latency_p95_ms=latencies[int((len(latencies)-1)*.95)],
                    judgment_authority="Provisional automatic judge; manual source review is a nonrandom sample")
    (dest / "summary.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Первый полный behavioral eval ALEPH", "", "**Поведенческая приёмка не пройдена. Semantic failures не исправлялись.**", "",
             f"Выполнены все {len(scored)} scored-реплик `full`, включая все шесть suites; ещё {len(summary['rows']) - len(scored)} setup-реплик.",
             "Suites пересекаются: таблица ниже — срезы одного полного прогона, не шесть независимых повторов.",
             f"Runtime commit `{summary['git_commit']}`; Master `{summary['master_model']}`; `LLM_MOCK=false`; `grounding_debug={summary['grounding_debug']}`.",
             f"Период: {summary['started_at']} — {summary['finished_at']}; concurrency={summary['concurrency']}.",
             "Данные: неизменный реальный snapshot вместо чтения БД, persist=False. Это live LLM/runtime eval, не HTTP/UI smoke.",
             "", "| Suite | Реплик | Technical PASS | Technical FAIL |", "|---|---:|---:|---:|"]
    for row in table:
        lines.append(f"| {row['suite']} | {row['completed']}/{row['planned']} | {row['technical_pass']} | {row['technical_fail']} |")
    lines += ["", "## Технические результаты", "", f"Явные runtime errors (включая setup): `{dict(errors)}`.",
              f"Счётчик failed checks scored-реплик: `{dict(failures)}`; одна реплика может провалить несколько проверок.",
              f"Latency p50={manifest['latency_p50_ms']} ms, p95={manifest['latency_p95_ms']} ms. Быстрые отказы входят в статистику; это не benchmark успешного synthesis.",
              "Ограничение 60 s — eval SLO. Отдельные NA-проверки не превращены в успешную проверку значений.",
              "`result_too_large` на насыщенных comparison-фикстурах срывает начальный tool и последующие follow-ups. Компании после обнаружения сбоя не заменялись более удобными.",
              "", "## Содержательная оценка", "", f"Автоматический judge: `{semantic.get('counts', {})}`.",
              f"Judge model: `{semantic.get('judge_model', 'not run')}`, same_model_as_subject={semantic.get('same_model_as_subject', 'n/a')}.",
              "Автоматический PASS предварительный: judge может пропускать нарушения. Его доля PASS не является уровнем качества продукта.",
              f"Отдельная ручная сверка Codex по trace и snapshot: `{manifest['manual_review_counts']}`. Выборка целевая, не случайная; нельзя экстраполировать её долю FAIL на весь банк.",
              "", "| Case | Review | Основание |", "|---|---|---|"]
    for item in manual["results"]:
        lines.append(f"| {item['case_id']} | {item['status']} | {item['explanation'].replace('|', '/')} |")
    lines += ["", "Точные цитаты, классы §19 и пути проверки — в [manual_review.json](manual_review.json).",
              "", "## Границы и воспроизведение", "",
              "До полного прогона выполнен пилот killer: 25/25 реплик, 6 технических FAIL сравнения. В нём unknown execution имел только неизвестную активную сумму; основной банк усилен до смеси известных и неизвестных сумм. Пилот сохранён отдельно, не подменяет основной результат.",
              "K24: UnknownResult есть в source, но первая страница trusted inspections может его не содержать. Проверка страницы/конкретной записи требует отдельного follow-up; существование fixture не означает, что запись была показана Master.",
              "История finance в этой выгрузке — максимум три года. Выбор фикстур не доказывает репрезентативность базы за её пределами.",
              "Фикстуры, формулировки и ожидаемые классы неизменны в копии банка каждого прогона. Технические проверки доработаны и пересчитаны на тех же сохранённых ответах (`regraded.json.gz`); LLM-ответы не перезаписывались.",
              "SQL/schema/repository, runtime/prompts, legacy endpoints и UI не изменены. DB impact отсутствует, миграции не нужны.",
              "", "```bash", "cd backend", "PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_eval_harness.py",
              "PYTHONPATH=. .venv/bin/python -m evals.run_local --suite full --concurrency 6 --output evals/results/replay",
              "PYTHONPATH=. .venv/bin/python -m evals.judge --run evals/results/replay --suite full",
              "PYTHONPATH=. .venv/bin/python -m evals.regrade --run evals/results/replay", "```", "",
              "Команды требуют настроенных live ключей и вызывают провайдеров. Инструкция всех suites: [evals README](../../../backend/evals/README.md).",
              "Вручную: проверить перечисленные semantic failures и независимым reviewer оценить выборку автоматических PASS; UI/HTTP в этой задаче не проверялись.",
              "", "Архив [traces.jsonl.gz](traces.jsonl.gz) содержит все исходные ответы, ToolResults, аргументы, state до/после и проверки. Одна строка JSON — одна реплика. Сжатые bank/latest/regraded/semantic JSON и judge outputs сохранены рядом."]
    (dest / "report.md").write_text("\n".join(lines) + "\n")
    # Readable complete response ledger, so review does not require decompressing traces.
    ledger = []
    for r in scored:
        verdict = judgments.get(r["case_id"], {"status": "NOT_REVIEWED"})
        ledger += [f"## {r['case_id']}", "", r["question"], "", r.get("message") or "Ответ отсутствует", "",
                   f"Technical failed: {sorted({c['name'] for c in checks[r['case_id']] if c['status'] == 'FAIL'})}; automatic semantic: {verdict['status']}; manual: {reviewed.get(r['case_id'], {}).get('status', 'NOT_REVIEWED')}.", ""]
    (dest / "answers.md").write_text("\n".join(ledger))
    print(json.dumps(table, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", required=True)
    p.add_argument("--destination", required=True)
    a = p.parse_args(); export(a.run, a.destination)
