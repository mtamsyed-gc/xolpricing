"""Single source of truth for the Results workbook data sheets (docs/data-contracts.md §5).

Used by tools/alteryx/build_stage07.py (which writes the sheets, columns in this order) and tools/excel/build_results.py
(whose formulas look columns up by header name, so a column added at the end never breaks them).
Each entry: sheet name -> columns. The source of each sheet is named in build_stage07.SOURCES.
"""
from __future__ import annotations

import control_spec as S

PARAM_COLS = [p[0] for p in S.PARAMS]

# text columns of the stage-04a tables (others numeric), for the typed placeholders stage 03 writes
TEXT_COLS = {"dist", "p1_name", "p2_name", "t_label"}
INT_COLS = {"n_params", "converged", "boundary", "rank_aic", "rank_bic", "rank_ks", "selected", "n_above", "n_fit",
            "n_uncensored"}


def col_type(c, X):
    """Designer type of a stage-04a column (X = xmlkit)."""
    return X.STR if c in TEXT_COLS else X.INT64 if c in INT_COLS else X.DBL

SHEETS = {
    "out_params": PARAM_COLS,
    "out_loadings": [c for c, *_ in S.TABLES["Loadings"][2]],
    "out_run_log": ["item", "value", "justification"],
    "out_warnings": ["stage", "code", "severity", "item", "message"],
    "out_recon": ["stage", "table", "measure", "value"],
    "out_completion_indicated": ["ty", "component", "measure", "months_elapsed", "indicated_factor"],
    "out_bc_summary": ["option_id", "layer", "l_label", "lim_basis", "ap", "limit", "aad", "aal", "cop", "n_years",
                       "n_xl_claims", "credibility_z", "bc_pppy_weighted_non", "bc_pppy_outlier", "bc_pppy_total",
                       "bc_pct_prem_weighted", "sens_straight_mean", "sens_wfreq_x_sev", "sens_sfreq_x_sev", "bc_min",
                       "bc_max", "lev_trend", "base_trend", "leverage_factor", "outlier_thr", "lives_quote_year",
                       "expected_cost_det", "expected_cost_pct_epi", "expected_cost_from_pct"],
    "out_bc_by_year": ["option_id", "layer", "l_label", "ty", "months_elapsed", "exposure_ly_full",
                       "exposure_prem_full", "prem_trend_f", "n_xl", "n_c", "freq_per_1000", "xl", "xl_c", "severity",
                       "xl_aa", "xl_non", "xl_out", "bc_pppy", "bc_pppy_non", "bc_pppy_out", "bc_pct_prem", "weight",
                       "weight_out"],
    "out_annualisation": ["option_id", "layer", "l_label", "ty", "xl_reported", "xl_ibnr", "xl_unearned",
                          "xl_unwritten", "n_reported", "n_ibnr", "n_unearned", "n_unwritten"],
    "out_large_claims": ["acc_key", "member_id", "ty", "segment", "scheme_id", "first_treatment", "amount_q", "trend_f",
                         "trended", "pre_ap_f", "adj_uncapped", "cap", "adj", "outlier", "outlier_thr"],
    "out_fit": ["t", "t_label", "u_fit", "alpha", "alpha_se", "n_fit", "n_uncensored", "n_censored", "max_observed",
                "n_ge", "expected_n", "s_empirical", "s_fitted"],
    "out_fit_candidates": ["dist", "n_params", "p1_name", "p1", "p2_name", "p2", "loglik", "aic", "bic", "ks",
                           "converged", "boundary", "rank_aic", "rank_bic", "rank_ks", "selected", "me_ratio_min",
                           "me_ratio_max", "u_fit", "max_observed", "n_fit", "n_uncensored"],
    "out_fit_mean_excess": ["dist", "ap", "n_above", "mean_excess_empirical", "mean_excess_fitted", "ratio"],
    "out_fit_survival": ["dist", "t", "t_label", "s_km", "s_fitted"],
    "out_sim_summary": ["option_id", "layer", "l_label", "iterations", "lambda", "freq_dist", "phi", "u_fit", "alpha",
                        "sev_dist", "max_observed", "lives_q", "mean_recovery", "sd_recovery", "cov", "p_zero", "p50", "p75", "p90",
                        "p95", "p99", "p995", "max_recovery", "mean_below_max_observed", "mean_above_max_observed",
                        "mean_reinst_usage", "expected_cost_det", "credibility_z", "det_to_stoch_below",
                        "expected_loss_blended", "expected_loss_pppy"],
    "out_sim_quantiles": ["option_id", "layer", "l_label", "q", "recovery"],
    "out_ev_events": ["event_key", "ev_src", "ty", "event_date", "n_lives", "scheme_id", "site", "cause_code",
                      "loss_raw", "trend_f", "loss_adj", "loss_net_pp"],
    "out_ev_bc_by_year": ["option_id", "layer", "l_label", "ty", "exposure_ly_full", "n_events", "events_per_1000",
                          "n_xl", "xl", "recovery", "xl_net", "recovery_net", "bc_pppy", "weight"],
    "out_ev_bc_summary": ["option_id", "layer", "l_label", "n_events", "n_xl_events", "bc_pppy_weighted",
                          "lives_quote_year", "expected_cost_det", "credibility_z", "lambda_exp"],
    "out_ev_scenarios": ["scenario_name", "lives", "cost_per_life", "return_period_yrs", "loss", "loss_net_pp",
                         "option_id", "layer", "l_label", "layer_loss", "layer_loss_net_pp"],
    "out_ev_sim_summary": ["option_id", "layer", "l_label", "iterations", "lambda_exp", "mean_events", "n_hist_events",
                           "mean_recovery", "sd_recovery", "p_zero", "p50", "p90", "p95", "p99", "p995", "max_recovery",
                           "mean_reinst_usage", "mean_recovery_net_pp", "expected_cost_det", "credibility_z",
                           "expected_loss_blended", "expected_loss_pppy", "lives_quote_year"],
    "out_ev_sim_quantiles": ["option_id", "layer", "l_label", "q", "recovery"],
    "out_attritional_by_year": ["ty", "weight", "exposure_ly_full", "total_claims", "n_large", "large_claims_raw",
                                "attritional_raw", "months_elapsed", "trend_f", "completion_f", "attritional_adj",
                                "attritional_pppy", "t_sl"],
    "out_gross_summary": ["t_sl", "u_fit", "n_years", "attritional_pppy_weighted", "attritional_pppy_mean", "cov_hist",
                          "cov_used", "attritional_dist", "lives_quote_year", "reference_lives", "incidence_adj",
                          "attritional_mean_q", "m_ln", "sigma_ln", "iterations", "mean_attritional_sim",
                          "mean_large_sim", "mean_gross", "sd_gross", "budget", "budget_source", "epi"],
    "out_struct_summary": ["struct_key", "cover", "option_id", "pp_option", "ev_option", "sl_option", "l_label",
                           "iterations", "mean_gross", "mean_recovery", "sd_recovery", "p_zero", "mean_recovery_total",
                           "mean_net", "sd_net", "mean_ru_l1", "mean_ru_l2", "mean_ru_ev1", "mean_ru_ev2", "sl_basis",
                           "sl_measure", "sl_ap", "sl_limit", "ap_amount", "limit_amount", "ap_pct_budget",
                           "limit_pct_budget", "ap_pct_epi", "limit_pct_epi", "budget", "epi"],
    "out_struct_quantiles": ["struct_key", "cover", "option_id", "pp_option", "ev_option", "sl_option", "l_label", "q",
                             "qm", "net_claims", "ru_l1_at_q", "ru_l2_at_q", "ru_ev1_at_q", "ru_ev2_at_q", "recovery"],
    "out_exposed_lives": ["cover", "option_id", "layer", "l_label", "ap", "share_exposed", "exposed_lives",
                          "lives_quote_year", "reference_lives", "basis_note"],
    "out_expiring": ["cover", "layer", "ap", "limit", "rate_basis", "rate", "premium", "matched_option", "lev_trend_used",
                     "lives_quote_year", "epi", "expiring_premium_q", "expiring_premium_trended"],
    "out_asif_by_year": ["cover", "layer", "matched_option", "ty", "exposure_ly_full", "exposure_prem_full",
                         "asif_recovery", "asif_premium", "premium_year_level", "actual_recovery"],
    "scn_summary": ["scenario_id", "scenario_name", "run_at", "struct_key", "l_label", "budget", "mean_recovery",
                    "sd_recovery", "mean_net", "sd_net", "expected_loss_blended"],
}
