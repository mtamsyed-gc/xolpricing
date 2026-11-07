"""Single source of truth for the Control workbook (docs/data-contracts.md §4).

Used by tools/excel/build_control.py (writes the workbook) and by the stage-00 workflow builder (validation rules).
Every table sits on its own sheet: title in row 1, instructions in row 2, header in row HEADER_ROW, data below.
All Params values are stored as text (ISO dates, plain decimals) so the tool reads one consistent type.
"""
from __future__ import annotations

HEADER_ROW = 4
VERSION = "0.2"

# --------------------------------------------------------------------------------------------- Params
# (param_id, group, unit, kind, allowed, required, description)
#   kind: TEXT | NUMBER | INT | DATE | LIST | CSV
PARAMS = [
    ("cedant", "General", "", "TEXT", "", True, "Cedant name (first row: keeps the value column text-typed)"),
    ("country", "General", "", "TEXT", "", True, "Primary country of cover"),
    ("line", "General", "", "TEXT", "", True, "Line of business"),
    ("quote_ccy", "General", "ISO", "TEXT", "", True, "Quote currency; tbl FX rates are to this currency"),
    ("fx_quote_to_usd", "General", "rate", "NUMBER", "", True, "1 unit of quote currency in USD"),
    ("treaty_start", "General", "date", "DATE", "", True, "Treaty / risk start date (yyyy-mm-dd)"),
    ("quote_year", "General", "year", "INT", "", True, "Treaty year being priced"),
    ("run_label", "General", "", "TEXT", "", False, "Free label carried into the run log"),
    ("scenario_id", "General", "", "INT", "", False,
     "Sensitivity run: apply the Scenarios rows with this id (blank or 0 = best estimate)"),
    ("source_mode", "Mode", "", "LIST", "L,C,E", True, "L = standalone line data, C = claimant listing, E = ETE extension"),
    ("premium_basis", "Mode", "", "LIST", "GROSS,GROSSNET,NET", True, "Premium basis for rates and EPI"),
    ("segment_field", "Mode", "", "TEXT", "", False, "Segmentation field (ETE: dim_life attribute)"),
    ("ete_tpa_filter", "Mode", "", "TEXT", "", False, "ETE only: comma list of TPAs to include (blank = all)"),
    ("ete_payer_filter", "Mode", "", "TEXT", "", False,
     "ETE / Merit only: comma list of payers (Payer Name) to include (blank = all; A16 flags several payers)"),
    ("treaty_basis", "Basis", "", "LIST", "RA,LO", True, "Risks attaching or losses occurring"),
    ("claims_trigger", "Basis", "", "LIST", "INCURRED,PAID", True, "Claims date trigger"),
    ("paid_runoff_months", "Basis", "months", "INT", "", False, "Paid trigger only: months after treaty year end"),
    ("pp_accumulation", "Basis", "", "LIST", "POLICY_YEAR,TREATY_YEAR", True, "Per-person accumulation period"),
    ("epi_quote_year", "Exposure", "quote ccy", "NUMBER", "", True, "Expected premium income for the quote year"),
    ("expected_lives_quote_year", "Exposure", "lives", "NUMBER", "", False, "Blank = EPI / trended average premium"),
    ("years_included", "Data", "years", "CSV", "", True, "Experience years used (comma list, 2-5 years)"),
    ("output_threshold_pct_min_ap", "Data", "%", "NUMBER", "", True, "Write claimant rows above this % of the lowest AP"),
    ("policy_limit", "Data", "quote ccy", "NUMBER", "", False, "Overall per-person policy limit (blank = none)"),
    ("default_benefit_limit", "Data", "quote ccy", "NUMBER", "", False,
     "Benefit limit for a life with none in the data or on the Limits sheet (blank = no limit)"),
    ("outlier_method", "Outliers", "", "LIST", "VALUE,IQR,NONE", True, "Outlier threshold rule"),
    ("outlier_value", "Outliers", "quote ccy", "NUMBER", "", False, "Threshold when outlier_method = VALUE"),
    ("iqr_multiplier", "Outliers", "x", "NUMBER", "", False, "Multiplier when outlier_method = IQR"),
    ("full_cred_n", "Credibility", "claims", "NUMBER", "", True, "Claims for full credibility (workshop default 64)"),
    ("complement_source", "Credibility", "", "LIST", "STOCHASTIC,BENCHMARK,EXPIRING", True, "Credibility complement"),
    ("benchmark_rate", "Credibility", "quote ccy pppy", "NUMBER", "", False, "When complement_source = BENCHMARK"),
    ("incidence_adj", "Incidence", "x", "NUMBER", "", True, "Overall incidence adjustment (1 = none); justify"),
    ("qs_cedant_retention_pct", "QS", "%", "NUMBER", "", True, "Cedant retention under an existing QS (1 = no QS)"),
    ("evaluate_mode", "Programme", "", "LIST", "STANDALONE,PROGRAMME", True, "Options standalone, or stacked per Programme"),
    ("iterations", "Stochastic", "n", "INT", "", True, "Simulation iterations"),
    ("seed", "Stochastic", "", "INT", "", True, "Random seed (recorded in the run log)"),
    ("fit_cutoff", "Stochastic", "quote ccy", "NUMBER", "", False, "Severity fitting cut-off (blank = AP)"),
    ("fit_candidates", "Stochastic", "", "CSV", "", True, "Candidate severity distributions"),
    ("below_cutoff", "Stochastic", "", "LIST", "STOP,EMPIRICAL,IGNORE", True,
     "When fit_cutoff is above a layer's attachment: STOP the run; EMPIRICAL = simulate the claims between the AP and "
     "the cut-off from the data's own claims (spliced with the fitted tail); IGNORE = leave them out, with a flag"),
    ("fit_selected", "Stochastic", "", "LIST", "LOGNORMAL,GAMMA,WEIBULL,PARETO,GPD", False,
     "Severity distribution used by the simulation (blank = lowest AIC in stage 04a); justify an override"),
    ("severity_source", "Stochastic", "", "LIST", "PYTHON,NATIVE", True,
     "PYTHON: the distribution selected in stage 04a (candidate fit, default); NATIVE: a single-parameter Pareto fitted in stage 04 (no candidate fit)"),
    ("freq_model", "Stochastic", "", "LIST", "POISSON,NEGBIN", True, "Frequency distribution"),
    ("ev_freq_source", "Stochastic", "", "LIST", "EXPERIENCE,SCENARIOS,BOTH", True,
     "Per-event simulation: historical events, EvScenarios (with a return period), or both"),
    ("sl_attritional_threshold", "Stochastic", "quote ccy", "NUMBER", "", True, "Stop loss: attritional / large split"),
    ("sl_cov_floor", "Stochastic", "", "NUMBER", "", True, "Stop loss: minimum CoV of the attritional cost per life"),
    ("sl_attritional_dist", "Stochastic", "", "LIST", "LOGNORMAL,NORMAL", True,
     "Stop loss: distribution of the attritional cost per life"),
    ("net_claims_budget", "Cedant impact", "quote ccy", "NUMBER", "", False, "Blank = best-estimate gross claims"),
    ("reference_lives", "Cedant impact", "lives", "NUMBER", "", False, "Blank = expected lives for the quote year"),
    ("return_periods", "Cedant impact", "years", "CSV", "", True, "Return periods shown in Exhibit C2"),
    ("currency_display", "Cedant impact", "", "LIST", "QUOTE,USD", True, "Currency of the client exhibits"),
]
PARAM_KIND = {p[0]: p[3] for p in PARAMS}
PARAM_ALLOWED = {p[0]: p[4] for p in PARAMS if p[4]}
PARAM_REQUIRED = [p[0] for p in PARAMS if p[5]]

DEFAULTS = {
    "premium_basis": "GROSS", "segment_field": "segment", "treaty_basis": "RA", "claims_trigger": "INCURRED",
    "pp_accumulation": "POLICY_YEAR", "output_threshold_pct_min_ap": "0.5", "outlier_method": "IQR",
    "iqr_multiplier": "1.5", "full_cred_n": "64", "complement_source": "STOCHASTIC", "incidence_adj": "1",
    "qs_cedant_retention_pct": "1", "evaluate_mode": "STANDALONE", "iterations": "10000", "seed": "12345",
    "fit_candidates": "LOGNORMAL,GAMMA,WEIBULL,PARETO,GPD", "below_cutoff": "STOP", "freq_model": "POISSON", "severity_source": "PYTHON",
    "sl_attritional_threshold": "100000", "sl_cov_floor": "0.05", "sl_attritional_dist": "LOGNORMAL", "ev_freq_source": "BOTH",
    "return_periods": "5,50,250",
    "currency_display": "QUOTE", "source_mode": "L",
}

# --------------------------------------------------------------------------------------------- tables
# sheet -> (title, instructions, [(column, kind, allowed, required)])
TABLES = {
    "Params": ("Run parameters",
               "Enter values as text: dates yyyy-mm-dd, percentages as decimals (0.08). Justify every judgement input.",
               [("param_id", "TEXT", "", True), ("group", "TEXT", "", False), ("value", "TEXT", "", False),
                ("unit", "TEXT", "", False), ("allowed_values", "TEXT", "", False), ("description", "TEXT", "", False),
                ("justification", "TEXT", "", False)]),
    "FX": ("Priced exchange rates",
           "Units of quote currency per 1 unit of each transaction currency. Claims are converted back to original "
           "currency and re-converted at these rates (as-if).",
           [("currency", "TEXT", "", True), ("priced_rate_to_quote_ccy", "NUMBER", "", True),
            ("rate_date", "DATE", "", False)]),
    "Limits": ("Benefit limits by plan", "Used when the data carries no annual_limit. Quote currency.",
               [("plan", "TEXT", "", True), ("annual_limit", "NUMBER", "", True), ("sub_limit_notes", "TEXT", "", False)]),
    "Trend": ("FGU claims trend and premium trend",
              "Annual trend for calendar year = w_client x client + w_market x market + w_infl x inflation + residual. "
              "Either weight the components or put the basis in residual_pct (others 0). one_off_adj multiplies the "
              "cumulative trend from that year on.",
              [("year", "INT", "", True), ("client_pct", "NUMBER", "", False), ("market_pct", "NUMBER", "", False),
               ("inflation_pct", "NUMBER", "", False), ("residual_pct", "NUMBER", "", False),
               ("weight_client", "NUMBER", "", False), ("weight_market", "NUMBER", "", False),
               ("weight_inflation", "NUMBER", "", False), ("one_off_adj", "NUMBER", "", False),
               ("large_claim_trend_pct", "NUMBER", "", False), ("premium_trend_pct", "NUMBER", "", False)]),
    "Completion": ("Completion: IBNR, unearned, unwritten gross-ups",
                   "CAVEAT: portfolio-level development factors are driven by high-frequency, low-severity claims and "
                   "may UNDERSTATE development above an attachment point (later reporting, case-estimate movement, "
                   "slower settlement). Prefer large-claim development or a case review; state the basis. "
                   "ATTRITIONAL: completion of total claims below the stop-loss large threshold (aggregate "
                   "development, unearned and unwritten), used by the stop-loss attritional model only. "
                   "indicated_factor is written by the tool (reference only).",
                   [("year", "INT", "", True), ("component", "LIST", "IBNR,UNEARNED,UNWRITTEN", True),
                    ("side", "LIST", "PRE_AP,POST_AP_AMOUNT,POST_AP_COUNT,ATTRITIONAL", True),
                    ("ap_band_from", "NUMBER", "", True), ("selected_factor", "NUMBER", "", True),
                    ("indicated_factor", "NUMBER", "", False), ("justification", "TEXT", "", False)]),
    "Weights": ("Experience-year weights",
                "months_complete drives the default weights (2-5 years). Custom weights need a justification and "
                "must sum to 100% over the years used.",
                [("year", "INT", "", True), ("months_complete", "NUMBER", "", True), ("use_default", "INT", "0,1", True),
                 ("custom_weight", "NUMBER", "", False), ("justification", "TEXT", "", False)]),
    "Layers": ("Layer options",
               "Up to 4 options per cover. PP = per person, EV = per event, SL = aggregate stop loss. "
               "limit_basis TO_BENEFIT_LIMIT runs the layer up to each life's benefit limit.",
               [("cover", "LIST", "PP,EV,SL", True), ("option_id", "INT", "", True), ("layer", "INT", "1,2", True),
                ("label", "TEXT", "", False), ("limit_basis", "LIST", "AMOUNT,TO_BENEFIT_LIMIT", False),
                ("ap", "NUMBER", "", False), ("limit", "NUMBER", "", False), ("aal", "NUMBER", "", False),
                ("aad", "NUMBER", "", False), ("co_participation_pct", "NUMBER", "", False),
                ("reinstatements_n", "INT", "", False), ("reinstatement_cost_pct", "NUMBER", "", False),
                ("reinst_pro_rata_time", "INT", "0,1", False), ("sl_claims_basis", "LIST", "GROSS,NET_OF_XOL", False),
                ("sl_measure", "LIST", "PCT_BUDGET,PCT_EPI,AMOUNT", False), ("sl_ap", "NUMBER", "", False),
                ("sl_limit", "NUMBER", "", False), ("active", "INT", "0,1", True)]),
    "Programme": ("Programme order", "Order of application when evaluate_mode = PROGRAMME.",
                  [("step", "INT", "", True), ("cover", "LIST", "QS,PP,EV,SL", True), ("option_id", "INT", "", False),
                   ("active", "INT", "0,1", True)]),
    "EventRule": ("Per-event definition",
                  "SUPPLIED uses event_id from the data only. CLUSTER also groups accident-coded claim lines "
                  "(cause_code present, no event_id) of the same group key and cause whose admission (else treatment) "
                  "dates fall within window_days of the cluster's first date. Events need at least min_lives lives.",
                  [("id_source", "LIST", "SUPPLIED,CLUSTER", True), ("window_days", "INT", "", True),
                   ("group_key", "LIST", "SCHEME,SITE,CAUSE,SCHEME+SITE,SCHEME+CAUSE", True),
                   ("min_lives", "INT", "", True)]),
    "EvScenarios": ("Per-event scenarios",
                    "Lives x cost per life (quote-year money). Shown deterministically against every EV option; with "
                    "a return period they are also simulated (Poisson, 1 / return period) when ev_freq_source is "
                    "SCENARIOS or BOTH.",
                    [("scenario_name", "TEXT", "", True), ("lives", "NUMBER", "", True),
                     ("cost_per_life", "NUMBER", "", True), ("return_period_yrs", "NUMBER", "", False),
                     ("site", "TEXT", "", False)]),
    "Loadings": ("High-level loadings (reinsurer view, for the indicative RI cost)",
                 "Premium = (E[L] + k x SD) / (1 - expenses - brokerage - profit - premium tax).",
                 [("cover", "LIST", "PP,EV,SL", True), ("reinsurer_expense_pct", "NUMBER", "", True),
                  ("brokerage_pct", "NUMBER", "", True), ("profit_margin_pct", "NUMBER", "", True),
                  ("premium_tax_pct", "NUMBER", "", True), ("risk_load_k", "NUMBER", "", True)]),
    "Expiring": ("Expiring structure", "For comparison: expiring rate trended by leveraged trend.",
                 [("cover", "LIST", "PP,EV,SL", True), ("layer", "INT", "", True), ("ap", "NUMBER", "", False),
                  ("limit", "NUMBER", "", False), ("rate_basis", "LIST", "PPPY,PCT_EPI,FLAT", False),
                  ("rate", "NUMBER", "", False), ("premium", "NUMBER", "", False),
                  ("recoveries_by_year", "TEXT", "", False)]),
    "Scenarios": ("Sensitivity scenarios",
                  "Scenario 0 is the best estimate (implicit). Set Params scenario_id to run one scenario. Each row "
                  "overrides one numeric column: SET replaces, MULT multiplies (> 0), ADD adds; applied in that order. "
                  "target_key: ALL, or column=value (Params: param_id=<id>, column value).",
                  [("scenario_id", "INT", "", True), ("scenario_name", "TEXT", "", True),
                   ("target_table", "LIST", "Trend,Completion,Weights,Params,FX,Layers,EvScenarios", True),
                   ("target_key", "TEXT", "", True),
                   ("target_column", "TEXT", "", True), ("operation", "LIST", "SET,ADD,MULT", True),
                   ("override_value", "TEXT", "", True)]),
}
SHEETS = list(TABLES)


# --------------------------------------------------------------------------------------------- user help (0.8.0)
# Examples for the Params sheet (shown in the cell comment, the Guide sheet and the browser page). A LIST param's
# options come from PARAMS; an example is only given where the format is not obvious from the description.
PARAM_EXAMPLES = {
    "cedant": "Gulf Health Insurance Co", "country": "UAE", "line": "Group medical", "quote_ccy": "AED",
    "fx_quote_to_usd": "0.272294", "treaty_start": "2026-01-01", "quote_year": "2026",
    "run_label": "2026 renewal, base case", "scenario_id": "blank (best estimate) or 1",
    "segment_field": "segment", "ete_tpa_filter": "NAS,NEXTCARE (blank = all)", "ete_payer_filter": "Payer A",
    "paid_runoff_months": "12", "epi_quote_year": "85000000", "expected_lives_quote_year": "blank or 8500",
    "years_included": "2021,2022,2023,2024,2025", "output_threshold_pct_min_ap": "0.5 (= 50% of the lowest AP)",
    "policy_limit": "blank or 2000000", "default_benefit_limit": "blank or 1000000", "outlier_value": "2500000",
    "iqr_multiplier": "1.5", "full_cred_n": "64", "benchmark_rate": "120 (quote ccy per person per year)",
    "incidence_adj": "1 (none) or 0.95", "qs_cedant_retention_pct": "1 (no QS) or 0.7",
    "iterations": "10000 (2000-5000 for a working run on a 16 GB laptop)", "seed": "12345", "fit_cutoff": "blank or 100000",
    "fit_candidates": "LOGNORMAL,GAMMA,WEIBULL,PARETO,GPD",
    "below_cutoff": "STOP (default; EMPIRICAL to price a layer attaching below the cut-off)", "sl_attritional_threshold": "100000", "sl_cov_floor": "0.05",
    "net_claims_budget": "blank or 70000000", "reference_lives": "blank or 10000", "return_periods": "5,50,250",
}

# Column help for the table sheets: sheet -> column -> (description, example). Options of LIST columns come from
# TABLES. Written as header comments and to the Guide sheet; served to the browser page.
COLUMN_HELP = {
    "FX": {
        "currency": ("ISO code of a currency that appears in the claims or premium data", "USD"),
        "priced_rate_to_quote_ccy": ("Units of quote currency per 1 unit of this currency, at the rate you price at", "3.6725"),
        "rate_date": ("Date the rate was taken (reference only)", "2025-09-30"),
    },
    "Limits": {
        "plan": ("Plan code exactly as it appears in the data (members.csv plan / Merit plan)", "A"),
        "annual_limit": ("Annual benefit limit per life for that plan, quote currency; used when the data carries none", "1000000"),
        "sub_limit_notes": ("Free text on sub-limits, for the file", "Maternity 25k; dental 5k"),
    },
    "Trend": {
        "year": ("Calendar year the trend applies to; cover every experience year and the quote year", "2024"),
        "client_pct": ("Client's own claims-cost trend for the year, as a decimal", "0.09"),
        "market_pct": ("Market medical trend for the year, as a decimal", "0.10"),
        "inflation_pct": ("General inflation for the year, as a decimal", "0.03"),
        "residual_pct": ("Trend used directly when the weights are 0; otherwise added to the weighted blend", "0.08"),
        "weight_client": ("Weight on client_pct (weights sum to 1, or all 0 to use residual_pct alone)", "0.5"),
        "weight_market": ("Weight on market_pct", "0.5"),
        "weight_inflation": ("Weight on inflation_pct", "0"),
        "one_off_adj": ("One-off multiplier applied to the cumulative trend from this year on (0 or blank = none)", "0.05 (= +5%)"),
        "large_claim_trend_pct": ("Trend for claims above the attachment point when it differs from the FGU trend (blank = same)", "0.10"),
        "premium_trend_pct": ("Premium rate trend for the year, as a decimal", "0.07"),
    },
    "Completion": {
        "year": ("Experience year the factor completes", "2025"),
        "component": ("IBNR = reported to ultimate; UNEARNED = earned to written cohorts; UNWRITTEN = written to all cohorts (RA)", "IBNR"),
        "side": ("PRE_AP: FGU below the AP; POST_AP_AMOUNT / POST_AP_COUNT: layer amount / count; ATTRITIONAL: total claims below the stop-loss threshold", "POST_AP_AMOUNT"),
        "ap_band_from": ("Lowest attachment point the row applies to (0 = all layers)", "0"),
        "selected_factor": ("The factor you select (1 = complete)", "1.15"),
        "indicated_factor": ("Written by the tool from the data (stage 02); reference only, do not type here", "1.12"),
        "justification": ("Why this factor; goes into the run log", "Large-claim triangle, 2021-2024"),
    },
    "Weights": {
        "year": ("Experience year", "2023"),
        "months_complete": ("Months of the year in the data (12 for a full year; the current year is partial)", "9"),
        "use_default": ("1 = default weight from months_complete; 0 = use custom_weight", "1"),
        "custom_weight": ("Weight when use_default = 0; the custom weights must sum to 1 over the years used", "0.3"),
        "justification": ("Why a custom weight", "2021 distorted by COVID backlog"),
    },
    "Layers": {
        "cover": ("PP = per person, EV = per event, SL = aggregate stop loss", "PP"),
        "option_id": ("Option number within the cover, 1 to 4", "1"),
        "layer": ("Layer number within the option (1, or 2 for a second layer above the first)", "1"),
        "label": ("Name shown in the exhibits", "400k xs 100k"),
        "limit_basis": ("AMOUNT = fixed limit; TO_BENEFIT_LIMIT = up to each life's benefit limit", "AMOUNT"),
        "ap": ("Attachment point, quote currency (PP, EV)", "100000"),
        "limit": ("Limit above the AP, quote currency (blank when TO_BENEFIT_LIMIT)", "400000"),
        "aal": ("Annual aggregate limit on recoveries (blank = none)", "5000000"),
        "aad": ("Annual aggregate deductible on recoveries (blank = none)", "500000"),
        "co_participation_pct": ("Cedant's share of each recovery, as a decimal (blank or 0 = none)", "0.1"),
        "reinstatements_n": ("Number of reinstatements (blank = unlimited)", "1"),
        "reinstatement_cost_pct": ("Reinstatement premium as a share of the original premium (1 = 100%)", "1"),
        "reinst_pro_rata_time": ("1 = reinstatement cost pro rata to time remaining; 0 = full", "0"),
        "sl_claims_basis": ("SL only: GROSS claims, or NET_OF_XOL after the PP / EV recoveries in the Programme", "GROSS"),
        "sl_measure": ("SL only: AP and limit as % of budget, % of EPI, or an amount", "PCT_BUDGET"),
        "sl_ap": ("SL attachment in the sl_measure unit (1.0 = 100% of budget)", "1.0"),
        "sl_limit": ("SL limit in the sl_measure unit", "0.2"),
        "active": ("1 = price this row; 0 = keep it in the workbook but skip it", "1"),
    },
    "Programme": {
        "step": ("Order of application, 1 first", "2"),
        "cover": ("QS (informative; retention is Params qs_cedant_retention_pct), PP, EV or SL", "PP"),
        "option_id": ("Which option of that cover is in the programme", "1"),
        "active": ("1 = in the programme; 0 = skipped", "1"),
    },
    "EventRule": {
        "id_source": ("SUPPLIED = use event_id from the data only; CLUSTER = also group accident-coded lines", "CLUSTER"),
        "window_days": ("CLUSTER: days from the cluster's first date within which lines join the event", "3"),
        "group_key": ("CLUSTER: what lines must share to be one event", "SCHEME+SITE"),
        "min_lives": ("Minimum lives for a cluster to count as an event", "3"),
    },
    "EvScenarios": {
        "scenario_name": ("Name of the deterministic event scenario", "Worksite transport accident"),
        "lives": ("Lives affected", "10"),
        "cost_per_life": ("Cost per life in quote-year money, quote currency", "150000"),
        "return_period_yrs": ("Return period in years for the simulation (blank = shown deterministically only)", "50"),
        "site": ("Site the scenario applies to (reference only)", "Plant 2"),
    },
    "Loadings": {
        "cover": ("PP, EV or SL: one row per cover", "PP"),
        "reinsurer_expense_pct": ("Reinsurer expenses as a share of premium, decimal", "0.05"),
        "brokerage_pct": ("Brokerage as a share of premium, decimal", "0.10"),
        "profit_margin_pct": ("Reinsurer profit margin as a share of premium, decimal", "0.10"),
        "premium_tax_pct": ("Premium tax as a share of premium, decimal (0 if none)", "0"),
        "risk_load_k": ("Risk load: k x standard deviation of the layer loss added to the expected loss", "0.25"),
    },
    "Expiring": {
        "cover": ("Cover of the expiring layer", "PP"),
        "layer": ("Layer number", "1"),
        "ap": ("Expiring attachment point, quote currency", "100000"),
        "limit": ("Expiring limit (blank = to benefit limit)", "400000"),
        "rate_basis": ("PPPY = rate per person per year; PCT_EPI = % of EPI; FLAT = premium amount", "PPPY"),
        "rate": ("Expiring rate in the rate_basis unit", "950"),
        "premium": ("Expiring premium amount when rate_basis = FLAT", "8000000"),
        "recoveries_by_year": ("Expiring recoveries by year, text year:amount separated by ;", "2023:120000; 2024:0"),
    },
    "Scenarios": {
        "scenario_id": ("Scenario number (1 or more); Params scenario_id selects which to run", "1"),
        "scenario_name": ("Name shown in the Results Scenarios sheet", "Trend +2pp"),
        "target_table": ("Sheet the override applies to", "Trend"),
        "target_key": ("ALL, or column=value to pick rows (Params: param_id=iterations)", "component=IBNR"),
        "target_column": ("Numeric column to change (Params: value)", "residual_pct"),
        "operation": ("SET replaces, MULT multiplies, ADD adds", "ADD"),
        "override_value": ("The number applied by the operation", "0.02"),
    },
}
