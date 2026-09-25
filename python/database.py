"""Build the SQLite analytics database from the processed star schema.

    1. run sql/01_schema.sql         (tables, keys, indexes)
    2. bulk-load data/processed/*.csv
    3. run sql/02, 03, 05            (DQ assertions, KPI views, commission view)
    4. assert every row of vw_dq_assertions has failing_rows = 0

Named business queries in sql/04_business_analysis.sql are executed by
run_named_queries() and their results saved to reports/sql_results/.

Run:  python python/database.py
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from utils import get_logger, load_config, read_csv_checked

log = get_logger("database")

LOAD_ORDER = ["dim_date", "dim_country", "dim_department", "dim_team", "dim_employee", "dim_product",
              "dim_customer", "dim_commission_plan", "ref_fx_rates", "bridge_rep_team_month",
              "fact_sales", "fact_targets", "fact_team_plan"]
VIEW_SCRIPTS = ["02_cleaning.sql", "03_kpi_analysis.sql", "05_commission.sql"]


def connect(cfg) -> sqlite3.Connection:
    con = sqlite3.connect(cfg["paths"]["database"])
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def run_script(con: sqlite3.Connection, path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    con.executescript(path.read_text(encoding="utf-8"))
    log.info("executed %s", path.name)


def load_tables(cfg, con: sqlite3.Connection) -> None:
    processed = cfg["paths"]["processed"]
    for table in LOAD_ORDER:
        df = read_csv_checked(processed / f"{table}.csv")
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        missing = set(cols) - set(df.columns)
        if missing:
            raise ValueError(f"{table}: columns missing from CSV: {missing}")
        df[cols].to_sql(table, con, if_exists="append", index=False)
        log.info("loaded %-24s %7d rows", table, len(df))
    con.commit()


def assert_data_quality(con: sqlite3.Connection) -> pd.DataFrame:
    dq = pd.read_sql("SELECT * FROM vw_dq_assertions", con)
    failed = dq[dq["failing_rows"] > 0]
    for _, r in dq.iterrows():
        (log.info if r.failing_rows == 0 else log.error)("%s %-4s %s", r.check_id,
                                                         "PASS" if r.failing_rows == 0 else "FAIL", r.check_name)
    if len(failed):
        raise ValueError(f"SQL data-quality assertions failed: {failed['check_id'].tolist()}")
    return dq


def parse_named_queries(path: Path) -> list[dict]:
    """Split a .sql file into blocks headed by '-- name: <id>' and '-- question: <text>'."""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"^-- name:\s*", text, flags=re.M)[1:]
    queries = []
    for b in blocks:
        name, _, body = b.partition("\n")
        q = re.search(r"^-- question:\s*(.+)$", body, flags=re.M)
        sql = body.strip().rstrip(";")
        queries.append({"name": name.strip(), "question": q.group(1).strip() if q else "", "sql": sql})
    return queries


def run_named_queries(cfg, con: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    out_dir = cfg["paths"]["reports"] / "sql_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for q in parse_named_queries(cfg["paths"]["sql"] / "04_business_analysis.sql"):
        try:
            df = pd.read_sql(q["sql"], con)
        except Exception as exc:
            raise RuntimeError(f"Query {q['name']} failed: {exc}") from exc
        df.to_csv(out_dir / f"{q['name']}.csv", index=False)
        results[q["name"]] = df
        log.info("%-38s %5d rows | %s", q["name"], len(df), q["question"][:70])
    return results


def build(cfg) -> None:
    db = cfg["paths"]["database"]
    if db.exists():
        db.unlink()
    con = connect(cfg)
    try:
        run_script(con, cfg["paths"]["sql"] / "01_schema.sql")
        load_tables(cfg, con)
        for s in VIEW_SCRIPTS:
            run_script(con, cfg["paths"]["sql"] / s)
        assert_data_quality(con)
    finally:
        con.close()
    log.info("database ready: %s", db)


if __name__ == "__main__":
    build(load_config())
