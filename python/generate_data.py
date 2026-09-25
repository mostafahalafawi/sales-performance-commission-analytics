"""Synthetic data generator for the fictional company Nexora Business Solutions.

Produces CRM/ERP-style raw extracts in data/raw/ that look like what an analyst
would actually receive: local currencies, free-text country names, target
revisions, credit notes, and a small share of deliberately injected defects.

What the generator encodes are BEHAVIOURS (rep skill spread, seasonality,
ramp-up of new hires, discounting habits, attrition). It does not encode the
conclusions: every KPI, ranking and insight is computed downstream from the
generated transactions.

Run:  python python/generate_data.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from utils import ensure_dirs, get_logger, load_config, month_range

log = get_logger("generate")

# ---------------------------------------------------------------------------
# Static reference data (fictional)
# ---------------------------------------------------------------------------
PRODUCTS = [
    # sku, name, product_line, list_price_usd, unit_cost_pct
    ("CN-FIB-100", "Business Fiber 100 Mbps (annual)", "Connectivity", 2400, 0.55),
    ("CN-FIB-1G", "Dedicated Internet 1 Gbps (annual)", "Connectivity", 14000, 0.50),
    ("CN-SDWAN", "SD-WAN Site License (annual)", "Connectivity", 3600, 0.45),
    ("CN-MPLS", "MPLS Site Link (annual)", "Connectivity", 9000, 0.55),
    ("CL-IAAS-S", "Cloud Compute Bundle - Small", "Cloud", 1800, 0.40),
    ("CL-IAAS-L", "Cloud Compute Bundle - Large", "Cloud", 7500, 0.38),
    ("CL-BKP", "Backup-as-a-Service (1 TB)", "Cloud", 1200, 0.30),
    ("CL-PROD", "Productivity Suite (10 users, annual)", "Cloud", 1500, 0.70),
    ("CY-FW", "Managed Firewall (annual)", "Cybersecurity", 6000, 0.35),
    ("CY-EDR", "Endpoint Protection (50 seats)", "Cybersecurity", 3000, 0.30),
    ("CY-SOC", "24/7 SOC Monitoring (annual)", "Cybersecurity", 18000, 0.40),
    ("MS-HELP", "Managed Helpdesk (annual)", "Managed Services", 4800, 0.45),
    ("MS-NOC", "Network Operations Centre (annual)", "Managed Services", 12000, 0.42),
    ("MS-COLO", "Colocation Rack (annual)", "Managed Services", 9600, 0.50),
    ("DV-RTR", "Enterprise Router", "Devices", 1100, 0.78),
    ("DV-SW48", "Managed Switch 48-port", "Devices", 2200, 0.80),
    ("DV-AP", "Wi-Fi 6 Access Point", "Devices", 450, 0.75),
    ("PS-IMPL", "Implementation Services (per day)", "Professional Services", 800, 0.55),
    ("PS-AUD", "Security Audit", "Professional Services", 5500, 0.45),
    ("PS-TRN", "Technical Training (per course)", "Professional Services", 1500, 0.50),
]

PRODUCT_MIX = {  # probability of each product line appearing on a deal line
    "ENT": {"Connectivity": .25, "Cloud": .20, "Cybersecurity": .20, "Managed Services": .20,
            "Devices": .05, "Professional Services": .10},
    "SME": {"Connectivity": .30, "Cloud": .30, "Cybersecurity": .10, "Managed Services": .05,
            "Devices": .20, "Professional Services": .05},
    "CHN": {"Connectivity": .15, "Cloud": .25, "Cybersecurity": .15, "Managed Services": .05,
            "Devices": .35, "Professional Services": .05},
}

# Deal-flow parameters per department (fully-ramped, skill = 1.0)
DEALS_PER_MONTH = {"ENT": 6.0, "SME": 12.0, "CHN": 8.0}
DEAL_SIZE_USD = {"ENT": 17000, "SME": 3900, "CHN": 8500}
DEAL_SIZE_SIGMA = {"ENT": 0.55, "SME": 0.45, "CHN": 0.50}
LINES_PER_DEAL = {"ENT": ([1, 2, 3, 4, 5], [.15, .30, .30, .15, .10]),
                  "SME": ([1, 2, 3], [.45, .40, .15]),
                  "CHN": ([1, 2, 3, 4], [.30, .35, .25, .10])}
BASE_DISCOUNT = {"ENT": 0.11, "SME": 0.05, "CHN": 0.17}
CREDIT_NOTE_PROB = {"ENT": 0.018, "SME": 0.022, "CHN": 0.045}
ACTUAL_RAMP = [0.30, 0.55, 0.80, 0.92, 0.97]           # realised productivity, months 1-5

# Market seasonality (what actually happens) vs. Finance's plan phasing (config)
BASE_SEASON = [0.85, 0.95, 1.00, 1.00, 1.00, 1.05, 0.92, 0.88, 1.00, 1.05, 1.10, 1.22]
GULF_SUMMER = {7: 0.85, 8: 0.80}
RAMADAN = {"2024-03": 0.86, "2024-04": 0.90, "2025-03": 0.78, "2025-04": 0.95}
MARKET_GROWTH_2025 = {"EG": 1.05, "SA": 1.10, "AE": 1.04, "OM": 1.12}
CHANNEL_SHOCK = {"team_id": "T07", "from": "2025-06", "factor": 0.85, "extra_discount": 0.03}

CITIES = {"EG": ["Cairo", "Giza", "Alexandria", "New Cairo"],
          "SA": ["Riyadh", "Jeddah", "Dammam", "Khobar"],
          "AE": ["Dubai", "Abu Dhabi", "Sharjah"],
          "OM": ["Muscat", "Sohar", "Salalah", "Nizwa"]}
INDUSTRIES = ["Logistics", "Retail", "Healthcare", "Manufacturing", "Hospitality", "Education",
              "Real Estate", "Financial Services", "Energy", "Construction", "Media", "Government Services"]
NAME_PREFIX = {"EG": ["Nile", "Delta", "Pyramid", "Lotus", "Sinai", "Horus", "Memphis", "Alex"],
               "SA": ["Najd", "Hejaz", "Asir", "Tuwaiq", "Qassim", "Dune", "Falcon", "Oasis"],
               "AE": ["Creek", "Marina", "Palm", "Emirates Bay", "Pearl", "Horizon", "Saffron", "Dhow"],
               "OM": ["Dhofar", "Batinah", "Muscat Bay", "Frankincense", "Hajar", "Sur", "Wadi", "Qurum"]}
NAME_SUFFIX = ["Group", "Holding", "Trading", "Solutions", "Co.", "Partners", "Enterprises", "Services"]
PARTNER_SUFFIX = ["IT Distribution", "Systems Integrator", "Tech Resellers", "Networks LLC", "Digital Partners"]
FIRST_NAMES = ["Ahmed", "Mohamed", "Omar", "Youssef", "Karim", "Hassan", "Mahmoud", "Tarek", "Amr", "Khaled",
               "Nour", "Salma", "Mariam", "Yasmin", "Hana", "Rana", "Laila", "Dina", "Reem", "Farah",
               "Faisal", "Saud", "Abdullah", "Fahad", "Sultan", "Nasser", "Hamad", "Rashid", "Saif", "Majid",
               "Aisha", "Noura", "Maha", "Lama", "Shahd", "Hind", "Asma", "Muna", "Sara", "Huda",
               "Ali", "Hussein", "Ibrahim", "Ziad", "Sherif", "Walid", "Adel", "Bilal", "Samir", "Rami"]
LAST_NAMES = ["Hassan", "Farouk", "Mansour", "Saleh", "Nasr", "Kamal", "Fathy", "Zaki", "Adly", "Gamal",
              "Al-Harbi", "Al-Qahtani", "Al-Otaibi", "Al-Shehri", "Al-Dosari", "Al-Mutairi", "Al-Zahrani",
              "Al-Balushi", "Al-Hinai", "Al-Rawahi", "Al-Busaidi", "Al-Kindi", "Al-Maamari",
              "Al-Mazrouei", "Al-Nuaimi", "Al-Ketbi", "Al-Suwaidi", "Haddad", "Khoury", "Sabry",
              "Rizk", "Shawky", "Osman", "Barakat", "Hamdy", "Lotfy", "Naguib", "Taha", "Wahba", "Youssef"]


@dataclass
class Rep:
    rep_id: str
    name: str
    hire_date: pd.Timestamp
    skill: float
    discount_bias: float
    trend_type: str = "stable"
    trend_start: int = 0
    trend_slope: float = 0.0
    exit_date: pd.Timestamp | None = None
    first_month_idx: int = 0            # month index of first active month (0 = before window)
    last_month_idx: int | None = None
    month_end_loader_months: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def build_products() -> pd.DataFrame:
    df = pd.DataFrame(PRODUCTS, columns=["sku", "product_name", "product_line", "list_price_usd", "unit_cost_pct"])
    return df


def build_fx(cfg: dict, months: list[pd.Period], rng: np.random.Generator) -> pd.DataFrame:
    fx_cfg = cfg["fx"]
    egp = fx_cfg["egp_path"]
    float_m = pd.Period(egp["float_month"], "M")
    post = [m for m in months if m >= float_m]
    rows = []
    for m in months:
        for ccy, rate in fx_cfg["pegged"].items():
            rows.append((str(m), ccy, rate))
        if m < float_m:
            r = egp["pre_float_rate"]
        else:
            k = post.index(m) / max(len(post) - 1, 1)
            r = egp["post_float_start"] + k * (egp["end_rate"] - egp["post_float_start"])
            r *= 1 + rng.normal(0, egp["noise_pct"])
        rows.append((str(m), "EGP", round(r, 4)))
    return pd.DataFrame(rows, columns=["fx_month", "currency", "rate_per_usd"])


class NameFactory:
    def __init__(self, rng: np.random.Generator):
        self.rng, self.used = rng, set()

    def person(self) -> str:
        for _ in range(1000):
            n = f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"
            if n not in self.used:
                self.used.add(n)
                return n
        raise RuntimeError("Name space exhausted")


def new_rep(idx: int, names: NameFactory, rng: np.random.Generator, hire_date: pd.Timestamp,
            first_idx: int, n_months: int, is_new_hire: bool) -> Rep:
    skill = float(np.exp(rng.normal(-0.04 if is_new_hire else 0.0, 0.18)))
    rep = Rep(rep_id=f"REP-{idx:03d}", name=names.person(), hire_date=hire_date, skill=skill,
              discount_bias=float(rng.normal(0, 0.025)), first_month_idx=first_idx)
    u = rng.random()
    if u < 0.12:        # performance deteriorates from some point onward
        rep.trend_type, rep.trend_slope = "declining", float(rng.uniform(-0.028, -0.016))
        rep.trend_start = int(rng.integers(max(first_idx, 5), max(first_idx + 6, n_months - 6)))
    elif u < 0.20:      # improving rep
        rep.trend_type, rep.trend_slope = "improving", float(rng.uniform(0.008, 0.015))
        rep.trend_start = int(rng.integers(first_idx, max(first_idx + 1, n_months - 8)))
    return rep


def simulate_workforce(cfg: dict, months: list[pd.Period], rng: np.random.Generator):
    """Month-by-month headcount simulation: attrition, vacancies, backfills, transfers.

    Returns reps (dict), monthly occupancy {(team_id, month_idx): [rep_id, ...]} and
    assignment history rows.
    """
    names = NameFactory(rng)
    teams = cfg["teams"]
    n = len(months)
    reps: dict[str, Rep] = {}
    counter = 1
    occupancy: dict[tuple[str, int], list[str]] = {}
    current: dict[str, list[str]] = {}
    pending: dict[str, list[int]] = {t["team_id"]: [] for t in teams}   # start month idx of backfills
    assignments: list[dict] = []
    start = months[0].to_timestamp()

    for t in teams:
        current[t["team_id"]] = []
        for _ in range(t["hc_2024"]):
            hire = start - pd.Timedelta(days=int(rng.integers(120, 6 * 365)))
            rep = new_rep(counter, names, rng, hire, 0, n, is_new_hire=False)
            reps[rep.rep_id] = rep
            current[t["team_id"]].append(rep.rep_id)
            assignments.append({"rep_id": rep.rep_id, "team_id": t["team_id"], "start": hire, "end": None})
            counter += 1
        # planned 2025 headcount growth -> hires in Jan-Apr 2025
        growth = t["hc_2025"] - t["hc_2024"]
        y25 = [i for i, m in enumerate(months) if m.year == 2025]
        for _ in range(max(growth, 0)):
            pending[t["team_id"]].append(int(rng.choice(y25[:4])))

    # four planned intra-country transfers (rep moves to a sister team)
    transfer_months = sorted(rng.choice(range(4, n - 3), size=4, replace=False).tolist())

    for mi, m in enumerate(months):
        # 1) new hires starting this month
        for t in teams:
            tid = t["team_id"]
            for _ in [s for s in pending[tid] if s == mi]:
                hire = m.to_timestamp() + pd.Timedelta(days=int(rng.integers(0, 12)))
                rep = new_rep(counter, names, rng, hire, mi, n, is_new_hire=True)
                reps[rep.rep_id] = rep
                current[tid].append(rep.rep_id)
                assignments.append({"rep_id": rep.rep_id, "team_id": tid, "start": hire, "end": None})
                counter += 1
            pending[tid] = [s for s in pending[tid] if s != mi]

        # 2) transfers
        if mi in transfer_months:
            countries = {}
            for t in teams:
                countries.setdefault(t["country"], []).append(t["team_id"])
            multi = [c for c, ts in countries.items() if len(ts) > 1]
            c = rng.choice(multi)
            src, dst = rng.choice(countries[c], size=2, replace=False)
            movers = [r for r in current[src] if reps[r].first_month_idx < mi - 3]
            if movers:
                r = str(rng.choice(movers))
                move_date = m.to_timestamp()
                current[src].remove(r)
                current[dst].append(r)
                for a in assignments:
                    if a["rep_id"] == r and a["end"] is None:
                        a["end"] = move_date - pd.Timedelta(days=1)
                assignments.append({"rep_id": r, "team_id": dst, "start": move_date, "end": None})
                pending[src].append(mi + int(rng.integers(1, 4)))

        # 3) record occupancy (who is active this month)
        for t in teams:
            occupancy[(t["team_id"], mi)] = list(current[t["team_id"]])

        # 4) attrition at month end (rep is active this month, gone from next)
        for t in teams:
            tid = t["team_id"]
            for r in list(current[tid]):
                if mi < n - 1 and rng.random() < 0.011:
                    rep = reps[r]
                    rep.exit_date = m.to_timestamp() + pd.Timedelta(days=int(rng.integers(15, 27)))
                    rep.last_month_idx = mi
                    current[tid].remove(r)
                    for a in assignments:
                        if a["rep_id"] == r and a["end"] is None:
                            a["end"] = rep.exit_date
                    pending[tid].append(mi + 1 + int(rng.integers(1, 4)))   # 1-3 month vacancy

    # month-end loaders: a handful of tenured reps who pull deals into month-end
    tenured = [r for r in reps.values() if r.first_month_idx == 0 and (r.last_month_idx or n) > 14]
    for rep in rng.choice(tenured, size=4, replace=False):
        rep.month_end_loader_months = sorted(rng.choice(range(2, n - 2), size=3, replace=False).tolist())
    return reps, occupancy, assignments


def build_customers(cfg: dict, rng: np.random.Generator) -> pd.DataFrame:
    pool_size = {"ENT": 70, "SME": 260, "CHN": 45}
    rows, used, cid = [], set(), 1
    for t in cfg["teams"]:
        c, dept = t["country"], t["department"]
        size = pool_size[dept] if c != "OM" else 150
        for _ in range(size):
            for _ in range(200):
                if dept == "CHN":
                    name = f"{rng.choice(NAME_PREFIX[c])} {rng.choice(PARTNER_SUFFIX)}"
                else:
                    name = f"{rng.choice(NAME_PREFIX[c])} {rng.choice(INDUSTRIES)} {rng.choice(NAME_SUFFIX)}"
                if name not in used:
                    break
                name = f"{name} {cid}"
            used.add(name)
            rows.append({"customer_code": f"CUST-{cid:05d}", "customer_name": name, "country_code": c,
                         "customer_segment": {"ENT": "Enterprise", "SME": "SME", "CHN": "Partner"}[dept],
                         "industry": "IT Channel" if dept == "CHN" else str(rng.choice(INDUSTRIES)),
                         "city": str(rng.choice(CITIES[c])), "owning_team_id": t["team_id"],
                         "created_date": (pd.Timestamp("2019-01-01") +
                                          pd.Timedelta(days=int(rng.integers(0, 1800)))).date()})
            cid += 1
    return pd.DataFrame(rows)


def actual_season(country: str, m: pd.Period) -> float:
    f = BASE_SEASON[m.month - 1]
    if country in ("SA", "AE", "OM"):
        f *= GULF_SUMMER.get(m.month, 1.0)
    ram = RAMADAN.get(str(m), 1.0)
    f *= ram if country != "EG" else ram ** 0.5
    if m.year == 2025:
        f *= MARKET_GROWTH_2025[country]
    return f


def quota_for(cfg: dict, dept: str, country: str, m: pd.Period) -> float:
    q = cfg["quota"]
    return (q["base_monthly_usd"][dept] * q["country_factor"][country]
            * q["plan_phasing"][m.month - 1] * q["annual_uplift"][m.year])


# ---------------------------------------------------------------------------
# Sales simulation
# ---------------------------------------------------------------------------
def simulate_sales(cfg, months, reps, occupancy, customers, products, rng):
    team_by_id = {t["team_id"]: t for t in cfg["teams"]}
    cust_by_team = customers.groupby("owning_team_id")["customer_code"].apply(list).to_dict()
    prod_by_line = {pl: g.sort_values("list_price_usd") for pl, g in products.groupby("product_line")}
    end_date = pd.Timestamp(cfg["project"]["end_date"])
    lines, deals_meta = [], []
    inv_no, line_no = 1, 1

    for (tid, mi), rep_ids in occupancy.items():
        team = team_by_id[tid]
        dept, c = team["department"], team["country"]
        m = months[mi]
        season = actual_season(c, m)
        shock = CHANNEL_SHOCK["factor"] if (tid == CHANNEL_SHOCK["team_id"] and m >= pd.Period(CHANNEL_SHOCK["from"], "M")) else 1.0
        extra_disc = CHANNEL_SHOCK["extra_discount"] if shock < 1 else 0.0
        days_in_month = m.days_in_month
        for r in rep_ids:
            rep = reps[r]
            tenure = mi - rep.first_month_idx if rep.first_month_idx > 0 else 99
            ramp = ACTUAL_RAMP[tenure] if tenure < len(ACTUAL_RAMP) else 1.0
            trend = 1.0
            if rep.trend_type != "stable" and mi >= rep.trend_start:
                trend = float(np.clip(1 + rep.trend_slope * (mi - rep.trend_start + 1), 0.45, 1.45))
            pre_exit = 0.80 if (rep.last_month_idx is not None and rep.last_month_idx - mi < 3) else 1.0
            noise = rng.gamma(20, 1 / 20)
            lam = DEALS_PER_MONTH[dept] * rep.skill ** 0.7 * season * shock * trend * ramp * pre_exit * noise
            n_deals = rng.poisson(lam)
            deal_specs = [("normal", 1.0)] * n_deals
            if mi in rep.month_end_loader_months:
                deal_specs += [("loaded", 2.5)] * int(rng.integers(2, 4))
            mu_size = DEAL_SIZE_USD[dept] * {"EG": .62, "SA": 1.25, "AE": 1.15, "OM": .85}[c] * rep.skill ** 0.3
            sigma = DEAL_SIZE_SIGMA[dept]
            for kind, size_mult in deal_specs:
                value = rng.lognormal(np.log(mu_size) - sigma ** 2 / 2, sigma) * size_mult
                if kind == "loaded":
                    day = int(rng.integers(days_in_month - 1, days_in_month + 1))
                elif rng.random() < 0.25:
                    day = int(rng.integers(days_in_month - 4, days_in_month + 1))
                else:
                    day = int(rng.integers(1, days_in_month + 1))
                inv_date = m.to_timestamp() + pd.Timedelta(days=day - 1)
                if rep.exit_date is not None and inv_date > rep.exit_date:
                    inv_date = rep.exit_date
                if inv_date < rep.hire_date:
                    inv_date = rep.hire_date
                month_end = day > days_in_month - 3
                customer = str(rng.choice(cust_by_team[tid]))
                ccy = "USD" if (c in ("SA", "AE") and dept == "ENT" and rng.random() < 0.06) else cfg["countries"][c]["currency"]
                k_opts, k_p = LINES_PER_DEAL[dept]
                k = int(rng.choice(k_opts, p=k_p))
                shares = rng.dirichlet(np.ones(k))
                mix = PRODUCT_MIX[dept]
                inv_id = f"INV-{inv_date.year}-{inv_no:06d}"
                inv_no += 1
                for s in shares:
                    pl = str(rng.choice(list(mix.keys()), p=list(mix.values())))
                    cand = prod_by_line[pl]
                    disc = float(np.clip(BASE_DISCOUNT[dept] + rep.discount_bias + extra_disc
                                         + rng.normal(0, 0.02) + (0.02 if month_end else 0), 0, 0.45))
                    line_net = s * value
                    gross_target = line_net / (1 - disc)
                    ok = cand[cand["list_price_usd"] <= gross_target * 1.2]
                    prod = ok.sample(1, random_state=int(rng.integers(1e9))).iloc[0] if len(ok) else cand.iloc[0]
                    qty = max(1, int(round(gross_target / prod["list_price_usd"])))
                    lines.append({"line_id": line_no, "document_no": inv_id, "doc_type": "INV",
                                  "original_document_no": None, "document_date": inv_date,
                                  "rep_code": r, "customer_code": customer, "country_code": c,
                                  "sku": prod["sku"], "quantity": qty, "list_price_usd": float(prod["list_price_usd"]),
                                  "discount_pct": round(disc, 4), "currency": ccy})
                    line_no += 1
                deals_meta.append({"document_no": inv_id, "date": inv_date, "dept": dept, "kind": kind})

    inv = pd.DataFrame(lines)

    # ---- credit notes (returns / cancellations) --------------------------------
    cn_rows, cn_no = [], 1
    by_doc = inv.groupby("document_no")
    for d in deals_meta:
        if d["kind"] == "loaded":
            p, full, lag = 1.0, True, int(rng.integers(20, 46))
        else:
            p = CREDIT_NOTE_PROB[d["dept"]]
            full, lag = rng.random() < 0.6, int(rng.integers(5, 151))
        if rng.random() >= p:
            continue
        cn_date = d["date"] + pd.Timedelta(days=lag)
        if cn_date > end_date:
            continue
        src = by_doc.get_group(d["document_no"])
        if not full:
            src = src.sample(max(1, len(src) // 2), random_state=int(rng.integers(1e9)))
        cn_id = f"CN-{cn_date.year}-{cn_no:05d}"
        cn_no += 1
        for _, row in src.iterrows():
            cn_rows.append({**row.to_dict(), "line_id": line_no, "document_no": cn_id, "doc_type": "CN",
                            "original_document_no": d["document_no"], "document_date": cn_date,
                            "quantity": -int(row["quantity"])})
            line_no += 1
    cn = pd.DataFrame(cn_rows)
    return pd.concat([inv, cn], ignore_index=True).sort_values(["document_date", "line_id"]).reset_index(drop=True)


def build_targets(cfg, months, reps, occupancy):
    team_by_id = {t["team_id"]: t for t in cfg["teams"]}
    ramp = cfg["quota"]["ramp_factors"]
    rows = []
    for (tid, mi), rep_ids in occupancy.items():
        t = team_by_id[tid]
        m = months[mi]
        for r in rep_ids:
            tenure = mi - reps[r].first_month_idx if reps[r].first_month_idx > 0 else 99
            rf = ramp[tenure] if tenure < len(ramp) else 1.0
            rows.append({"target_month": str(m), "rep_code": r, "team_id": tid,
                         "target_amount_usd": round(quota_for(cfg, t["department"], t["country"], m) * rf, 2)})
    return pd.DataFrame(rows)


def build_team_plan(cfg, months):
    rows = []
    for t in cfg["teams"]:
        for m in months:
            hc = t[f"hc_{m.year}"]
            rows.append({"team_id": t["team_id"], "plan_month": str(m), "planned_headcount": hc,
                         "plan_amount_usd": round(hc * quota_for(cfg, t["department"], t["country"], m), 2)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Raw formatting + defect injection
# ---------------------------------------------------------------------------
COUNTRY_VARIANTS = {"EG": ["egypt", "EGY", "Egypt "], "SA": ["KSA", "Saudi", "saudi arabia"],
                    "AE": ["U.A.E", "United Arab Emirates", "uae"], "OM": ["Sultanate of Oman", "oman"]}


def to_raw_invoices(cfg, sales, fx):
    fx_map = fx.set_index(["fx_month", "currency"])["rate_per_usd"].to_dict()
    df = sales.copy()
    df["fx"] = [fx_map[(d.strftime("%Y-%m"), c)] for d, c in zip(df["document_date"], df["currency"])]
    df["unit_list_price_local"] = (df["list_price_usd"] * df["fx"]).round(2)
    df["net_amount_local"] = (df["quantity"] * df["unit_list_price_local"] * (1 - df["discount_pct"])).round(2)
    df["country"] = df["country_code"].map(lambda c: cfg["countries"][c]["name"])
    df["document_date"] = df["document_date"].dt.strftime("%Y-%m-%d")
    df["line_id"] = df["line_id"].map(lambda x: f"L{x:07d}")
    df["exported_at"] = "2026-01-05 06:00:00"
    cols = ["line_id", "document_no", "doc_type", "original_document_no", "document_date", "rep_code",
            "customer_code", "country", "sku", "quantity", "unit_list_price_local", "discount_pct",
            "net_amount_local", "currency", "exported_at"]
    return df[cols]


def inject_defects(cfg, raw, targets, rng):
    d = cfg["defects"]
    n = len(raw)
    manifest = {}
    raw = raw.copy()
    raw["quantity"] = raw["quantity"].astype(float)

    def pick(rate, mask=None):
        pool = raw.index if mask is None else raw.index[mask]
        k = max(1, int(round(len(pool) * rate)))
        return rng.choice(pool, size=min(k, len(pool)), replace=False)

    inv_mask = (raw["doc_type"] == "INV").to_numpy()

    idx = pick(d["alt_date_format_rate"])
    raw.loc[idx, "document_date"] = pd.to_datetime(raw.loc[idx, "document_date"]).dt.strftime("%d/%m/%Y")
    manifest["alt_date_format"] = len(idx)

    idx = pick(d["country_variant_rate"])
    code_by_name = {v["name"]: k for k, v in cfg["countries"].items()}
    raw.loc[idx, "country"] = [str(rng.choice(COUNTRY_VARIANTS[code_by_name[c]])) for c in raw.loc[idx, "country"]]
    manifest["country_variant"] = len(idx)

    idx = pick(d["sku_format_rate"])
    raw.loc[idx, "sku"] = [f" {s.lower()}" if rng.random() < .5 else f"{s.lower()} " for s in raw.loc[idx, "sku"]]
    manifest["sku_format"] = len(idx)

    idx = pick(d["negative_qty_on_invoice_rate"], inv_mask)
    raw.loc[idx, ["quantity", "net_amount_local"]] *= -1
    manifest["negative_qty_on_invoice"] = len(idx)

    idx = pick(d["missing_rep_rate"])
    raw.loc[idx, "rep_code"] = None
    manifest["missing_rep"] = len(idx)

    idx = pick(d["future_date_rate"])
    raw.loc[idx, "document_date"] = [f"2026-{int(rng.integers(3, 12)):02d}-{int(rng.integers(1, 28)):02d}" for _ in idx]
    manifest["future_date"] = len(idx)

    idx = pick(d["invalid_currency_rate"])
    raw.loc[idx, "currency"] = [str(rng.choice(["", "XXX", "EUR?"])) for _ in idx]
    manifest["invalid_currency"] = len(idx)

    idx = pick(d["zero_price_rate"], inv_mask)
    raw.loc[idx, ["unit_list_price_local", "net_amount_local"]] = 0.0
    manifest["zero_price"] = len(idx)

    idx = pick(d["orphan_sku_rate"])
    raw.loc[idx, "sku"] = [str(rng.choice(["CY-XDR", "CL-GPU", "DV-OLD-01"])) for _ in idx]
    manifest["orphan_sku"] = len(idx)

    # exact duplicate rows (same line_id) - typical double export
    idx = pick(d["duplicate_line_rate"])
    raw = pd.concat([raw, raw.loc[idx]], ignore_index=True)
    manifest["duplicate_lines"] = len(idx)
    raw = raw.sample(frac=1, random_state=int(rng.integers(1e9))).reset_index(drop=True)

    # ---- targets: revisions (keep latest) and gaps --------------------------------
    t = targets.copy()
    t["revision_no"] = 1
    t["revised_at"] = pd.to_datetime(t["target_month"]).dt.to_period("M").dt.start_time - pd.Timedelta(days=10)
    rev_idx = rng.choice(t.index, size=int(len(t) * d["target_revision_rate"]), replace=False)
    first = t.loc[rev_idx].copy()
    first["target_amount_usd"] = (first["target_amount_usd"] * rng.uniform(0.9, 1.1, len(first))).round(2)
    first["revision_no"] = 1
    first["revised_at"] = first["revised_at"] - pd.Timedelta(days=20)
    t.loc[rev_idx, "revision_no"] = 2           # current row becomes the latest revision
    t = pd.concat([t, first], ignore_index=True)
    manifest["target_revisions"] = len(rev_idx)
    drop_idx = rng.choice(t.index[t["revision_no"] == 1], size=int(len(targets) * d["missing_target_rate"]), replace=False)
    dropped = t.loc[drop_idx, ["target_month", "rep_code"]]
    # only drop rep-months that do not also have a revision row
    dropped = dropped[~dropped.set_index(["target_month", "rep_code"]).index.isin(
        t.loc[t["revision_no"] == 2].set_index(["target_month", "rep_code"]).index)]
    t = t.drop(index=dropped.index)
    manifest["missing_targets"] = len(dropped)
    t["revised_at"] = t["revised_at"].dt.strftime("%Y-%m-%d")
    t = t.drop(columns=["team_id"]).sort_values(["target_month", "rep_code", "revision_no"])
    manifest["raw_invoice_rows"] = int(len(raw))
    manifest["clean_generated_rows"] = int(n)
    return raw, t, manifest


# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    rng = np.random.default_rng(cfg["project"]["seed"])
    months = month_range(cfg["project"]["start_date"], cfg["project"]["end_date"])
    raw_dir = cfg["paths"]["raw"]

    log.info("Simulating workforce for %d teams over %d months", len(cfg["teams"]), len(months))
    reps, occupancy, assignments = simulate_workforce(cfg, months, rng)
    products = build_products()
    fx = build_fx(cfg, months, rng)
    customers = build_customers(cfg, rng)

    log.info("Simulating deal flow ...")
    sales = simulate_sales(cfg, months, reps, occupancy, customers, products, rng)
    targets = build_targets(cfg, months, reps, occupancy)
    team_plan = build_team_plan(cfg, months)

    raw_inv = to_raw_invoices(cfg, sales, fx)
    raw_inv, raw_targets, manifest = inject_defects(cfg, raw_inv, targets, rng)

    # employees (reps + managers)
    managers = []
    names = NameFactory(np.random.default_rng(cfg["project"]["seed"] + 7))
    for i, t in enumerate(cfg["teams"], start=1):
        managers.append({"employee_code": f"MGR-{i:02d}", "full_name": names.person(), "role": "Sales Manager",
                         "hire_date": (pd.Timestamp("2016-01-01") + pd.Timedelta(days=int(rng.integers(0, 2000)))).date(),
                         "exit_date": None, "home_country": t["country"], "managed_team_id": t["team_id"]})
    emp = [{"employee_code": r.rep_id, "full_name": r.name, "role": "Sales Representative",
            "hire_date": r.hire_date.date(), "exit_date": r.exit_date.date() if r.exit_date is not None else None,
            "home_country": None, "managed_team_id": None} for r in reps.values()]
    employees = pd.DataFrame(managers + emp)
    asg = pd.DataFrame(assignments).rename(columns={"rep_id": "rep_code", "start": "start_date", "end": "end_date"})
    asg["start_date"] = pd.to_datetime(asg["start_date"]).dt.date
    asg["end_date"] = pd.to_datetime(asg["end_date"]).dt.date

    teams = pd.DataFrame(cfg["teams"]).rename(columns={"country": "country_code", "department": "department_code"})
    teams["manager_code"] = [f"MGR-{i:02d}" for i in range(1, len(teams) + 1)]
    plan_rows = []
    for p in cfg["commission_plans"]:
        for i, tier in enumerate(p["tiers"], start=1):
            plan_rows.append({"plan_id": p["plan_id"], "plan_name": p["plan_name"], "effective_from": p["effective_from"],
                              "effective_to": p["effective_to"], "tier_order": i, "tier_name": tier["tier"],
                              "min_achievement": tier["min"], "max_achievement": tier["max"], "commission_rate": tier["rate"],
                              "strategic_weight": p["strategic_weight"], "cap_multiple_of_target": p["cap_multiple_of_target"],
                              "clawback_window_days": p["clawback_window_days"]})

    outputs = {
        "raw_sales_lines.csv": raw_inv, "raw_sales_targets.csv": raw_targets, "raw_team_plan.csv": team_plan,
        "raw_employees.csv": employees, "raw_rep_assignments.csv": asg, "raw_teams.csv": teams,
        "raw_customers.csv": customers, "raw_products.csv": products, "raw_fx_rates.csv": fx,
        "raw_commission_plans.csv": pd.DataFrame(plan_rows),
    }
    for name, df in outputs.items():
        df.to_csv(raw_dir / name, index=False)
        log.info("wrote %-28s %7d rows", name, len(df))
    # Manifest is used ONLY by tests to confirm the validator catches injected defects.
    (raw_dir / "_defect_manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("Generation complete: %d reps, %d customers, %d raw sales lines",
             len(reps), len(customers), len(raw_inv))


if __name__ == "__main__":
    main()
