"""Run the full pipeline end to end.

    python run_pipeline.py              # generate -> ETL -> SQL -> KPIs -> analysis -> visuals -> reports
    python run_pipeline.py --skip-generate   # reuse existing data/raw extracts

Every step fails loudly: a data-quality gate that does not pass stops the run.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))

import analysis  # noqa: E402
import database  # noqa: E402
import etl  # noqa: E402
import generate_data  # noqa: E402
import kpi_engine  # noqa: E402
import reporting  # noqa: E402
import visuals  # noqa: E402
from utils import get_logger, load_config, read_csv_checked  # noqa: E402

log = get_logger("pipeline")


def export_samples(cfg) -> None:
    """Small, committed samples so reviewers can inspect data without running anything."""
    n = cfg["reporting"]["sample_rows"]
    sample, raw, proc = cfg["paths"]["sample"], cfg["paths"]["raw"], cfg["paths"]["processed"]
    read_csv_checked(raw / "raw_sales_lines.csv").head(2000).to_csv(sample / "sample_raw_sales_lines.csv", index=False)
    fs = read_csv_checked(proc / "fact_sales.csv")
    fs.sample(n=min(n, len(fs)), random_state=cfg["project"]["seed"]).sort_values("date_key") \
      .to_csv(sample / "sample_fact_sales.csv", index=False)
    read_csv_checked(proc / "quarantine_sales_lines.csv").to_csv(sample / "sample_quarantine_sales_lines.csv", index=False)
    log.info("sample datasets written to %s", sample)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-generate", action="store_true", help="reuse existing raw extracts")
    args = parser.parse_args()
    cfg = load_config()
    steps = [
        ("generate synthetic raw data", generate_data.main, not args.skip_generate),
        ("ETL + data validation", etl.main, True),
        ("build SQLite model + SQL DQ assertions", lambda: database.build(cfg), True),
        ("run business SQL queries", lambda: database.run_named_queries(cfg, database.connect(cfg)), True),
        ("KPI engine + Python/SQL reconciliation", kpi_engine.main, True),
        ("advanced analysis", analysis.main, True),
        ("render visuals", visuals.main, True),
        ("automated reports", reporting.main, True),
        ("export samples", lambda: export_samples(cfg), True),
    ]
    t0 = time.time()
    for i, (name, fn, enabled) in enumerate(steps, start=1):
        if not enabled:
            log.info("[%d/%d] skipped: %s", i, len(steps), name)
            continue
        log.info("[%d/%d] %s", i, len(steps), name)
        fn()
    log.info("pipeline finished in %.0fs", time.time() - t0)


if __name__ == "__main__":
    main()
