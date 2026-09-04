import json
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).resolve().parents[2] / "contractors_audit.snapshot.json"


@pytest.fixture(scope="session")
def documents():
    if not SNAPSHOT.exists():
        pytest.skip("Выгрузка %s не найдена" % SNAPSHOT)
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def document(documents):
    return documents[0]
