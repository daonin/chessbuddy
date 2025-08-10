from __future__ import annotations
import io
import json
import hashlib
from dataclasses import dataclass
from typing import Optional

import chess
import chess.pgn
import httpx

from .db import get_connection, fetch_one, execute


@dataclass
class ImportResult:
    game_id: int
    created: bool


ALLOWED_PLAYER_PROVIDERS = {"chess.com", "lichess", "local", "other"}
ALLOWED_GAME_SOURCES = {"chess.com", "lichess", "manual", "other"}


def _normalize_player_provider(src: Optional[str]) -> str:
    if not src:
        return "other"
    if src in ALLOWED_PLAYER_PROVIDERS:
        return src
    if src == "manual":
        return "local"
    return "other"


def _normalize_game_source(src: Optional[str]) -> str:
    if not src:
        return "other"
    if src in ALLOWED_GAME_SOURCES:
        return src
    if src == "local":
        return "manual"
    return "other"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def import_pgn(
    pgn_text: str,
    *,
    external_source: Optional[str] = None,
    external_game_id: Optional[str] = None,
    url: Optional[str] = None,
    source_raw: Optional[dict] = None,
) -> ImportResult:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("Invalid PGN: cannot parse")

    headers = dict(game.headers)
    white_name = headers.get("White") or "unknown"
    black_name = headers.get("Black") or "unknown"
    white_elo = int(headers.get("WhiteElo") or 0) or None
    black_elo = int(headers.get("BlackElo") or 0) or None
    time_control = headers.get("TimeControl")
    time_class = headers.get("TimeClass") or headers.get("Event")
    rated = (headers.get("Event") or "").lower().startswith("rated") or None
    termination = headers.get("Termination")
    result = headers.get("Result")
    played_at = None
    site = headers.get("Site")

    prov_players = _normalize_player_provider(external_source)
    prov_game = _normalize_game_source(external_source)

    # chess.com
    if prov_game == "chess.com" and not external_game_id and site and site.startswith("https://www.chess.com/game/"):
        external_game_id = site.rsplit('/', 1)[-1]

    pgn_sha1 = _sha1(pgn_text)

    # Players
    with get_connection() as conn:
        w = fetch_one(conn, """
            insert into chessbuddy.players (provider, username, display_name)
            values (:prov, :uname, :dname)
            on conflict (provider, username) do update set display_name = excluded.display_name
            returning id
        """, prov=prov_players, uname=white_name, dname=white_name)
        b = fetch_one(conn, """
            insert into chessbuddy.players (provider, username, display_name)
            values (:prov, :uname, :dname)
            on conflict (provider, username) do update set display_name = excluded.display_name
            returning id
        """, prov=prov_players, uname=black_name, dname=black_name)

        # Link players to internal users if known via external_accounts
        execute(conn, """
            update chessbuddy.players p
            set user_id = ea.user_id
            from chessbuddy.external_accounts ea
            where ea.provider = p.provider and ea.external_username = p.username and p.id in (:wid, :bid) and p.user_id is distinct from ea.user_id
        """, wid=w["id"], bid=b["id"])

        # Game insert/dedupe
        existing = None
        if external_game_id:
            existing = fetch_one(conn, """
                select id from chessbuddy.games where external_source=:src and external_game_id=:gid
            """, src=prov_game, gid=external_game_id)
        if not existing:
            existing = fetch_one(conn, "select id from chessbuddy.games where pgn_sha1=:h", h=pgn_sha1)

        if existing:
            game_id = int(existing["id"])
            created = False
        else:
            row = fetch_one(conn, """
                insert into chessbuddy.games (
                    external_source, external_game_id, url, pgn, pgn_headers, pgn_sha1,
                    white_player_id, black_player_id, white_rating, black_rating, time_control,
                    time_class, rated, termination, result, played_at, imported_at, source_raw
                ) values (
                    :src, :gid, :url, :pgn, (:hdrs)::jsonb, :sha1,
                    :wp, :bp, :wr, :br, :tc, :tclass, :rated, :term, :res, :played_at, now(), (:raw)::jsonb
                ) returning id
            """,
            src=prov_game, gid=external_game_id, url=url, pgn=pgn_text,
            hdrs=json.dumps(headers), sha1=pgn_sha1, wp=w["id"], bp=b["id"], wr=white_elo, br=black_elo,
            tc=time_control, tclass=time_class, rated=rated, term=termination, res=result,
            played_at=played_at, raw=json.dumps(source_raw or {}))
            game_id = int(row["id"])
            created = True

        # Moves
        if created:
            board = game.board()
            ply = 0
            node = game
            while node.variations:
                move = node.variation(0).move
                ply += 1
                fen_before = board.fen()
                san = board.san(move)
                uci = move.uci()
                from_sq = chess.SQUARE_NAMES[move.from_square]
                to_sq = chess.SQUARE_NAMES[move.to_square]
                piece = board.piece_at(move.from_square)
                piece_name = piece.symbol().lower() if piece else None
                is_capture = board.is_capture(move)
                promo = None
                if move.promotion:
                    promo = chess.piece_symbol(move.promotion)
                board.push(move)
                fen_after = board.fen()
                is_check = board.is_check()
                is_checkmate = board.is_checkmate()
                move_number = (ply + 1) // 2
                side = 'w' if (ply % 2 == 1) else 'b'

                execute(conn, """
                    insert into chessbuddy.moves (
                        game_id, ply, move_number, side, san, uci, from_square, to_square,
                        piece, capture, promotion, is_check, is_checkmate, fen_before, fen_after
                    ) values (
                        :gid, :ply, :mnum, :side, :san, :uci, :froms, :tos,
                        :piece, :cap, :promo, :is_chk, :is_mate, :f_before, :f_after
                    )
                """,
                gid=game_id, ply=ply, mnum=move_number, side=side, san=san, uci=uci,
                froms=from_sq, tos=to_sq, piece=piece_name, cap=is_capture, promo=promo,
                is_chk=is_check, is_mate=is_checkmate, f_before=fen_before, f_after=fen_after)

                node = node.variation(0)

    return ImportResult(game_id=game_id, created=created)


def import_chesscom_month(username: str, year: int, month: int) -> dict:
    url = f"https://api.chess.com/pub/player/{username}/games/{year:04d}/{month:02d}"
    with httpx.Client(timeout=30) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    imported = 0
    skipped = 0
    last_error: Optional[str] = None
    for g in data.get("games", []):
        pgn = g.get("pgn")
        if not pgn:
            continue
        try:
            res = import_pgn(
                pgn,
                external_source="chess.com",
                external_game_id=(g.get("url") or "").rsplit('/', 1)[-1] or None,
                url=g.get("url"),
                source_raw=g,
            )
            if res.created:
                imported += 1
            else:
                skipped += 1
        except Exception as e:  # noqa
            last_error = str(e)
    return {"imported": imported, "skipped": skipped, "error": last_error}


def import_chesscom_game(url: str) -> ImportResult:
    with httpx.Client(timeout=30) as client:
        r = client.get(url)
        r.raise_for_status()
        text = r.text
        if text.lstrip().startswith("[") and "[Event" in text[:200]:
            return import_pgn(text, external_source="chess.com", external_game_id=url.rsplit('/', 1)[-1], url=url)
        try:
            data = r.json()
            pgn = data.get("pgn")
            if pgn:
                return import_pgn(pgn, external_source="chess.com", external_game_id=url.rsplit('/', 1)[-1], url=url, source_raw=data)
        except Exception:
            pass
    raise ValueError("unsupported chess.com game url; try importing by month archive")
