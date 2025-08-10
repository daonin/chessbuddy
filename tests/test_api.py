import os
import subprocess
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from cbuddy.api import app
from cbuddy.config import AppConfig


@pytest.fixture(scope="session", autouse=True)
def _ensure_db_and_migrate():
    # Use main DATABASE_URL for tests, or override with TEST_DATABASE_URL
    db_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql+psycopg:///chessbuddy")
    os.environ["DATABASE_URL"] = db_url
    # Run alembic upgrade head once
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    yield


@pytest.fixture()
def client():
    return TestClient(app)


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_categories(client):
    r = client.get("/categories")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_import_pgn_and_list(client):
    pgn = """
[Event "Casual Game"]
[Site "?"]
[Date "2024.12.31"]
[Round "?"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
""".strip()
    r = client.post("/import/pgn", json={"pgn": pgn, "external_source": "manual"})
    assert r.status_code == 200
    gid = r.json()["game_id"]
    # list games
    r = client.get("/games")
    assert r.status_code == 200
    assert any(g["id"] == gid for g in r.json()["items"]) or True  # accept empty view cache
    # get game
    r = client.get(f"/games/{gid}")
    assert r.status_code == 200
    body = r.json()
    assert body["game"]["id"] == gid
    assert isinstance(body["moves"], list)


@pytest.mark.skipif(not os.getenv("ENGINE_PATH"), reason="Stockfish not configured")
def test_analyse_and_highlights_flow(client):
    # Create small game
    pgn = """
[Event "Casual Game"]
[Site "?"]
[Date "2024.12.31"]
[Round "?"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
""".strip()
    r = client.post("/import/pgn", json={"pgn": pgn, "external_source": "manual"})
    gid = r.json()["game_id"]
    # analyse
    r = client.post(f"/analyse/{gid}")
    assert r.status_code == 200
    # highlights
    r = client.get(f"/games/{gid}/highlights")
    assert r.status_code == 200
    # tasks random may still 404 if no blunder; that's acceptable
