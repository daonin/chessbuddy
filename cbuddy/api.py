from __future__ import annotations
import os
import time
import random
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, ConfigDict

from .db import get_connection, fetch_all, fetch_one, execute
from .importer import import_pgn, import_chesscom_month
from .engine_worker import analyse_game_pipeline, verify_task_answer
from .config import AppConfig


class _LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        dur_ms = (time.perf_counter() - start) * 1000
        print(f"{request.method} {request.url.path} -> {response.status_code} {dur_ms:.1f}ms")
        return response


class _RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_per_minute: int = 120):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self._hits: dict[tuple[str, str], list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        key = (request.client.host if request.client else "unknown", request.url.path)
        now = time.time()
        window_start = now - 60
        arr = self._hits.setdefault(key, [])
        i = 0
        while i < len(arr) and arr[i] < window_start:
            i += 1
        if i:
            del arr[:i]
        if len(arr) >= self.max_per_minute:
            raise HTTPException(429, "rate limit exceeded")
        arr.append(now)
        return await call_next(request)


app = FastAPI(title="ChessBuddy API")

# CORS
_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "*")
if _origins_env.strip() == "*":
    allow_origins = ["*"]
else:
    allow_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# simple in-proc rate limit (can tune via env RATE_LIMIT_PER_MIN, default 120)
app.add_middleware(_RateLimitMiddleware, max_per_minute=int(os.getenv("RATE_LIMIT_PER_MIN", "120")))
app.add_middleware(_LoggingMiddleware)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/engine/health")
def engine_health():
    import chess
    import chess.engine
    cfg = AppConfig()
    path = cfg.engine.engine_path
    try:
        eng = chess.engine.SimpleEngine.popen_uci(path)
        board = chess.Board()
        eng.analyse(board, chess.engine.Limit(time=0.05))
        eng.quit()
        return {"ok": True, "engine_path": path}
    except Exception as e:  # noqa
        return {"ok": False, "engine_path": path, "error": str(e)}


@app.post("/analyse/pending")
def analyse_pending(user_id: Optional[int] = Query(None), limit: int = Query(5, ge=1, le=100)):
    clauses = ["not exists (select 1 from chessbuddy.engine_evaluations e where e.game_id = g.id)"]
    params = {"lim": limit}
    if user_id is not None:
        clauses.append(
            "exists (select 1 from chessbuddy.external_accounts ea where ea.user_id = :uid and ((ea.provider = wp.provider and ea.external_username = wp.username) or (ea.provider = bp.provider and ea.external_username = bp.username)))"
        )
        params["uid"] = user_id
    sql = f"""
        select g.id
        from chessbuddy.games g
        join chessbuddy.players wp on wp.id = g.white_player_id
        join chessbuddy.players bp on bp.id = g.black_player_id
        where {' and '.join(clauses)}
        order by coalesce(g.played_at, g.imported_at) desc, g.id desc
        limit :lim
    """
    ids: list[int] = []
    with get_connection() as conn:
        rows = fetch_all(conn, sql, **params)
        ids = [int(r["id"]) for r in rows]
    processed = 0
    errors: list[dict] = []
    for gid in ids:
        try:
            analyse_game_pipeline(gid)
            processed += 1
        except Exception as e:  # noqa
            print(f"analyse error game_id={gid}: {e}")
            errors.append({"game_id": gid, "error": str(e)})
    return {"selected": len(ids), "processed": processed, "errors": errors}


@app.get("/users/by_external")
def user_by_external(provider: str, external_user_id: Optional[str] = None, external_username: Optional[str] = None):
    if not external_user_id and not external_username:
        raise HTTPException(400, "external_user_id or external_username required")
    with get_connection() as conn:
        where = ["provider=:prov"]
        params = {"prov": provider}
        if external_user_id:
            where.append("external_user_id = :eid")
            params["eid"] = external_user_id
        if external_username:
            where.append("external_username = :euname")
            params["euname"] = external_username
        row = fetch_one(conn, f"select user_id from chessbuddy.external_accounts where {' and '.join(where)}", **params)
        if not row:
            raise HTTPException(404, "not found")
        return {"user_id": row["user_id"]}


@app.get("/status")
def status(username: Optional[str] = Query(None), user_id: Optional[int] = Query(None)):
    with get_connection() as conn:
        # total games for filter (username or user_id)
        if user_id is not None:
            total_games = fetch_one(conn, """
                select count(*) as c
                from chessbuddy.games g
                join chessbuddy.players wp on wp.id = g.white_player_id
                join chessbuddy.players bp on bp.id = g.black_player_id
                where exists (
                  select 1 from chessbuddy.external_accounts ea
                  where ea.user_id = :uid
                    and ((ea.provider = wp.provider and ea.external_username = wp.username)
                      or (ea.provider = bp.provider and ea.external_username = bp.username))
                )
            """, uid=user_id)["c"]
            analysed_games = fetch_one(conn, """
                select count(distinct h.game_id) as c
                from chessbuddy.move_highlights h
                join chessbuddy.games g on g.id = h.game_id
                join chessbuddy.players wp on wp.id = g.white_player_id
                join chessbuddy.players bp on bp.id = g.black_player_id
                where exists (
                  select 1 from chessbuddy.external_accounts ea
                  where ea.user_id = :uid
                    and ((ea.provider = wp.provider and ea.external_username = wp.username)
                      or (ea.provider = bp.provider and ea.external_username = bp.username))
                )
            """, uid=user_id)["c"]
            total_highlights = fetch_one(conn, """
                select count(*) as c
                from chessbuddy.move_highlights h
                join chessbuddy.games g on g.id = h.game_id
                join chessbuddy.players wp on wp.id = g.white_player_id
                join chessbuddy.players bp on bp.id = g.black_player_id
                where exists (
                  select 1 from chessbuddy.external_accounts ea
                  where ea.user_id = :uid
                    and ((ea.provider = wp.provider and ea.external_username = wp.username)
                      or (ea.provider = bp.provider and ea.external_username = bp.username))
                )
            """, uid=user_id)["c"]
        else:
            params = {}
            uname_clause = ""
            if username:
                uname_clause = " where white_username = :uname or black_username = :uname"
                params["uname"] = username
            total_games = fetch_one(conn, f"select count(*) as c from chessbuddy.v_game_meta{uname_clause}", **params)["c"]
            analysed_games = fetch_one(
                conn,
                f"""
                select count(distinct game_id) as c
                from chessbuddy.v_move_highlights_feed
                {('where category_key is not null and (white_username = :uname or black_username = :uname)') if username else ''}
                """,
                **({"uname": username} if username else {}),
            )["c"]
            total_highlights = fetch_one(
                conn,
                f"select count(*) as c from chessbuddy.v_move_highlights_feed{uname_clause}",
                **params,
            )["c"]
        tasks_stats = {}
        if user_id is not None:
            tasks_stats = fetch_one(
                conn,
                """
                with t as (
                  select status, count(*) as c from chessbuddy.tactics_tasks where user_id=:uid group by status
                )
                select
                  coalesce((select c from t where status='new'),0) as new,
                  coalesce((select c from t where status='answered'),0) as answered,
                  coalesce((select count(*) from chessbuddy.tactics_tasks where user_id=:uid),0) as total
                """,
                uid=user_id,
            )
        # last import job (prefer username context)
        job = None
        if username is not None:
            job = fetch_one(conn, """
                select * from chessbuddy.import_jobs
                where username=:uname
                order by started_at desc, id desc
                limit 1
            """, uname=username)
    progress = None
    if total_games:
        progress = round((analysed_games / total_games) * 100)
    return {
        "username": username,
        "total_games": total_games,
        "analysed_games": analysed_games,
        "total_highlights": total_highlights,
        "progress_percent": progress,
        "tasks": tasks_stats,
        "last_import_job": job,
    }


class ImportPGNRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "pgn": "[Event \"Casual Game\"]\n[Site \"?\"]\n[Date \"2024.12.31\"]\n[Round \"?\"]\n[White \"White\"]\n[Black \"Black\"]\n[Result \"1-0\"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0\n",
            "external_source": "manual",
            "external_game_id": None,
            "url": None
        }
    })
    pgn: str
    external_source: Optional[str] = None
    external_game_id: Optional[str] = None
    url: Optional[str] = None


@app.post("/import/pgn")
def import_pgn_endpoint(body: ImportPGNRequest):
    try:
        res = import_pgn(
            body.pgn,
            external_source=body.external_source,
            external_game_id=body.external_game_id,
            url=body.url,
        )
    except Exception as e:  # noqa
        raise HTTPException(400, {"code": "PGN_PARSE_ERROR", "message": str(e)})
    return {"game_id": res.game_id, "created": res.created}


class EnsureExternalUserRequest(BaseModel):
    provider: str
    external_user_id: Optional[str] = None
    external_username: Optional[str] = None
    display_name: Optional[str] = None


@app.post("/users/ensure_external")
def ensure_external_user(req: EnsureExternalUserRequest):
    if not req.external_user_id and not req.external_username:
        raise HTTPException(400, "external_user_id or external_username required")
    with get_connection() as conn:
        where = ["provider=:prov"]
        params = {"prov": req.provider}
        if req.external_user_id:
            where.append("external_user_id = :eid")
            params["eid"] = req.external_user_id
        if req.external_username:
            where.append("external_username = :euname")
            params["euname"] = req.external_username
        row = fetch_one(conn, f"select user_id from chessbuddy.external_accounts where {' and '.join(where)}", **params)
        if row:
            return {"user_id": row["user_id"]}
        uname = req.external_username or f"{req.provider}:{req.external_user_id}"
        user = fetch_one(conn, """
            insert into chessbuddy.users (username, display_name)
            values (:uname, :dname)
            returning id
        """, uname=uname, dname=req.display_name or uname)
        ext_username = req.external_username or uname
        execute(conn, """
            insert into chessbuddy.external_accounts (user_id, provider, external_username, external_user_id)
            values (:uid, :prov, :euname, :eid)
        """, uid=user["id"], prov=req.provider, euname=ext_username, eid=req.external_user_id)
        return {"user_id": user["id"]}


class ImportChesscomJobRequest(BaseModel):
    username: str
    months: int = 3
    initiated_by_user_id: int


@app.post("/import/chesscom/job")
def import_chesscom_job(req: ImportChesscomJobRequest):
    from datetime import date
    today = date.today()
    ym = []
    y, m = today.year, today.month
    for _ in range(req.months):
        ym.append((y, m))
        m -= 1
        if m == 0:
            y -= 1
            m = 12
    stats = {"imported": 0, "skipped": 0}
    with get_connection() as conn:
        # ensure internal user exists
        u = fetch_one(conn, "select id from chessbuddy.users where id=:id", id=req.initiated_by_user_id)
        if not u:
            raise HTTPException(400, "initiated_by_user_id does not exist; resolve external -> internal first")
        # auto-link chess.com account to user for downstream filters (no-op if exists)
        execute(conn, """
            insert into chessbuddy.external_accounts (user_id, provider, external_username)
            values (:uid, 'chess.com', :uname)
            on conflict (provider, external_username) do nothing
        """, uid=req.initiated_by_user_id, uname=req.username)
        job = fetch_one(conn, """
            insert into chessbuddy.import_jobs (provider, username, initiated_by_user_id, total_months, status)
            values ('chess.com', :uname, :uid, :tm, 'running')
            returning id
        """, uname=req.username, uid=req.initiated_by_user_id, tm=len(ym))
        job_id = job["id"]
    try:
        for y, m in ym:
            res = import_chesscom_month(req.username, y, m)
            stats["imported"] += res.get("imported", 0)
            stats["skipped"] += res.get("skipped", 0)
            with get_connection() as conn:
                execute(conn, """
                    update chessbuddy.import_jobs
                    set processed_months = processed_months + 1,
                        imported_games = imported_games + :imp,
                        skipped_games = skipped_games + :sk
                    where id=:jid
                """, jid=job_id, imp=res.get("imported", 0), sk=res.get("skipped", 0))
        with get_connection() as conn:
            execute(conn, "update chessbuddy.import_jobs set status='done', finished_at=now() where id=:jid", jid=job_id)
    except Exception as e:  # noqa
        with get_connection() as conn:
            execute(conn, "update chessbuddy.import_jobs set status='failed', error=:err, finished_at=now() where id=:jid", jid=job_id, err=str(e))
        raise
    return {"job_id": job_id, **stats}


@app.post("/import/chesscom/{username}/{year}/{month}")
def import_chesscom_month_endpoint(username: str, year: int, month: int):
    return import_chesscom_month(username, year, month)


@app.get("/categories")
def list_categories():
    with get_connection() as conn:
        rows = fetch_all(conn, "select id, key, name, description from chessbuddy.move_categories order by id")
    return {"items": rows}


@app.get("/games")
def list_games(
    start_time: Optional[datetime] = Query(None, description="played_at >= ISO8601"),
    end_time: Optional[datetime] = Query(None, description="played_at < ISO8601"),
    username: Optional[str] = Query(None, description="filter by white or black username"),
    last_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    clauses = ["true"]
    params: dict = {"lim": limit}
    if start_time is not None:
        clauses.append("played_at >= :st")
        params["st"] = start_time
    if end_time is not None:
        clauses.append("played_at < :et")
        params["et"] = end_time
    if username:
        clauses.append("(white_username = :uname or black_username = :uname)")
        params["uname"] = username
    if last_id is not None:
        clauses.append("id < :lid")
        params["lid"] = last_id
    sql = f"""
    select * from chessbuddy.v_game_meta
    where {' and '.join(clauses)}
    order by id desc
    limit :lim
    """
    with get_connection() as conn:
        rows = fetch_all(conn, sql, **params)
    return {"items": rows}


@app.get("/games/{game_id}")
def get_game(game_id: int):
    with get_connection() as conn:
        g = fetch_one(conn, "select * from chessbuddy.games where id=:id", id=game_id)
        if not g:
            raise HTTPException(404, "game not found")
        moves = fetch_all(conn, "select * from chessbuddy.moves where game_id=:id order by ply", id=game_id)
    return {"game": g, "moves": moves}


@app.delete("/games/{game_id}")
def delete_game(game_id: int):
    with get_connection() as conn:
        execute(conn, "delete from chessbuddy.games where id=:id", id=game_id)
    return {"status": "deleted"}


@app.post("/games/{game_id}/reanalyse")
def reanalyse_game(game_id: int, clear_tasks: bool = Query(False)):
    with get_connection() as conn:
        # Clear evaluations and highlights for clean reanalysis
        execute(conn, "delete from chessbuddy.engine_evaluations where game_id=:id", id=game_id)
        execute(conn, "delete from chessbuddy.move_highlights where game_id=:id", id=game_id)
        if clear_tasks:
            # Remove tasks tied to highlights of this game
            execute(conn, """
                delete from chessbuddy.tactics_responses where task_id in (
                  select id from chessbuddy.tactics_tasks where game_id=:id
                )
            """, id=game_id)
            execute(conn, "delete from chessbuddy.tactics_tasks where game_id=:id", id=game_id)
    analyse_game_pipeline(game_id)
    return {"status": "reanalyzed"}


@app.get("/games/{game_id}/highlights")
def game_highlights(game_id: int):
    with get_connection() as conn:
        rows = fetch_all(conn, "select * from chessbuddy.v_move_highlights_feed where game_id=:gid order by ply", gid=game_id)
    return {"items": rows}


@app.get("/highlights")
def list_highlights(
    category: Optional[str] = Query(None, pattern=r"^[a-z_]+$"),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    username: Optional[str] = Query(None),
    game_id: Optional[int] = Query(None),
    last_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    clauses = ["true"]
    params: dict = {"lim": limit}
    if category:
        clauses.append("category_key = :cat")
        params["cat"] = category
    if start_time is not None:
        clauses.append("played_at >= :st")
        params["st"] = start_time
    if end_time is not None:
        clauses.append("played_at < :et")
        params["et"] = end_time
    if username:
        clauses.append("(white_username = :uname or black_username = :uname)")
        params["uname"] = username
    if game_id is not None:
        clauses.append("game_id = :gid")
        params["gid"] = game_id
    if last_id is not None:
        clauses.append("highlight_id < :lid")
        params["lid"] = last_id
    sql = f"""
    select * from chessbuddy.v_move_highlights_feed
    where {' and '.join(clauses)}
    order by highlight_id desc
    limit :lim
    """
    with get_connection() as conn:
        rows = fetch_all(conn, sql, **params)
    return {"items": rows}


@app.get("/highlights/random")
def random_highlight(category: Optional[str] = Query(None, pattern=r"^[a-z_]+$"), username: Optional[str] = Query(None)):
    with get_connection() as conn:
        clauses = ["true"]
        params: dict = {}
        if category:
            clauses.append("c.key = :cat")
            params["cat"] = category
        if username:
            clauses.append("(wp.username = :uname or bp.username = :uname)")
            params["uname"] = username
        # compute bounds under constraints
        where_sql = " and ".join(clauses)
        bounds = fetch_one(conn, f"""
            select min(h.id) as min_id, max(h.id) as max_id
            from chessbuddy.move_highlights h
            join chessbuddy.move_categories c on c.id = h.category_id
            join chessbuddy.games g on g.id = h.game_id
            join chessbuddy.players wp on wp.id = g.white_player_id
            join chessbuddy.players bp on bp.id = g.black_player_id
            where {where_sql}
        """, **params)
        if not bounds or bounds["min_id"] is None:
            raise HTTPException(404, "no highlights")
        lo, hi = int(bounds["min_id"]), int(bounds["max_id"])
        for _ in range(5):
            sample = random.randint(lo, hi)
            row = fetch_one(conn, f"""
                select * from chessbuddy.v_move_highlights_feed
                where {' and '.join([cond.replace('c.key', 'category_key').replace('wp.username', 'white_username').replace('bp.username', 'black_username') for cond in clauses])}
                  and highlight_id >= :sid
                order by highlight_id asc
                limit 1
            """, **params, sid=sample)
            if row:
                return row
        row = fetch_one(conn, f"""
            select * from chessbuddy.v_move_highlights_feed
            where {' and '.join([cond.replace('c.key', 'category_key').replace('wp.username', 'white_username').replace('bp.username', 'black_username') for cond in clauses])}
            order by highlight_id asc
            limit 1
        """, **params)
        if not row:
            raise HTTPException(404, "no highlights")
        return row


@app.post("/analyse/{game_id}")
def analyse(game_id: int):
    analyse_game_pipeline(game_id)
    return {"status": "ok"}


class VerifyTaskRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"move_uci": "e2e4", "user_id": 1, "response_ms": 2500}
    })
    move_uci: str
    user_id: int = 1
    response_ms: int = 0


@app.post("/tasks/{task_id}/verify")
def verify_task(task_id: int, body: VerifyTaskRequest):
    with get_connection() as conn:
        # The user_id is now resolved via /users/ensure_external
        pass
    try:
        res = verify_task_answer(task_id, body.move_uci, user_id=body.user_id, response_ms=body.response_ms)
    except ValueError:
        raise HTTPException(404, "task not found")
    return res


class CreateTaskFromHighlightRequest(BaseModel):
    user_id: int = 1


@app.post("/highlights/{highlight_id}/create_task")
def create_task_from_highlight(highlight_id: int, body: CreateTaskFromHighlightRequest):
    with get_connection() as conn:
        # The user_id is now resolved via /users/ensure_external
        pass
        row = fetch_one(conn, """
            select h.id, h.game_id, h.ply, m.id as move_id, m.fen_before
            from chessbuddy.move_highlights h
            join chessbuddy.moves m on m.id = h.move_id
            where h.id = :hid
        """, hid=highlight_id)
        if not row or not row["fen_before"] or int(row["ply"]) <= 1:
            raise HTTPException(404, "highlight not found or not solvable")
        task = fetch_one(conn, """
            insert into chessbuddy.tactics_tasks (user_id, game_id, move_id, source_highlight_id, position_ply, fen, category_id)
            values (:uid, :gid, :mid, :hid, :plym1, :fen, (select id from chessbuddy.move_categories where key='blunder'))
            on conflict do nothing
            returning id
        """, uid=body.user_id, gid=row["game_id"], mid=row["move_id"], hid=highlight_id, plym1=int(row["ply"]) - 1, fen=row["fen_before"])
        if not task:
            existing = fetch_one(conn, """
                select id from chessbuddy.tactics_tasks where user_id=:uid and game_id=:gid and position_ply=:plym1
                order by id desc limit 1
            """, uid=body.user_id, gid=row["game_id"], plym1=int(row["ply"]) - 1)
            return {"task_id": existing["id"], "created": False}
        return {"task_id": task["id"], "created": True}


class RandomTaskRequest(BaseModel):
    user_id: int = 1
    category: Optional[str] = None
    username: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


@app.post("/tasks/random")
def random_task(body: RandomTaskRequest):
    # pick a random highlight under constraints, then create/get task for user
    clauses = ["true"]
    params: dict = {}
    if body.category:
        clauses.append("category_key = :cat")
        params["cat"] = body.category
    if body.username:
        clauses.append("(white_username = :uname or black_username = :uname)")
        params["uname"] = body.username
    if body.start_time is not None:
        clauses.append("played_at >= :st")
        params["st"] = body.start_time
    if body.end_time is not None:
        clauses.append("played_at < :et")
        params["et"] = body.end_time
    where_sql = " and ".join(clauses)
    with get_connection() as conn:
        # The user_id is now resolved via /users/ensure_external
        pass
        bounds = fetch_one(conn, f"""
            select min(highlight_id) as min_id, max(highlight_id) as max_id
            from chessbuddy.v_move_highlights_feed
            where {where_sql}
        """, **params)
        if not bounds or bounds["min_id"] is None:
            raise HTTPException(404, "no highlights match filters")
        lo, hi = int(bounds["min_id"]), int(bounds["max_id"])
        chosen = None
        for _ in range(5):
            sample = random.randint(lo, hi)
            chosen = fetch_one(conn, f"""
                select * from chessbuddy.v_move_highlights_feed
                where {where_sql} and highlight_id >= :sid
                order by highlight_id asc
                limit 1
            """, **params, sid=sample)
            if chosen:
                break
        if not chosen:
            chosen = fetch_one(conn, f"""
                select * from chessbuddy.v_move_highlights_feed
                where {where_sql}
                order by highlight_id asc
                limit 1
            """, **params)
        if not chosen:
            raise HTTPException(404, "no highlights match filters")
        # create task from chosen
        hi = fetch_one(conn, "select id, game_id, ply from chessbuddy.move_highlights where id=:hid", hid=chosen["highlight_id"])
        mv = fetch_one(conn, "select id, fen_before from chessbuddy.moves where game_id=:gid and ply=:ply", gid=hi["game_id"], ply=hi["ply"])
        if not mv or not mv["fen_before"] or int(hi["ply"]) <= 1:
            raise HTTPException(404, "highlight not solvable")
        task = fetch_one(conn, """
            insert into chessbuddy.tactics_tasks (user_id, game_id, move_id, source_highlight_id, position_ply, fen, category_id)
            values (:uid, :gid, :mid, :hid, :plym1, :fen, (select id from chessbuddy.move_categories where key='blunder'))
            on conflict do nothing
            returning id
        """, uid=body.user_id, gid=hi["game_id"], mid=mv["id"], hid=hi["id"], plym1=int(hi["ply"]) - 1, fen=mv["fen_before"])
        if not task:
            existing = fetch_one(conn, """
                select id from chessbuddy.tactics_tasks where user_id=:uid and game_id=:gid and position_ply=:plym1
                order by id desc limit 1
            """, uid=body.user_id, gid=hi["game_id"], plym1=int(hi["ply"]) - 1)
            return {"task_id": existing["id"], "created": False}
        return {"task_id": task["id"], "created": True, "highlight_id": hi["id"], "game_id": hi["game_id"], "ply": hi["ply"]}


@app.get("/tasks")
def list_tasks(user_id: Optional[int] = Query(None), status: Optional[str] = Query(None), last_id: Optional[int] = Query(None), limit: int = Query(50, ge=1, le=200)):
    clauses = ["true"]
    params = {"lim": limit}
    if user_id is not None:
        clauses.append("user_id = :uid")
        params["uid"] = user_id
    if status:
        clauses.append("status = :st")
        params["st"] = status
    if last_id is not None:
        clauses.append("id < :lid")
        params["lid"] = last_id
    sql = f"select * from chessbuddy.tactics_tasks where {' and '.join(clauses)} order by id desc limit :lim"
    with get_connection() as conn:
        rows = fetch_all(conn, sql, **params)
    return {"items": rows}
