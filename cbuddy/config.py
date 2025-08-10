from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass
class EngineConfig:
    engine_path: str = os.getenv("ENGINE_PATH", "/opt/homebrew/bin/stockfish")
    threads: int = int(os.getenv("ENGINE_THREADS", "2"))
    hash_mb: int = int(os.getenv("ENGINE_HASH_MB", "256"))
    # fast pass
    fast_movetime_ms: int = int(os.getenv("ENGINE_FAST_MOVETIME_MS", "40"))
    fast_depth: int | None = None  # can override to fixed depth
    # deep pass
    deep_movetime_ms: int = int(os.getenv("ENGINE_DEEP_MOVETIME_MS", "400"))
    deep_multipv: int = int(os.getenv("ENGINE_DEEP_MULTIPV", "3"))


@dataclass
class HighlightThresholds:
    # Радикально строгие дефолты, чтобы обрезать шум
    # delta_cp = after - before; отрицательные значения — ухудшения
    brilliant_cp: int = int(os.getenv("THRESH_BRILLIANT_CP", "900"))
    great_cp: int = int(os.getenv("THRESH_GREAT_CP", "600"))
    inaccuracy_cp: int = int(os.getenv("THRESH_INACCURACY_CP", "-400"))
    mistake_cp: int = int(os.getenv("THRESH_MISTAKE_CP", "-800"))
    blunder_cp: int = int(os.getenv("THRESH_BLUNDER_CP", "-1200"))
    near_best_tolerance_cp: int = int(os.getenv("THRESH_NEAR_BEST_TOL_CP", "10"))


@dataclass
class AppConfig:
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg:///chessbuddy")
    engine: EngineConfig = field(default_factory=EngineConfig)
    thresholds: HighlightThresholds = field(default_factory=HighlightThresholds)
