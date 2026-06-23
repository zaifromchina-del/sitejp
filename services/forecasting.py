# services/forecasting.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ModelResult:
    name: str
    params: Dict[str, Any]
    forecast: List[Optional[float]]
    error: List[Optional[float]]
    abs_error: List[Optional[float]]
    mad: Optional[float]
    next_forecast: Optional[float]


def _mad_from_abs(abs_errors: List[Optional[float]], start_idx: int) -> Optional[float]:
    vals = [x for x in abs_errors[start_idx:] if x is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _linear_regression(x_vals: List[float], y_vals: List[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(x_vals)
    if n < 2:
        return None, None

    sx = sum(x_vals)
    sy = sum(y_vals)
    sxx = sum(x * x for x in x_vals)
    sxy = sum(x * y for x, y in zip(x_vals, y_vals))
    den = (n * sxx) - (sx * sx)
    if den == 0:
        return None, None

    b = ((n * sxy) - (sx * sy)) / den
    a = (sy - (b * sx)) / n
    return a, b


def _centered_moving_average(d: List[float], season_length: int) -> List[Optional[float]]:
    n = len(d)
    centered = [None] * n
    if season_length < 2 or n < season_length + 2:
        return centered

    moving = []
    for start in range(0, n - season_length + 1):
        moving.append(sum(d[start:start + season_length]) / season_length)

    if season_length % 2 == 1:
        offset = season_length // 2
        for idx, value in enumerate(moving):
            centered[idx + offset] = value
        return centered

    offset = season_length // 2
    for idx in range(len(moving) - 1):
        centered[idx + offset] = (moving[idx] + moving[idx + 1]) / 2
    return centered


def moving_average(d: List[float], k: int) -> ModelResult:
    n = len(d)
    forecast = [None] * n
    error = [None] * n
    abs_error = [None] * n

    for t in range(k, n):
        f = sum(d[t - k:t]) / k
        forecast[t] = f
        e = d[t] - f
        error[t] = e
        abs_error[t] = abs(e)

    mad = _mad_from_abs(abs_error, start_idx=k)
    next_forecast = sum(d[n - k:n]) / k if n >= k else None

    return ModelResult(
        name=f"media_movel_{k}",
        params={"k": k},
        forecast=forecast,
        error=error,
        abs_error=abs_error,
        mad=mad,
        next_forecast=next_forecast,
    )


def exp_smoothing(d: List[float], alpha: float) -> ModelResult:
    n = len(d)
    forecast = [None] * n
    error = [None] * n
    abs_error = [None] * n

    if n >= 2:
        forecast[1] = d[0]
        error[1] = d[1] - forecast[1]
        abs_error[1] = abs(error[1])

    for t in range(2, n):
        forecast[t] = forecast[t - 1] + alpha * error[t - 1]
        error[t] = d[t] - forecast[t]
        abs_error[t] = abs(error[t])

    mad = _mad_from_abs(abs_error, start_idx=1)
    next_forecast = None
    if n >= 2:
        next_forecast = forecast[n - 1] + alpha * error[n - 1]

    return ModelResult(
        name=f"media_exponencial_{alpha:.2f}",
        params={"alpha": alpha},
        forecast=forecast,
        error=error,
        abs_error=abs_error,
        mad=mad,
        next_forecast=next_forecast,
    )


def holt_from_excel(d: List[float], alpha: float, beta: float) -> ModelResult:
    n = len(d)
    forecast = [None] * n
    error = [None] * n
    abs_error = [None] * n

    m_vals = [None] * n
    t_vals = [None] * n
    next_vals = [None] * n

    if n < 3:
        return ModelResult(
            name="ajuste_tendencia_holt",
            params={"alpha": alpha, "beta": beta},
            forecast=forecast,
            error=error,
            abs_error=abs_error,
            mad=None,
            next_forecast=None,
        )

    m_vals[2] = d[2]
    t_vals[2] = (d[2] - d[0]) / 2.0
    next_vals[2] = m_vals[2] + t_vals[2]

    if n >= 4:
        forecast[3] = next_vals[2]
        error[3] = d[3] - forecast[3]
        abs_error[3] = abs(error[3])

        m_vals[3] = forecast[3] + alpha * error[3]
        t_vals[3] = t_vals[2]
        next_vals[3] = m_vals[3] + t_vals[3]

    for idx in range(4, n):
        forecast[idx] = next_vals[idx - 1]
        error[idx] = d[idx] - forecast[idx]
        abs_error[idx] = abs(error[idx])

        m_vals[idx] = forecast[idx] + alpha * error[idx]
        t_vals[idx] = t_vals[idx - 1] + beta * ((m_vals[idx] - m_vals[idx - 1]) - t_vals[idx - 1])
        next_vals[idx] = m_vals[idx] + t_vals[idx]

    mad = _mad_from_abs(abs_error, start_idx=3)
    next_forecast = next_vals[n - 1]

    return ModelResult(
        name="ajuste_tendencia_holt",
        params={"alpha": alpha, "beta": beta},
        forecast=forecast,
        error=error,
        abs_error=abs_error,
        mad=mad,
        next_forecast=next_forecast,
    )


def linear_regression_trend(d: List[float]) -> ModelResult:
    n = len(d)
    forecast = [None] * n
    error = [None] * n
    abs_error = [None] * n

    x_vals = [float(i) for i in range(1, n + 1)]
    a, b = _linear_regression(x_vals, [float(v) for v in d])
    if a is None or b is None:
        return ModelResult("equacao_linear", {"a": None, "b": None}, forecast, error, abs_error, None, None)

    for idx, x in enumerate(x_vals):
        pred = a + (b * x)
        forecast[idx] = pred
        err = d[idx] - pred
        error[idx] = err
        abs_error[idx] = abs(err)

    return ModelResult(
        name="equacao_linear",
        params={"a": a, "b": b},
        forecast=forecast,
        error=error,
        abs_error=abs_error,
        mad=_mad_from_abs(abs_error, start_idx=0),
        next_forecast=a + (b * (n + 1)),
    )


def seasonal_simple(d: List[float], season_length: int = 12) -> ModelResult:
    n = len(d)
    forecast = [None] * n
    error = [None] * n
    abs_error = [None] * n

    if n < season_length * 2:
        return ModelResult(
            name=f"sazonalidade_simples_{season_length}",
            params={"periodo_sazonal": season_length},
            forecast=forecast,
            error=error,
            abs_error=abs_error,
            mad=None,
            next_forecast=None,
        )

    centered = _centered_moving_average(d, season_length)
    seasonal_ratios: List[List[float]] = [[] for _ in range(season_length)]
    for idx, cma in enumerate(centered):
        if cma is None or cma == 0:
            continue
        seasonal_ratios[idx % season_length].append(d[idx] / cma)

    indices = []
    for bucket in seasonal_ratios:
        if not bucket:
            return ModelResult(
                name=f"sazonalidade_simples_{season_length}",
                params={"periodo_sazonal": season_length},
                forecast=forecast,
                error=error,
                abs_error=abs_error,
                mad=None,
                next_forecast=None,
            )
        indices.append(sum(bucket) / len(bucket))

    normalizer = sum(indices) / season_length
    indices = [v / normalizer for v in indices]
    deseasonalized = [d[idx] / indices[idx % season_length] for idx in range(n)]
    level = sum(deseasonalized) / len(deseasonalized)

    for idx in range(n):
        pred = level * indices[idx % season_length]
        forecast[idx] = pred
        err = d[idx] - pred
        error[idx] = err
        abs_error[idx] = abs(err)

    return ModelResult(
        name=f"sazonalidade_simples_{season_length}",
        params={"periodo_sazonal": season_length},
        forecast=forecast,
        error=error,
        abs_error=abs_error,
        mad=_mad_from_abs(abs_error, start_idx=season_length),
        next_forecast=level * indices[n % season_length],
    )


def seasonal_trend(d: List[float], season_length: int = 12) -> ModelResult:
    n = len(d)
    forecast = [None] * n
    error = [None] * n
    abs_error = [None] * n

    if n < season_length * 2:
        return ModelResult(
            name=f"sazonalidade_tendencia_{season_length}",
            params={"periodo_sazonal": season_length},
            forecast=forecast,
            error=error,
            abs_error=abs_error,
            mad=None,
            next_forecast=None,
        )

    centered = _centered_moving_average(d, season_length)
    seasonal_ratios: List[List[float]] = [[] for _ in range(season_length)]
    for idx, cma in enumerate(centered):
        if cma is None or cma == 0:
            continue
        seasonal_ratios[idx % season_length].append(d[idx] / cma)

    indices = []
    for bucket in seasonal_ratios:
        if not bucket:
            return ModelResult(
                name=f"sazonalidade_tendencia_{season_length}",
                params={"periodo_sazonal": season_length},
                forecast=forecast,
                error=error,
                abs_error=abs_error,
                mad=None,
                next_forecast=None,
            )
        indices.append(sum(bucket) / len(bucket))

    normalizer = sum(indices) / season_length
    indices = [v / normalizer for v in indices]
    deseasonalized = [d[idx] / indices[idx % season_length] for idx in range(n)]

    x_vals = [float(i) for i in range(1, n + 1)]
    a, b = _linear_regression(x_vals, deseasonalized)
    if a is None or b is None:
        return ModelResult(
            name=f"sazonalidade_tendencia_{season_length}",
            params={"periodo_sazonal": season_length},
            forecast=forecast,
            error=error,
            abs_error=abs_error,
            mad=None,
            next_forecast=None,
        )

    for idx, x in enumerate(x_vals):
        trend = a + (b * x)
        pred = trend * indices[idx % season_length]
        forecast[idx] = pred
        err = d[idx] - pred
        error[idx] = err
        abs_error[idx] = abs(err)

    return ModelResult(
        name=f"sazonalidade_tendencia_{season_length}",
        params={"periodo_sazonal": season_length, "a": a, "b": b},
        forecast=forecast,
        error=error,
        abs_error=abs_error,
        mad=_mad_from_abs(abs_error, start_idx=season_length),
        next_forecast=(a + (b * (n + 1))) * indices[n % season_length],
    )


def choose_best_model(results: List[ModelResult], eval_start_period: int = 13) -> Tuple[ModelResult, List[ModelResult]]:
    start_idx = eval_start_period - 1

    recalced: List[ModelResult] = []
    for r in results:
        mad = _mad_from_abs(r.abs_error, start_idx=start_idx)
        recalced.append(ModelResult(
            name=r.name,
            params=r.params,
            forecast=r.forecast,
            error=r.error,
            abs_error=r.abs_error,
            mad=mad,
            next_forecast=r.next_forecast,
        ))

    valid = [r for r in recalced if r.mad is not None]
    best = min(valid, key=lambda r: r.mad)
    return best, recalced
