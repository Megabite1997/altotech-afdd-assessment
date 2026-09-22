from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = Path(os.getenv("SOURCE_DIR_TEST", REPO_ROOT / "source-pack"))

T0 = datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def source_dir() -> Path:
    assert SOURCE_DIR.exists(), f"source pack missing at {SOURCE_DIR}"
    return SOURCE_DIR


def minutes(n: int) -> timedelta:
    return timedelta(minutes=n)
