from __future__ import annotations
from typing import Optional

import chess
import chess.svg
import cairosvg


def fen_to_png_bytes(
    fen: str,
    *,
    last_move_uci: Optional[str] = None,
    show_check: bool = False,
    size: int = 512,
) -> bytes:
    board = chess.Board(fen)
    # Orient the board so the side to move is at the bottom
    orientation = board.turn  # chess.WHITE or chess.BLACK
    arrows = []
    if last_move_uci and len(last_move_uci) >= 4:
        try:
            uci = last_move_uci
            arrows = [chess.svg.Arrow(chess.parse_square(uci[:2]), chess.parse_square(uci[2:4]), color="#00aa00")]  # green arrow
            # Also set lastmove to suppress default red dot markers on unrelated squares
            _from = chess.parse_square(uci[:2])
            _to = chess.parse_square(uci[2:4])
            lastmove = chess.Move(_from, _to)
        except Exception:
            arrows = []
            lastmove = None
    else:
        lastmove = None
    # Disable default coordinate/marker overlays to avoid red dot on a1
    # When show_check is True and the side to move is in check, highlight the king's square.
    check_square = board.king(board.turn) if (show_check and board.is_check()) else None

    svg = chess.svg.board(
        board=board,
        size=size,
        check=check_square,
        arrows=arrows,
        coordinates=True,
        lastmove=lastmove,
        squares=None,  # None to avoid forcing overlays that may create stray markers
        orientation=orientation,
    )
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    return png
