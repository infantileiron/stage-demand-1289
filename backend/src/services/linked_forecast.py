"""Theatre-wide linked rehearsal.

Per-venue and total ridge forecasts are produced independently, then reconciled
into one daily, auditable set of figures that add up exactly and stay inside
each venue's [0, capacity] box.

Leakage discipline: every tuning fold, residual and calibration statistic is
computed from data strictly before the backtest origin. The held-out two weeks
are only read to report realized coverage.
"""
from datetime import date
import numpy as np

from src.services.forecast import (
    HOLDOUT_DAYS, COVERAGE, venue_rows, select_alpha, predict,
    seasonal_naive, finite_quantile, build_future,
)

VENUES = ("大剧场", "黑匣子", "实验厅")


def _total_rows(venue_data):
    """Aggregate aligned venue rows into one theatre-level series."""
    first = venue_data[0]
    first_dates = [r["date"] for r in first]
    for data in venue_data[1:]:
        if [r["date"] for r in data] != first_dates:
            raise ValueError("三厅日期不齐，请补齐为相同连续日期后再做联动预演")
    totals = []
    for idx, row in enumerate(first):
        totals.append({
            "date": row["date"],
            "attendance": sum(data[idx]["attendance"] for data in venue_data),
            "capacity": sum(data[idx]["capacity"] for data in venue_data),
            "event": row.get("event", 0),
        })
    return totals


def _model_series(data):
    """Alpha selection, held-out backtest and accuracy stats for one series.

    Only observations before the holdout origin enter tuning or calibration.
    """
    calibration_start = len(data) - HOLDOUT_DAYS
    alpha, folds = select_alpha(data, calibration_start)
    heldout = data[calibration_start:]
    back = np.asarray(predict(data[:calibration_start], heldout, alpha), dtype=float)
    actual = np.asarray([r["attendance"] for r in heldout], dtype=float)
    baseline = np.asarray(seasonal_naive(data[:calibration_start], len(heldout)), dtype=float)
    return {
        "alpha": alpha, "folds": folds, "heldout": heldout,
        "back": back, "actual": actual, "baseline": baseline,
        "residual": actual - back,
        "mae": float(np.mean(np.abs(actual - back))),
    }


def _reconcile_weights(residual_matrix):
    """MinT-style covariance reconciliation, singular-safe by construction.

    The constrained MinT solution for a sum hierarchy (S = ones row) is
        b = b_hat + (W @ 1) / (1' W 1) * (total - sum(b_hat))
    where W is the venue residual covariance. The only "inversion" is the
    scalar (1' W 1); there is no matrix inverse, so a rank-deficient W (venue
    residuals perfectly collinear) stays finite without special handling.

    Inputs are pre-origin backtest residuals only. Returns (covariance shares
    summing to 1, positive precision weights for the box repair, covariance W).
    """
    r = np.asarray(residual_matrix, dtype=float)
    omega = np.atleast_2d(np.cov(r, rowvar=False))
    ones = np.ones(omega.shape[0])
    total_var = float(ones @ omega @ ones)
    if total_var <= 1e-12:
        # Degenerate window (e.g. every residual identical/near zero): equal split.
        shares = ones / len(ones)
    else:
        shares = (omega @ ones) / total_var
        if not np.all(np.isfinite(shares)):
            shares = ones / len(ones)
    # Precision weights for the non-negative capacity repair: venues with
    # smaller historical residual variance move less. Always strictly positive.
    precision = 1.0 / np.maximum(np.diag(omega), 1e-12)
    precision = precision / float(np.sum(precision)) * len(precision)
    return shares, precision, omega


def mint_adjust(total_pred, venue_preds, shares):
    """Linear covariance reconciliation (unconstrained MinT step)."""
    p = np.asarray(venue_preds, dtype=float)
    gap = float(total_pred) - float(np.sum(p))
    return p + shares * gap


def project_box(anchor, total_pred, weights, capacities):
    """Project an anchored point onto the additive, box-constrained plane.

    Minimises sum_j w_j (x_j - a_j)^2 subject to sum_j x_j = t and
    0 <= x_j <= capacity_j. Coordinates crossing a boundary are frozen and the
    equality multiplier is recomputed on the remaining free coordinates.
    """
    a = np.asarray(anchor, dtype=float)
    c = np.asarray(capacities, dtype=float)
    w = np.asarray(weights, dtype=float)
    t = float(np.clip(total_pred, 0.0, float(np.sum(c))))

    x = np.clip(a.copy(), 0.0, c)
    free = np.ones(len(a), dtype=bool)
    for _ in range(len(a) + 2):
        if not np.any(free):
            break
        inv_w = 1.0 / w[free]
        lam = (float(np.sum(x[free])) - (t - float(np.sum(x[~free])))) / float(np.sum(inv_w))
        candidate = x[free] - inv_w * lam
        low = candidate < -1e-9
        high = candidate > c[free] + 1e-9
        if not np.any(low) and not np.any(high):
            x[free] = np.clip(candidate, 0.0, c[free])
            break
        free_idx = np.where(free)[0]
        for j in free_idx[low]:
            x[j] = 0.0; free[j] = False
        for j in free_idx[high]:
            x[j] = c[j]; free[j] = False

    # Exact-additivity cleanup inside remaining headroom (guards rounding gaps).
    gap = t - float(np.sum(x))
    if abs(gap) > 1e-9:
        order = np.argsort(-w)
        for j in order:
            room = (c[j] - x[j]) if gap > 0 else x[j]
            take = min(abs(gap), room) * (1.0 if gap > 0 else -1.0)
            x[j] += take; gap -= take
            if abs(gap) <= 1e-9:
                break
    return np.clip(x, 0.0, c)


def reconcile_day(total_pred, venue_preds, shares, precision, capacities):
    """Covariance adjustment followed by the non-negative capacity repair."""
    adjusted = mint_adjust(total_pred, venue_preds, shares)
    return project_box(adjusted, total_pred, precision, capacities)


def linked_analyze(rows, scenario):
    if scenario["horizon"] not in (7, 14):
        raise ValueError("预测天数仅支持7或14")
    venue_data = [venue_rows(rows, venue) for venue in VENUES]
    totals = _total_rows(venue_data)
    calibration_start = len(totals) - HOLDOUT_DAYS

    # Independent venue models and the independent theatre-total model. Tuning,
    # folds and residuals only see observations before the backtest origin.
    venue_models = [_model_series(data) for data in venue_data]
    total_model = _model_series(totals)

    residual_matrix = np.column_stack([m["residual"] for m in venue_models])
    shares, precision, omega = _reconcile_weights(residual_matrix)
    capacities = [int(data[-1]["capacity"]) for data in venue_data]
    total_capacity = sum(capacities)

    # Total-level interval: calibrated on the aggregated same-day residuals,
    # i.e. it reflects venues rising/falling together, never summed venue bands.
    total_radius = finite_quantile(np.abs(total_model["residual"]), COVERAGE)
    total_actual = total_model["actual"]
    total_back = total_model["back"]
    raw_coverage = float(np.mean(np.abs(total_actual - total_back) <= total_radius + 1e-9))
    # Covariance-implied same-day total sigma (1'Omega 1)^0.5; the naive
    # benchmark sums venue sigmas as if venue bands were simply added, which
    # overstates total risk whenever venues do not move in lockstep extremes.
    ones3 = np.ones(len(VENUES))
    total_sigma = float(np.sqrt(max(0.0, float(ones3 @ omega @ ones3))))
    naive_sum_sigma = float(np.sum(np.sqrt(np.maximum(np.diag(omega), 0.0))))
    # Each venue's own band is calibrated on its own pre-origin residuals.
    venue_radii = [finite_quantile(np.abs(m["residual"]), COVERAGE) for m in venue_models]

    # Reconcile every held-out day; coverage is measured on the final figures.
    reconciled_back = np.asarray([
        reconcile_day(float(total_back[i]),
                      [float(m["back"][i]) for m in venue_models],
                      shares, precision, capacities)
        for i in range(HOLDOUT_DAYS)
    ])
    reconciled_totals = reconciled_back.sum(axis=1)
    reconciled_coverage = float(np.mean(
        np.abs(total_actual - reconciled_totals) <= total_radius + 1e-9))

    # Future forecasts: every model recurses on its own full history.
    dates = [date.fromisoformat(r["date"]) for r in totals]
    future = build_future(dates, scenario["horizon"], bool(scenario.get("weekend_event", False)))
    total_future = np.asarray(predict(totals, future, total_model["alpha"]), dtype=float)
    venue_future = [np.asarray(predict(data, future, m["alpha"]), dtype=float)
                    for data, m in zip(venue_data, venue_models)]
    reconciled_future = np.asarray([
        reconcile_day(float(total_future[i]),
                      [float(vf[i]) for vf in venue_future],
                      shares, precision, capacities)
        for i in range(len(future))
    ])
    per_usher = scenario["people_per_usher"]

    linked_forecast = []
    for i, day in enumerate(future):
        # Round venues first, then derive the total from the rounded figures so
        # the displayed rows reconcile to the digit shown on screen.
        venue_point = {venue: round(float(reconciled_future[i][j]), 1) for j, venue in enumerate(VENUES)}
        total_point = float(round(sum(venue_point.values()), 1))
        linked_forecast.append({
            "date": day["date"], "event": day["event"],
            "total_prediction": total_point,
            "total_raw": round(float(total_future[i]), 1),
            "total_lower": round(max(0.0, total_point - total_radius), 1),
            "total_upper": round(min(float(total_capacity), total_point + total_radius), 1),
            "venue_prediction": venue_point,
            "venue_raw": {venue: round(float(venue_future[j][i]), 1) for j, venue in enumerate(VENUES)},
            "venue_delta": {venue: round(venue_point[venue] - round(float(venue_future[j][i]), 1), 1) for j, venue in enumerate(VENUES)},
            "venue_ushers": {venue: max(2, int(np.ceil(reconciled_future[i][j] / per_usher))) for j, venue in enumerate(VENUES)},
        })

    venue_forecasts = []
    for j, venue in enumerate(VENUES):
        m = venue_models[j]
        venue_forecasts.append({
            "venue": venue, "alpha": m["alpha"], "capacity": capacities[j],
            "mae": round(m["mae"], 2),
            "baseline_mae": round(float(np.mean(np.abs(m["actual"] - m["baseline"]))), 2),
            "share": round(float(shares[j]), 4),
            "residual_sigma": round(float(np.sqrt(max(0.0, float(omega[j, j])))), 2),
            "folds": m["folds"],
            "forecast": [
                {"date": day["date"], "event": day["event"],
                 "prediction": round(float(reconciled_future[i][j]), 1),
                 "raw": round(float(venue_future[j][i]), 1),
                 "delta": round(float(reconciled_future[i][j] - venue_future[j][i]), 1),
                 "lower": round(max(0.0, float(reconciled_future[i][j]) - venue_radii[j]), 1),
                 "upper": round(min(float(capacities[j]), float(reconciled_future[i][j]) + venue_radii[j]), 1),
                 "ushers": max(2, int(np.ceil(reconciled_future[i][j] / per_usher)))}
                for i, day in enumerate(future)
            ],
            "interval_radius": round(venue_radii[j], 2),
        })

    backtest = []
    for i, row in enumerate(total_model["heldout"]):
        venue_point = {venue: round(float(reconciled_back[i][j]), 1) for j, venue in enumerate(VENUES)}
        venue_raw = {venue: round(float(venue_models[j]["back"][i]), 1) for j, venue in enumerate(VENUES)}
        total_point = round(sum(venue_point.values()), 1)
        backtest.append({
            "date": row["date"],
            "total_actual": int(total_actual[i]),
            "total_prediction": total_point,
            "total_raw": round(float(total_back[i]), 1),
            "total_lower": round(max(0.0, total_point - total_radius), 1),
            "total_upper": round(min(float(total_capacity), total_point + total_radius), 1),
            "venue_actual": {venue: int(venue_models[j]["actual"][i]) for j, venue in enumerate(VENUES)},
            "venue_raw": venue_raw,
            "venue_prediction": venue_point,
            "venue_delta": {venue: round(venue_point[venue] - venue_raw[venue], 1) for venue in VENUES},
        })

    return {
        "mode": "linked",
        "venues": list(VENUES),
        "horizon": scenario["horizon"],
        "nominal_coverage": COVERAGE,
        "history_days": len(totals),
        "total_capacity": total_capacity,
        "total_alpha": total_model["alpha"],
        "total_folds": total_model["folds"],
        "total_mae": round(float(np.mean(np.abs(total_model["residual"]))), 2),
        "total_baseline_mae": round(float(np.mean(np.abs(total_actual - total_model["baseline"]))), 2),
        "interval_radius": round(total_radius, 2),
        "total_sigma": round(total_sigma, 2),
        "naive_sum_sigma": round(naive_sum_sigma, 2),
        "raw_coverage": round(raw_coverage, 3),
        "realized_coverage": round(reconciled_coverage, 3),
        "reconcile_shares": {venue: round(float(shares[j]), 4) for j, venue in enumerate(VENUES)},
        "residual_covariance": {
            venue: {other: round(float(omega[j, k]), 2) for k, other in enumerate(VENUES)}
            for j, venue in enumerate(VENUES)
        },
        "forecast": linked_forecast,
        "venue_forecasts": venue_forecasts,
        "backtest": backtest,
        "split": {
            "tuning_last_date": totals[calibration_start - 1]["date"],
            "calibration_first_date": totals[calibration_start]["date"],
            "training_last_date": totals[-1]["date"],
        },
        "limitations": [
            "模拟到场数据，不能替代运营判断",
            "总量区间由三厅同日合计残差校准，反映同向涨跌，不等于各厅区间上下界之和",
            "协调权重与区间半径只使用回测起点之前的数据；残差完全共线时协方差矩阵秩亏，协调仅做标量归一化，不涉及矩阵求逆，仍可稳定求解",
            "漂移或非独立同分布冲击下，90%名义覆盖率不被保证",
        ],
    }
