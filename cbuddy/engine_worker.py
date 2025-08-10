from __future__ import annotations
import os
import shutil
import time
from dataclasses import dataclass
from typing import Optional

import chess
import chess.engine

from .config import AppConfig
from .db import get_connection, fetch_all, fetch_one, execute


@dataclass
class EvalResult:
    cp: int
    best_uci: Optional[str]
    depth: Optional[int]


def get_engine_path(cfg: Optional[AppConfig] = None) -> str:
    cfg = cfg or AppConfig()
    candidates: list[Optional[str]] = [
        cfg.engine.engine_path,
        shutil.which("stockfish"),
        "/usr/games/stockfish",
        "/usr/bin/stockfish",
        "/usr/local/bin/stockfish",
        "/opt/homebrew/bin/stockfish",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("Stockfish binary not found; set ENGINE_PATH or install stockfish in PATH")


def _open_engine(cfg: AppConfig) -> chess.engine.SimpleEngine:
    path = get_engine_path(cfg)
    eng = chess.engine.SimpleEngine.popen_uci(path)
    eng.configure({
        "Threads": cfg.engine.threads,
        "Hash": cfg.engine.hash_mb,
    })
    return eng


def _eval_fen(engine: chess.engine.SimpleEngine, fen: str, movetime_ms: int, multipv: int = 1) -> list[EvalResult]:
    engine.configure({"MultiPV": multipv})
    board = chess.Board(fen)
    info = engine.analyse(board, chess.engine.Limit(time=movetime_ms / 1000))
    infos = info if isinstance(info, list) else [info]
    results: list[EvalResult] = []
    for li in infos:
        score = li["score"].pov(board.turn)
        cp = score.score(mate_score=10_000)
        pv = li.get("pv") or []
        best_uci = pv[0].uci() if pv else None
        results.append(EvalResult(cp=cp, best_uci=best_uci, depth=li.get("depth")))
    return results


def analyse_game_fast(game_id: int) -> None:
    """Fast pass: evaluate positions before/after each move, store engine_evaluations."""
    cfg = AppConfig()
    with _open_engine(cfg) as engine, get_connection() as conn:
        moves = fetch_all(conn, """
            select id, ply, fen_before, fen_after from chessbuddy.moves
            where game_id = :gid
            order by ply asc
        """, gid=game_id)
        for m in moves:
            for key, fen in (("before", m["fen_before"]), ("after", m["fen_after"])):
                if not fen:
                    continue
                res = _eval_fen(engine, fen, cfg.engine.fast_movetime_ms, multipv=1)[0]
                execute(conn, """
                    insert into chessbuddy.engine_evaluations (
                        game_id, move_id, ply, eval_side, score_cp, score_mate, best_move_uci, depth, engine_name, engine_version
                    ) values (
                        :gid, :mid, :ply, :side, :cp, null, :best, :depth, :ename, :ever
                    ) on conflict do nothing
                """, gid=game_id, mid=m["id"], ply=m["ply"], side='w' if (m["ply"] % 2 == 1) else 'b',
                    cp=res.cp, best=res.best_uci, depth=res.depth, ename='stockfish', ever='local')


def _classify_delta(delta_cp: int, thresholds=AppConfig().thresholds) -> Optional[str]:
    if delta_cp <= thresholds.blunder_cp:
        return 'blunder'
    if delta_cp <= thresholds.mistake_cp:
        return 'mistake'
    if delta_cp <= thresholds.inaccuracy_cp:
        return 'inaccuracy'
    if delta_cp >= thresholds.brilliant_cp:
        return 'brilliant'
    if delta_cp >= thresholds.great_cp:
        return 'great'
    return None


def annotate_highlights(game_id: int) -> None:
    cfg = AppConfig()
    with get_connection() as conn:
        rows = fetch_all(conn, """
            with evals as (
              select m.id as move_id, m.ply,
                lag(e.score_cp) over (order by m.ply) as before_cp,
                e.score_cp as after_cp
              from chessbuddy.moves m
              join chessbuddy.engine_evaluations e on e.move_id = m.id
              where m.game_id = :gid
            )
            select move_id, ply, before_cp, after_cp, (after_cp - before_cp) as delta
            from evals
            where before_cp is not null
            order by ply asc
        """, gid=game_id)
        for r in rows:
            cat_key = _classify_delta(r["delta"], cfg.thresholds)
            if not cat_key:
                continue
            cat = fetch_one(conn, "select id from chessbuddy.move_categories where key = :k", k=cat_key)
            if not cat:
                continue
            execute(conn, """
                insert into chessbuddy.move_highlights (
                  game_id, move_id, category_id, ply,
                  eval_before_cp, eval_after_cp, eval_delta_cp
                ) values (:gid, :mid, :cid, :ply, :before, :after, :delta)
                on conflict do nothing
            """, gid=game_id, mid=r["move_id"], cid=cat["id"], ply=r["ply"], before=r["before_cp"], after=r["after_cp"], delta=r["delta"])


def deep_refine_candidates(game_id: int) -> None:
    cfg = AppConfig()
    with _open_engine(cfg) as engine, get_connection() as conn:
        cands = fetch_all(conn, """
            select h.id as highlight_id, m.id as move_id, m.ply, m.fen_before, m.fen_after
            from chessbuddy.move_highlights h
            join chessbuddy.moves m on m.id = h.move_id
            where h.game_id = :gid
        """, gid=game_id)
        for c in cands:
            if not c["fen_before"] or not c["fen_after"]:
                continue
            before = _eval_fen(engine, c["fen_before"], cfg.engine.deep_movetime_ms, cfg.engine.deep_multipv)[0]
            after = _eval_fen(engine, c["fen_after"], cfg.engine.deep_movetime_ms, cfg.engine.deep_multipv)[0]
            delta = after.cp - before.cp
            execute(conn, """
                update chessbuddy.move_highlights
                set eval_before_cp = :before, eval_after_cp = :after, eval_delta_cp = :delta
                where id = :hid
            """, before=before.cp, after=after.cp, delta=delta, hid=c["highlight_id"])
            execute(conn, """
                insert into chessbuddy.engine_evaluations (game_id, move_id, ply, eval_side, score_cp, best_move_uci, depth, engine_name, engine_version)
                values (:gid, :mid, :ply, :side, :cp, :best, :depth, 'stockfish','local')
                on conflict do nothing
            """, gid=game_id, mid=c["move_id"], ply=c["ply"], side='w' if (c["ply"] % 2 == 1) else 'b', cp=after.cp, best=after.best_uci, depth=after.depth)


def create_tasks_from_blunders(game_id: int) -> None:
    with get_connection() as conn:
        blunders = fetch_all(conn, """
            select h.id as highlight_id, h.game_id, h.ply, m.id as move_id, m.fen_before
            from chessbuddy.move_highlights h
            join chessbuddy.move_categories c on c.id = h.category_id and c.key = 'blunder'
            join chessbuddy.moves m on m.id = h.move_id
            where h.game_id = :gid
        """, gid=game_id)
        for b in blunders:
            if not b["fen_before"] or b["ply"] <= 1:
                continue
            execute(conn, """
                insert into chessbuddy.tactics_tasks (user_id, game_id, move_id, source_highlight_id, position_ply, fen, category_id)
                values (1, :gid, :mid, :hid, :pply, :fen, (select id from chessbuddy.move_categories where key='blunder'))
                on conflict do nothing
            """, gid=b["game_id"], mid=b["move_id"], hid=b["highlight_id"], pply=b["ply"] - 1, fen=b["fen_before"])


def verify_task_answer(task_id: int, proposed_move_uci: str, *, user_id: int = 1, response_ms: int = 0) -> dict:
    cfg = AppConfig()
    with _open_engine(cfg) as engine, get_connection() as conn:
        t = fetch_one(conn, "select id, fen from chessbuddy.tactics_tasks where id = :tid", tid=task_id)
        if not t:
            raise ValueError("task not found")
        board = chess.Board(t["fen"])
        res_list = _eval_fen(engine, t["fen"], cfg.engine.deep_movetime_ms, cfg.engine.deep_multipv)
        best = res_list[0]
        board.push_uci(proposed_move_uci)
        after = _eval_fen(engine, board.fen(), cfg.engine.deep_movetime_ms, cfg.engine.deep_multipv)[0]
        delta = best.cp - after.cp
        is_correct = (proposed_move_uci == best.best_uci) or (delta <= cfg.thresholds.near_best_tolerance_cp)
        row = fetch_one(conn, """
            insert into chessbuddy.tactics_responses (
                task_id, user_id, proposed_move_uci, proposed_move_san, response_ms,
                evaluated_by_engine, is_correct, score_cp_delta, engine_eval_after_cp, engine_best_move_uci
            ) values (
                :tid, :uid, :uci, null, :ms, true, :ok, :delta, :after_cp, :best
            ) returning id
        """, tid=task_id, uid=user_id, uci=proposed_move_uci, ms=response_ms, ok=is_correct, delta=delta, after_cp=after.cp, best=best.best_uci)
        execute(conn, "update chessbuddy.tactics_tasks set status='answered', answered_at=now() where id=:tid", tid=task_id)
        return {
            "response_id": row["id"],
            "is_correct": is_correct,
            "score_cp_delta": delta,
            "engine_best_move_uci": best.best_uci,
            "engine_after_cp": after.cp,
        }


def analyse_game_pipeline(game_id: int) -> None:
    analyse_game_fast(game_id)
    annotate_highlights(game_id)
    deep_refine_candidates(game_id)
    create_tasks_from_blunders(game_id)
