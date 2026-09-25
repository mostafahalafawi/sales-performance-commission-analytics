"""Shared helpers: configuration loading, paths, logging, small date utilities.

Every script in the pipeline imports from here so paths and business rules are
defined once (config/config.yaml) and never hard-coded.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def load_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    """Load the YAML configuration and resolve all paths to absolute paths."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["paths"] = {k: (PROJECT_ROOT / v) for k, v in cfg["paths"].items()}
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    for key in ("raw", "processed", "sample", "reports", "images", "insights"):
        cfg["paths"][key].mkdir(parents=True, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(name)-11s | %(levelname)-7s | %(message)s",
                                               "%H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def month_range(start: str, end: str) -> list[pd.Period]:
    return list(pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M"))


def read_csv_checked(path: Path, **kwargs) -> pd.DataFrame:
    """Read a CSV with a clear error if an upstream step has not been run."""
    if not path.exists():
        raise FileNotFoundError(
            f"Expected input '{path.name}' not found in {path.parent}. "
            "Run the previous pipeline step first (python run_pipeline.py)."
        )
    return pd.read_csv(path, **kwargs)


def plan_for_month(cfg: dict[str, Any], month: pd.Period) -> dict[str, Any]:
    """Return the commission plan whose effective window contains `month`."""
    ts = month.to_timestamp()
    for plan in cfg["commission_plans"]:
        if pd.Timestamp(plan["effective_from"]) <= ts <= pd.Timestamp(plan["effective_to"]):
            return plan
    raise ValueError(f"No commission plan configured for {month}")


def tier_for_achievement(plan: dict[str, Any], achievement: float) -> tuple[str, float]:
    """Map an achievement ratio to (tier name, rate) using half-open [min, max) bands."""
    if achievement is None or pd.isna(achievement):
        return ("No Target", 0.0)
    for tier in plan["tiers"]:
        if tier["min"] <= achievement < tier["max"]:
            return (tier["tier"], float(tier["rate"]))
    return (plan["tiers"][-1]["tier"], float(plan["tiers"][-1]["rate"]))
