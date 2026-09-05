"""Compile the user's source verbatim and pin independently verified fixtures."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "AGENT_EVALS.md"
SNAPSHOT = ROOT / "contractors_audit.snapshot.json"
BANK = Path(__file__).with_name("scenarios.json")
SUITES = ("killer", "solo", "comparison", "traps", "multiturn", "full")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(x):
    if isinstance(x, list):
        return [normalize(v) for v in x]
    if isinstance(x, dict):
        if len(x) == 1:
            k, v = next(iter(x.items()))
            if k in ("$numberLong", "$numberInt"):
                return int(v)
            if k in ("$numberDouble", "$numberDecimal"):
                return float(v)
            if k in ("$date", "$oid"):
                return v
        return {k: normalize(v) for k, v in x.items()}
    return x


def documents():
    return {d["report"]["baseInfo"]["inn"]: d for d in normalize(json.loads(SNAPSHOT.read_text()))}


def at(value, path):
    for key in path:
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and isinstance(key, int) and key < len(value):
            value = value[key]
        else:
            return {"present": False}
    return {"present": True, "value": value}


def candidates(doc):
    """Source predicates, independent of the product's calculators and conclusions."""
    r = doc["report"]
    found = {}
    def add(name, why, *paths):
        found[name] = {"requirement": name, "selection_reason": why,
                       "evidence": [{"path": ["report", *p], **at(r, p)} for p in paths]}
    fs = r.get("finReports") or []
    latest = max(range(len(fs)), key=lambda i: fs[i]["common"]["year"]) if fs else None
    for i, e in enumerate(r.get("executionProceedings") or []):
        if e.get("active") is True:
            if e.get("amount") is None and any(x.get("active") is True and x.get("amount") is not None for x in r["executionProceedings"]):
                add("unknown_execution", "Среди активных ИП одновременно есть известные и неизвестные суммы", ["executionProceedings"])
            elif e.get("amount") is not None and abs(float(e["amount"]) - 24.63) < .005:
                add("small_execution", "active=true, amount=24.63 RUB", ["executionProceedings", i])
    cases = r.get("arbitrationCases") or []
    total = sum(float(c.get("defendantAmount") or 0) for c in cases)
    if total >= 100_000_000:
        add("large_courts", f"Сумма годовых defendantAmount={total} RUB >= 100 млн; не установленный долг", ["arbitrationCases"])
    p = ["arbitrationByStatus", "defandantArbitration", "defandantArbitrationPending", "dpCount"]
    if at(r, p).get("value", 0) > 0:
        add("pending", "Непустой агрегат pending; годовые агрегаты не имеют общего case id", p, ["arbitrationCases"])
    if r.get("status", {}).get("status") == "CURRENT" and r["status"].get("reasonName"):
        add("current_reason", "CURRENT не отменяет существенный reasonName", ["status"])
    if not fs:
        add("no_finance", "Нет финансовых строк", ["finReports"])
    else:
        f = fs[latest]
        if f["common"].get("profit") is None:
            add("missing_profit", "В последнем году profit отсутствует/null", ["finReports", latest, "common", "year"], ["finReports", latest, "common", "profit"])
        cap = f.get("liabilities", {}).get("capitals")
        assets = f.get("assets", {}).get("totalAssets")
        if cap is not None and assets and cap / assets < .05:
            add("weak_capital", "Капитал/активы < 5%; критерий отбора фикстуры, не risk score", ["finReports", latest, "liabilities", "capitals"], ["finReports", latest, "assets", "totalAssets"])
        if len({f["common"]["year"] for f in fs}) >= 3 and sum(f["common"].get("profit") is not None for f in fs) >= 2:
            add("rich_finance", "Не менее трёх отчётных лет, минимум два раскрывают прибыль", ["finReports"])
        reg = r["baseInfo"].get("registrationInfo", {}).get("registrationDate")
        for i, f in enumerate(fs):
            if reg and f["common"]["year"] < int(reg[:4]) and f["common"].get("proceeds") == 0:
                add("pre_registration", "Нулевая выручка в году до регистрации", ["baseInfo", "registrationInfo", "registrationDate"], ["finReports", i])
    for polarity in ("negative", "positive"):
        for i, sig in enumerate(r.get("reputationalRisks", {}).get(polarity) or []):
            if sig.get("code") == "fnsBlocking" and polarity == "negative":
                add("fns_blocking", "Негативный fnsBlocking; reason/duration/scope не раскрыты отдельными полями", ["reputationalRisks", polarity, i])
            if sig.get("code") == "massOkved" and polarity == "negative":
                add("mass_okved", "Негативный source code massOkved", ["reputationalRisks", polarity, i], ["kindsOfActivityInfo"])
            if sig.get("code") == "proceeds" and polarity == "positive" and fs and fs[latest]["common"].get("proceeds") == 0:
                add("source_conflict", "Позитивное narrative о стабильном доходе при нулевой последней выручке; это комментарий источника", ["reputationalRisks", polarity, i], ["finReports", latest, "common"])
    for i, inspection in enumerate(r.get("inspections") or []):
        if inspection.get("inspectionStatus") == "InspectionsUnknownResult":
            add("unknown_inspection", "UnknownResult не означает violation", ["inspections", i])
    if r.get("licenses"):
        add("licenses", "Непустой список лицензий", ["licenses"])
    for i, p in enumerate(r.get("procurements") or []):
        if (p.get("contractSignedCnt") or 0) > 0:
            add("procurements", "contractSignedCnt > 0; исполнение не раскрыто", ["procurements", i])
    add("bank_" + r["baseInfo"].get("riskLevel", "MISSING") + "_" + r.get("zskRiskLevel", "MISSING"),
        "Независимая пара банковских индикаторов", ["baseInfo", "riskLevel"], ["zskRiskLevel"])
    return found


PREFERRED = {"small_execution": "7813664770", "source_conflict": "7813664770",
             "unknown_execution": "7728380537", "pending": "6165169320",
             "missing_profit": "6165169320", "weak_capital": "6165169320",
             "current_reason": "5032257375", "pre_registration": "1684017097",
             "fns_blocking": "6165169320", "no_finance": "5029069967"}


def fixtures(docs):
    matches = {}
    for inn, d in docs.items():
        for name, evidence in candidates(d).items():
            matches.setdefault(name, []).append({"inn": inn, "name": d["report"]["baseInfo"].get("shortName"), **evidence})
    required = set(PREFERRED) | {"large_courts", "rich_finance", "unknown_inspection", "licenses", "procurements", "mass_okved"}
    if required - matches.keys():
        raise ValueError(f"Unmet fixture requirements: {required - matches.keys()}")
    return {name: next((v for v in values if v["inn"] == PREFERRED.get(name)), values[0])
            for name, values in sorted(matches.items())}


def compile_bank():
    source = SOURCE.read_text()
    docs = documents()
    fx = fixtures(docs)
    aliases = {"A": "6165169320", "B": "3711039473", "C": "7813664770"}
    sections = {int(m[1]): m[2] for m in re.finditer(r"^# (\d+)\. ([\s\S]*?)(?=^# \d+\.|\Z)", source, re.M)}
    sessions = []
    def render(q, a):
        for alias, inn in a.items():
            q = q.replace(f"<{alias}>", inn)
        return q
    def turn(cid, q, expectation, suites):
        if cid.startswith("S"):
            section = int(cid.split("_")[0][1:])
            start = re.search(rf"^# {section}\.", source, re.M).start()
        elif cid.startswith(("K", "M")):
            start = source.index("## " + cid.split(".")[0] + " —")
        else:
            start = source.index("## K01 —")
        line = source[:source.index(q, start)].count("\n") + 1
        return {"id": cid, "template": q, "source_line": line, "expectation": expectation, "suites": [*suites, "full"]}
    def session(sid, turns, fixture=None, comparison=False, setup=True):
        a = {**aliases, **({"A": fx[fixture]["inn"]} if fixture else {})}
        for t in turns:
            t["question"] = render(t["template"], a)
        setup_q = f"Сравни {a['A']}, {a['B']}, {a['C']}" if comparison else f"Проверь {a['A']}"
        sessions.append({"id": sid, "aliases": a, "fixture": fixture,
                         "mode": "comparison" if comparison else "solo",
                         "setup": [setup_q] if setup else [], "turns": turns})
    ks = {}
    traps = {7, 9, 11, 12, 18, 21, 22, 23, 24, 25}
    for m in re.finditer(r"^## (K\d+) — ([\s\S]*?)(?=^## K|\Z)", sections[1], re.M):
        cid, block = m[1], m[2]
        q = re.search(r"\*\*Вопрос:\*\* `([^`]+)`", block)[1]
        expectation = re.search(r"\*\*Ожидание:\*\* ([^\n]+)", block)[1]
        n = int(cid[1:])
        ks[n] = turn(cid, q, expectation, ["killer"] + (["traps"] if n in traps else []))
    session("K-main", [ks[i] for i in [*range(1, 10), 13, 14]], setup=False)
    session("K-comparison", [ks[i] for i in range(15, 21)], comparison=True, setup=False)
    special = {10: "small_execution", 11: "unknown_execution", 12: "fns_blocking", 21: "source_conflict", 22: "missing_profit", 23: "pre_registration", 24: "unknown_inspection", 25: "procurements"}
    for i, fixture in special.items():
        session(f"K{i:02}", [ks[i]], fixture)
        if i == 24:
            evidence = fx[fixture]["evidence"][0]
            index = evidence["path"][-1]
            inn = fx[fixture]["inn"]
            sessions[-1]["setup"] = [f"Юридические данные компании {inn}: надзорные проверки, страница {index // 5 + 1}. Дальше обсудим запись номер {index % 5 + 1} на этой странице."]
            sessions[-1]["setup_tools"] = ["get_legal_data"]
    # Sections are coherent, ordered conversations; special factual premises get their own fixture.
    question_fixtures = {
        "Есть ИП на 24 рубля. Мне вообще есть дело до него?": "small_execution",
        "Активное ИП без суммы — это значит долг нулевой?": "unknown_execution",
        "Есть активные ИП, по которым сумма неизвестна?": "unknown_execution",
        "У компании LOW risk, но слабые финансы. Как это совместить?": "weak_capital",
        "Почему source считает компанию позитивной по финансам, если сами цифры спорные?": "source_conflict",
    }
    for section in range(2, 16):
        group = "solo" if section < 10 else "comparison" if section < 15 else "traps"
        turns = []
        for i, q in enumerate(re.findall(r"^- `([^`]+)`", sections[section], re.M), 1):
            t = turn(f"S{section:02}_{i:02}", q, sections[section].splitlines()[0] + "; применить §0, §19 и §20 source of truth", [group])
            if q in question_fixtures:
                session(t["id"], [t], question_fixtures[q])
            else:
                turns.append(t)
        if section == 15:
            # Singular traps need an active company; plural traps need a comparison.
            plural = {1, 10, 11, 12, 13, 21}
            for t in turns:
                i = int(t["id"].split("_")[1])
                session(t["id"], [t], comparison=i in plural)
        else:
            session(f"S{section:02}", turns, fixture={5: "unknown_execution", 8: "licenses"}.get(section), comparison=section >= 10, setup=section not in (2, 10, 14))
    for m in re.finditer(r"^## (M\d+) — ([\s\S]*?)(?=^## M|\Z)", sections[16], re.M):
        cid, block = m[1], m[2]
        questions = re.search(r"```text\n([\s\S]*?)```", block)[1].strip().splitlines()
        expectation = re.search(r"Проверять: ([^\n]+)", block)[1]
        ts = [turn(f"{cid}.{i}", q.removeprefix("→ "), expectation, ["multiturn"]) for i, q in enumerate(questions, 1)]
        fixture = {"M06": "source_conflict", "M08": "pending", "M09": "unknown_execution", "M10": "weak_capital"}.get(cid)
        session(cid, ts, fixture, comparison=cid in ("M03", "M07"), setup=cid == "M06")
    # Explicit coverage probes reuse original questions, with difficult additional fixtures.
    for name in fx:
        session("F-" + name, [turn("F-" + name, "Проверь <A>.", "Проверить §17: " + fx[name]["selection_reason"], ["solo"])], name, setup=False)
    return {"version": 1, "source": "AGENT_EVALS.md", "source_sha256": sha(SOURCE),
            "snapshot_sha256": sha(SNAPSHOT), "aliases": aliases, "fixtures": fx,
            "rubric_classes": re.findall(r"^- `([^`]+)`", sections[19], re.M),
            "sessions": sessions}


def validate_bank(bank):
    if bank != compile_bank():
        raise ValueError("Scenario bank drift: regenerate and review source/fixture changes")


def select(bank, suite):
    """Include conversation prerequisites, marked separately from scored targets."""
    selected = []
    for session in bank["sessions"]:
        targets = [i for i, t in enumerate(session["turns"]) if suite in t["suites"]]
        if targets:
            turns = [{**t, "scored": suite in t["suites"]} for t in session["turns"][:max(targets) + 1]]
            selected.append({**session, "turns": turns})
    return selected


if __name__ == "__main__":
    bank = compile_bank()
    BANK.write_text(json.dumps(bank, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({s: sum(t["scored"] for c in select(bank, s) for t in c["turns"]) for s in SUITES}))
