"""Weather-derived features — VENDORED from the VoltSignal app (voltsignal.weather). Keep BYTE-IDENTICAL:
the public puller must derive exactly what the app documents/tests, so stored values match the API.

Pure functions only — no app/edge code, no secrets, no DB. Public (CC-BY weather + textbook transforms).
"""
from __future__ import annotations

BASE_C = 18.0   # degree-day base temperature (°C), AU convention


def cdd(temp_c: float, base: float = BASE_C) -> float:
    """Cooling degrees above base."""
    return max(0.0, temp_c - base)


def hdd(temp_c: float, base: float = BASE_C) -> float:
    """Heating degrees below base."""
    return max(0.0, base - temp_c)


WIND_CUT_IN = 3.0      # m/s — below this, no generation
WIND_RATED = 12.0      # m/s — rated output (proxy = 1.0)
WIND_CUT_OUT = 25.0    # m/s — turbine shuts down above this
LOW_WIND_PROXY_THRESHOLD = 0.1


def wind_power_proxy(wind_ms: float) -> float:
    """Normalised wind-power proxy (0-1): 0 below cut-in (3 m/s); cubic (~v^3) rise to rated (12 m/s ->
    1.0); flat 1.0 to cut-out (25 m/s); 0 above cut-out."""
    if wind_ms < WIND_CUT_IN or wind_ms > WIND_CUT_OUT:
        return 0.0
    if wind_ms >= WIND_RATED:
        return 1.0
    return (wind_ms ** 3 - WIND_CUT_IN ** 3) / (WIND_RATED ** 3 - WIND_CUT_IN ** 3)


def low_wind_flag(wind_ms: float) -> float:
    """1.0 if wind_power_proxy < LOW_WIND_PROXY_THRESHOLD (0.1), else 0.0."""
    return 1.0 if wind_power_proxy(wind_ms) < LOW_WIND_PROXY_THRESHOLD else 0.0


CLEAR_SKY_GHI_REF = 1000.0   # W/m² — nominal clear-sky peak


def solar_proxy(ghi_wm2: float) -> float:
    """GHI normalised to 0-1 clear-sky-relative: GHI / 1000 W/m², clamped [0,1]."""
    return max(0.0, min(1.0, ghi_wm2 / CLEAR_SKY_GHI_REF))


if __name__ == "__main__":  # self-check: the documented hand-checks (must match the app's tests)
    assert cdd(24.0) == 6.0 and hdd(10.0) == 8.0
    assert wind_power_proxy(2.0) == 0.0 and wind_power_proxy(12.0) == 1.0 and wind_power_proxy(26.0) == 0.0
    assert abs(wind_power_proxy(7.5) - 0.23214) < 1e-4
    assert low_wind_flag(4.0) == 1.0 and low_wind_flag(8.0) == 0.0
    assert solar_proxy(176.0) == 0.176 and solar_proxy(1300.0) == 1.0
    print("features self-check OK")
