"""Single source of truth for the Control workbook (docs/data-contracts.md §4).

Used by tools/excel/build_control.py (writes the workbook) and by the stage-00 workflow builder (validation rules).
Every table sits on its own sheet: title in row 1, instructions in row 2, header in row HEADER_ROW, data below.
All Params values are stored as text (ISO dates, plain decimals) so Alteryx reads one consistent type.
"""
from __future__ import annotations

HEADER_ROW = 4
VERSION = "0.1"

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
    ("fit_selected", "Stochastic", "", "LIST", "LOGNORMAL,GAMMA,WEIBULL,PARETO,GPD", False,
     "Severity distribution used by the simulation (blank = lowest AIC in stage 04a); justify an override"),
    ("severity_source", "Stochastic", "", "LIST", "PYTHON,NATIVE", True,
     "PYTHON: distribution selected in 04a_fit_severity (Python tool); NATIVE: Pareto fitted in stage 04 (no Python)"),
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
    "fit_candidates": "LOGNORMAL,GAMMA,WEIBULL,PARETO,GPD", "freq_model": "POISSON", "severity_source": "PYTHON",
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
                   "indicated_factor is written by Alteryx (reference only).",
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
