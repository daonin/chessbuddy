from __future__ import annotations
import argparse
import sys

from .engine_worker import analyse_game_pipeline, verify_task_answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cbuddy")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_an = sub.add_parser("analyse-game", help="Run full engine analysis pipeline for a game id")
    p_an.add_argument("game_id", type=int)

    p_v = sub.add_parser("verify-task", help="Verify a tactics task answer against engine")
    p_v.add_argument("task_id", type=int)
    p_v.add_argument("move_uci", type=str)

    args = parser.parse_args(argv)

    if args.cmd == "analyse-game":
        analyse_game_pipeline(args.game_id)
        return 0
    if args.cmd == "verify-task":
        verify_task_answer(args.task_id, args.move_uci)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
