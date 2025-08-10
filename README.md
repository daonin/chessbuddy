# ChessBuddy

A service to store and analyze chess games, extract notable moves (blunders, mistakes, brilliants, etc.), and generate tactics tasks.

## Changes (DB user)
- Compose spins up Postgres and creates a dedicated app user `chessbuddy_app` via `docker/initdb/01_app_user.sql`.
- API connects as `chessbuddy_app` (no superuser), schema `chessbuddy` owned by this user.
- DB port 5432 is not exposed by default (uncomment in compose for local DB access).

## Features
- Import games (PGN or chess.com monthly archives)
- Store games, moves, engine evaluations, and highlights
- Analyze games with Stockfish (fast + deep passes)
- Generate tactics tasks from blunders and verify user answers
- FastAPI HTTP API with Swagger docs
- Alembic migrations for PostgreSQL
- Dockerized deployment (API + Postgres + Bot)

## Tech
- Python 3.11, FastAPI, SQLAlchemy Core
- PostgreSQL, Alembic
- python-chess (UCI engine integration)
- Stockfish (UCI engine)

## Quickstart (Docker)
```bash
docker compose up --build
# API: http://localhost:8000 ; Swagger: /docs
```
- For local DB access, uncomment the `ports: 5432:5432` lines under `db`.
- API connects with: `postgresql+psycopg://chessbuddy_app:chessbuddy_password@db:5432/chessbuddy`.

## Quickstart (local)
1) Prereqs: Postgres, Stockfish (optional)
2) venv + deps
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
3) Env & migrate
```bash
createdb chessbuddy || true
alembic upgrade head
```
4) Run API
```bash
uvicorn cbuddy.api:app --reload
```

## Docker
Build and run API and Postgres:
```bash
docker compose up --build
# API: http://localhost:8000
# Swagger: http://localhost:8000/docs
```
- API image installs Stockfish at `/usr/bin/stockfish`.
- Env vars in docker-compose:
  - `DATABASE_URL` (defaults to `postgresql+psycopg://postgres:postgres@db:5432/chessbuddy`)
  - `CORS_ALLOW_ORIGINS` (default `*`)
  - `ENGINE_PATH` (default `/usr/bin/stockfish`)

## Env variables
- `DATABASE_URL`: SQLAlchemy URL to Postgres (e.g. `postgresql+psycopg:///chessbuddy`)
- `ENGINE_PATH`: path to Stockfish binary (e.g. `/opt/homebrew/bin/stockfish` on macOS, `/usr/bin/stockfish` in Docker)
- `ENGINE_THREADS`, `ENGINE_HASH_MB`, `ENGINE_FAST_MOVETIME_MS`, `ENGINE_DEEP_MOVETIME_MS`, `ENGINE_DEEP_MULTIPV`
- Thresholds: `THRESH_BRILLIANT_CP`, `THRESH_GREAT_CP`, `THRESH_INACCURACY_CP`, `THRESH_MISTAKE_CP`, `THRESH_BLUNDER_CP`, `THRESH_NEAR_BEST_TOL_CP`
- CORS: `CORS_ALLOW_ORIGINS`
- Rate limit: `RATE_LIMIT_PER_MIN` (default 120)

## API (high level)
- POST `/import/pgn` { pgn, external_source?, external_game_id?, url? }
- POST `/import/chesscom/{username}/{year}/{month}`
- GET  `/categories`
- GET  `/games` params: start_time, end_time, username, last_id, limit
- GET  `/games/{game_id}`
- DELETE `/games/{game_id}`
- POST `/games/{game_id}/reanalyse` (query: clear_tasks=false)
- GET  `/games/{game_id}/highlights`
- GET  `/highlights` params: category, username, game_id, start_time, end_time, last_id, limit
- GET  `/highlights/random` params: category, username
- POST `/highlights/{highlight_id}/create_task` body: { user_id }
- POST `/tasks/{task_id}/verify` body: { move_uci, user_id, response_ms }
- GET  `/tasks` params: user_id, status, last_id, limit

See `/docs` for full schemas and examples.

## Analysis pipeline
- Fast pass: quick eval every ply; store `engine_evaluations`
- Highlights: classify by eval delta (blunder, mistake, ...); store `move_highlights`
- Deep pass: refine candidates (MultiPV > 1)
- Tactics: create tasks from blunders at position `ply - 1`

## Running tests
Tests create a temporary test database, run Alembic migrations, and use FastAPI TestClient.
```bash
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```
Notes:
- Tests that require Stockfish will be skipped if `ENGINE_PATH` is not found.

## Why Stockfish inside the container?
- Reproducible deployments, simple ops (no external dependency), resource isolation via container limits. See discussion in the PR/notes.

## Next
- Auth (JWT), multi-user separation
- Import Lichess
- Optional queue/worker separation if analysis volume grows
