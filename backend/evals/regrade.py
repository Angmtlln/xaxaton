"""Regrade immutable saved responses, without another subject/model invocation."""
import argparse
import gzip
import json
from pathlib import Path

from .bank import documents, sha, SOURCE, SNAPSHOT
from .graders import grade


def run(path):
    out = Path(path)
    summary = json.loads((out / "latest.json").read_text())
    bank = json.loads((out / "scenarios.json").read_text())
    if sha(SOURCE) != bank["source_sha256"] or sha(SNAPSHOT) != bank["snapshot_sha256"]:
        raise ValueError("Source/snapshot drift")
    sessions = {s["id"]: s for s in bank["sessions"]}
    for session in sessions.values():
        if session.get("fixture"):
            session["fixture_contract"] = bank["fixtures"][session["fixture"]]
    docs = documents()
    rows = []
    for compact in summary["rows"]:
        with gzip.open(out / compact["trace"], "rt") as f:
            row = json.load(f)
        checks = grade(row, sessions[row["session"]], docs, summary["latency_threshold_ms"])
        rows.append({"case_id": row["case_id"], "scored": row["scored"], "checks": checks})
    result = {"authority": "deterministic_regrade_same_saved_responses", "grader_sha256": sha(Path(__file__).with_name("graders.py")), "rows": rows}
    (out / "regraded.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print({"rows": len(rows), "scored_fail": sum(r["scored"] and any(c["status"] == "FAIL" for c in r["checks"]) for r in rows)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    run(parser.parse_args().run)
