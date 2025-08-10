from __future__ import annotations
from typing import Optional

import chess
import chess.svg
import cairosvg


def fen_to_png_bytes(fen: str, *, last_move_uci: Optional[str] = None, check: bool = False, size: int = 512) -> bytes:
    board = chess.Board(fen)
    # Orient the board so the side to move is at the bottom
    orientation = board.turn  # chess.WHITE or chess.BLACK
    arrows = []
    if last_move_uci and len(last_move_uci) >= 4:
        try:
            uci = last_move_uci
            arrows = [chess.svg.Arrow(chess.parse_square(uci[:2]), chess.parse_square(uci[2:4]), color="#00aa00")]  # green arrow
            # Also set lastmove to suppress default red dot markers on unrelated squares
            lastmove = (chess.parse_square(uci[:2]), chess.parse_square(uci[2:4]))
        except Exception:
            arrows = []
            lastmove = None
    else:
        lastmove = None
    # Disable default coordinate/marker overlays to avoid red dot on a1
    svg = chess.svg.board(
        board=board,
        size=size,
        check=check,
        arrows=arrows,
        coordinates=True,
        lastmove=lastmove,
        squares=[],
        orientation=orientation,
    )
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    return png
