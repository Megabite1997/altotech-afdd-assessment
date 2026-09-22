"""Runtime configuration. Everything is environment driven; see .env.example."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- stores -------------------------------------------------------------
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql://afdd:afdd@localhost:5432/afdd"
        )
    )
    db_pool_min: int = field(default_factory=lambda: _int("DB_POOL_MIN", 1))
    db_pool_max: int = field(default_factory=lambda: _int("DB_POOL_MAX", 8))

    # --- broker -------------------------------------------------------------
    kafka_bootstrap: str = field(
        default_factory=lambda: os.getenv("KAFKA_BOOTSTRAP", "localhost:19092")
    )
    telemetry_topic: str = field(
        default_factory=lambda: os.getenv("TELEMETRY_TOPIC", "telemetry.device.v1")
    )
    dlq_topic: str = field(
        default_factory=lambda: os.getenv("DLQ_TOPIC", "telemetry.device.v1.dlq")
    )
    consumer_group: str = field(
        default_factory=lambda: os.getenv("CONSUMER_GROUP", "afdd-ingest")
    )
    topic_partitions: int = field(default_factory=lambda: _int("TOPIC_PARTITIONS", 6))

    # --- simulator ----------------------------------------------------------
    source_dir: Path = field(
        default_factory=lambda: Path(os.getenv("SOURCE_DIR", "/srv/source-pack"))
    )
    sim_interval_seconds: int = field(default_factory=lambda: _int("SIM_INTERVAL_SECONDS", 60))
    sim_speedup: float = field(default_factory=lambda: _float("SIM_SPEEDUP", 60.0))
    sim_buildings: str = field(default_factory=lambda: os.getenv("SIM_BUILDINGS", "*"))
    sim_loop: bool = field(default_factory=lambda: _bool("SIM_LOOP", False))
    sim_start_delay_seconds: float = field(
        default_factory=lambda: _float("SIM_START_DELAY_SECONDS", 5.0)
    )

    # --- evaluator ----------------------------------------------------------
    eval_tick_seconds: float = field(default_factory=lambda: _float("EVAL_TICK_SECONDS", 5.0))
    eval_clock: str = field(default_factory=lambda: os.getenv("EVAL_CLOCK", "data"))

    # --- agent --------------------------------------------------------------
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-5"))
    agent_max_iterations: int = field(default_factory=lambda: _int("AGENT_MAX_ITERATIONS", 6))
    agent_timeout_seconds: float = field(
        default_factory=lambda: _float("AGENT_TIMEOUT_SECONDS", 90.0)
    )

    # --- api ----------------------------------------------------------------
    api_cors_origins: str = field(
        default_factory=lambda: os.getenv("API_CORS_ORIGINS", "*")
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    @property
    def sim_building_filter(self) -> list[str] | None:
        raw = self.sim_buildings.strip()
        if raw in ("", "*", "all"):
            return None
        return [b.strip() for b in raw.split(",") if b.strip()]


settings = Settings()
