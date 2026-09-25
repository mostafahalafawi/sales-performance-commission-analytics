-- =============================================================================
-- 01_schema.sql  |  Star schema for Sales Performance & Commission Analytics
-- -----------------------------------------------------------------------------
-- Engine: SQLite 3.25+ (window functions). Syntax is kept close to ANSI SQL so
-- the model ports to PostgreSQL / SQL Server with type changes only.
--
-- Grain
--   fact_sales            one row per invoice or credit-note line
--   fact_targets          one row per rep per month (latest revision only)
--   fact_team_plan        one row per team per month (capacity plan)
--   bridge_rep_team_month one row per active rep per month (team as of the 15th)
--   kpi_*                 KPI layer written by python/kpi_engine.py
-- =============================================================================
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS kpi_rep_month;
DROP TABLE IF EXISTS kpi_team_month;
DROP TABLE IF EXISTS kpi_country_month;
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS fact_targets;
DROP TABLE IF EXISTS fact_team_plan;
DROP TABLE IF EXISTS bridge_rep_team_month;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_employee;
DROP TABLE IF EXISTS dim_team;
DROP TABLE IF EXISTS dim_department;
DROP TABLE IF EXISTS dim_country;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS dim_commission_plan;
DROP TABLE IF EXISTS ref_fx_rates;

-- ----------------------------------------------------------------- dimensions
CREATE TABLE dim_date (
    date_key            INTEGER PRIMARY KEY,          -- YYYYMMDD
    date                TEXT    NOT NULL,
    year                INTEGER NOT NULL,
    quarter             TEXT    NOT NULL,
    month_num           INTEGER NOT NULL,
    month_name          TEXT    NOT NULL,
    month_key           INTEGER NOT NULL,             -- YYYYMM
    year_month          TEXT    NOT NULL,
    month_start_date    TEXT    NOT NULL,
    day_of_month        INTEGER NOT NULL,
    days_in_month       INTEGER NOT NULL,
    weekday_name        TEXT    NOT NULL,
    is_month_end_window INTEGER NOT NULL,             -- last N days of month (config)
    month_index         INTEGER NOT NULL              -- 1..24, simplifies trend maths
);
CREATE INDEX ix_dim_date_month ON dim_date(month_key);

CREATE TABLE dim_country (
    country_code TEXT PRIMARY KEY,
    country_name TEXT NOT NULL,
    currency     TEXT NOT NULL,
    region       TEXT NOT NULL
);

CREATE TABLE dim_department (
    department_code TEXT PRIMARY KEY,
    department_name TEXT NOT NULL
);

CREATE TABLE dim_team (
    team_key        INTEGER PRIMARY KEY,
    team_id         TEXT NOT NULL UNIQUE,
    team_name       TEXT NOT NULL,
    department_code TEXT NOT NULL REFERENCES dim_department(department_code),
    department_name TEXT NOT NULL,
    country_code    TEXT NOT NULL REFERENCES dim_country(country_code),
    country_name    TEXT NOT NULL,
    manager_code    TEXT,
    manager_name    TEXT,
    hc_2024         INTEGER,
    hc_2025         INTEGER
);

CREATE TABLE dim_employee (
    employee_key          INTEGER PRIMARY KEY,
    employee_code         TEXT NOT NULL UNIQUE,
    full_name             TEXT NOT NULL,
    role                  TEXT NOT NULL,                -- Sales Representative / Sales Manager
    hire_date             TEXT NOT NULL,
    exit_date             TEXT,
    employment_status     TEXT NOT NULL,
    tenure_band           TEXT,
    is_new_hire_in_period INTEGER,
    current_team_id       TEXT,
    manager_code          TEXT,
    country_code          TEXT,
    department_code       TEXT
);

CREATE TABLE dim_product (
    product_key       INTEGER PRIMARY KEY,
    sku               TEXT NOT NULL UNIQUE,
    product_name      TEXT NOT NULL,
    product_line      TEXT NOT NULL,
    list_price_usd    REAL NOT NULL,
    unit_cost_pct     REAL NOT NULL,
    is_strategic      INTEGER NOT NULL,
    commission_weight REAL NOT NULL
);

CREATE TABLE dim_customer (
    customer_key     INTEGER PRIMARY KEY,
    customer_code    TEXT NOT NULL UNIQUE,
    customer_name    TEXT NOT NULL,
    country_code     TEXT NOT NULL REFERENCES dim_country(country_code),
    customer_segment TEXT NOT NULL,
    industry         TEXT,
    city             TEXT,
    owning_team_id   TEXT,
    created_date     TEXT
);

CREATE TABLE dim_commission_plan (
    plan_tier_key          INTEGER PRIMARY KEY,
    plan_id                TEXT NOT NULL,
    plan_name              TEXT NOT NULL,
    effective_from         TEXT NOT NULL,
    effective_to           TEXT NOT NULL,
    tier_order             INTEGER NOT NULL,
    tier_name              TEXT NOT NULL,
    min_achievement        REAL NOT NULL,             -- inclusive
    max_achievement        REAL NOT NULL,             -- exclusive
    commission_rate        REAL NOT NULL,
    strategic_weight       REAL NOT NULL,
    cap_multiple_of_target REAL NOT NULL,
    clawback_window_days   INTEGER NOT NULL
);

CREATE TABLE ref_fx_rates (
    fx_month     TEXT NOT NULL,
    currency     TEXT NOT NULL,
    rate_per_usd REAL NOT NULL,
    PRIMARY KEY (fx_month, currency)
);

-- ---------------------------------------------------------------------- facts
CREATE TABLE bridge_rep_team_month (
    rep_key       INTEGER NOT NULL REFERENCES dim_employee(employee_key),
    month_key     INTEGER NOT NULL,
    team_key      INTEGER NOT NULL REFERENCES dim_team(team_key),
    tenure_months INTEGER NOT NULL,
    is_ramp_month INTEGER NOT NULL,
    PRIMARY KEY (rep_key, month_key)
);

CREATE TABLE fact_sales (
    line_id              TEXT PRIMARY KEY,
    document_no          TEXT    NOT NULL,
    doc_type             TEXT    NOT NULL CHECK (doc_type IN ('INV','CN')),
    original_document_no TEXT,                          -- credit notes only
    date_key             INTEGER NOT NULL REFERENCES dim_date(date_key),
    month_key            INTEGER NOT NULL,
    rep_key              INTEGER NOT NULL REFERENCES dim_employee(employee_key),
    team_key             INTEGER NOT NULL REFERENCES dim_team(team_key),
    customer_key         INTEGER NOT NULL REFERENCES dim_customer(customer_key),
    product_key          INTEGER NOT NULL REFERENCES dim_product(product_key),
    country_code         TEXT    NOT NULL REFERENCES dim_country(country_code),
    currency             TEXT    NOT NULL,
    fx_rate              REAL    NOT NULL,
    quantity             INTEGER NOT NULL,
    unit_list_price_usd  REAL    NOT NULL,
    discount_pct         REAL    NOT NULL,
    gross_amount_usd     REAL    NOT NULL,              -- list value
    discount_amount_usd  REAL    NOT NULL,
    net_amount_usd       REAL    NOT NULL,              -- revenue (negative for credit notes)
    cost_amount_usd      REAL    NOT NULL,
    gross_margin_usd     REAL    NOT NULL,
    commission_weight    REAL    NOT NULL,              -- 1.25 for strategic product lines
    days_since_original  REAL,                          -- credit notes: days after invoice
    is_month_end_window  INTEGER NOT NULL,
    net_amount_local     REAL    NOT NULL
);
CREATE INDEX ix_fs_rep_month  ON fact_sales(rep_key, month_key);
CREATE INDEX ix_fs_team_month ON fact_sales(team_key, month_key);
CREATE INDEX ix_fs_product    ON fact_sales(product_key);
CREATE INDEX ix_fs_doc        ON fact_sales(document_no);

CREATE TABLE fact_targets (
    rep_key           INTEGER NOT NULL REFERENCES dim_employee(employee_key),
    team_key          INTEGER NOT NULL REFERENCES dim_team(team_key),
    month_key         INTEGER NOT NULL,
    date_key          INTEGER NOT NULL REFERENCES dim_date(date_key),
    target_amount_usd REAL    NOT NULL CHECK (target_amount_usd > 0),
    revision_no       INTEGER NOT NULL,
    is_ramp_month     INTEGER NOT NULL,
    PRIMARY KEY (rep_key, month_key)
);

CREATE TABLE fact_team_plan (
    team_key          INTEGER NOT NULL REFERENCES dim_team(team_key),
    month_key         INTEGER NOT NULL,
    date_key          INTEGER NOT NULL REFERENCES dim_date(date_key),
    planned_headcount INTEGER NOT NULL,
    plan_amount_usd   REAL    NOT NULL,
    PRIMARY KEY (team_key, month_key)
);
