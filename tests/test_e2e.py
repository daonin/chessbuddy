import os
import time

import pytest
from fastapi.testclient import TestClient

from cbuddy.api import app


@pytest.fixture()
def client():
    return TestClient(app)


def _sample_pgn():
    return (
        """
[Event "Casual Game"]
[Site "?"]
[Date "2024.12.31"]
[Round "?"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
"""
    ).strip()


def test_import_and_fetch_game(client):
    r = client.post("/import/pgn", json={"pgn": _sample_pgn(), "external_source": "manual"})
    assert r.status_code == 200
    gid = r.json()["game_id"]
    r = client.get(f"/games/{gid}")
    assert r.status_code == 200
    data = r.json()
    assert data["game"]["id"] == gid
    assert len(data["moves"]) >= 1


@pytest.mark.skipif(not os.getenv("ENGINE_PATH"), reason="Stockfish not configured")
def test_full_flow_with_engine(client):
    # Import game
    r = client.post("/import/pgn", json={"pgn": _sample_pgn(), "external_source": "manual"})
    assert r.status_code == 200
    gid = r.json()["game_id"]

    # Analyse
    r = client.post(f"/analyse/{gid}")
    assert r.status_code == 200

    # List highlights for game
    r = client.get(f"/games/{gid}/highlights")
    assert r.status_code == 200
    highlights = r.json()["items"]

    # Create task from first highlight if exists and verify move
    if highlights:
        hid = highlights[0]["highlight_id"]
        r = client.post(f"/highlights/{hid}/create_task", json={"user_id": 1})
        assert r.status_code == 200
        task_id = r.json()["task_id"]

        # Try a naive move (may be wrong); endpoint should still return structured result
        r = client.post(f"/tasks/{task_id}/verify", json={"move_uci": "e2e4", "user_id": 1, "response_ms": 1000})
        assert r.status_code == 200
        res = r.json()
        assert "is_correct" in res and "engine_best_move_uci" in res
