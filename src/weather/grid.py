"""바다를 칸으로 나눠 칸마다 운항 판단을 계산한다. (해역 색칠)

무엇을 하나
-----------
지점 15곳만 점으로 찍는 것과 별개로, 항로가 지나는 바다 전체를 칸으로 나누고
칸마다 색(가능/조건/불가)을 계산한다. 기상청 특보 지도처럼 '면'으로 보인다.

왜 이렇게 하나
-------------
기상청 특보구역의 '경계 좌표'는 공개돼 있지 않다. 우리가 가진 특보구역 자료
에는 코드와 이름만 있고 지도에 그릴 다각형이 없다.
그래서 남의 구역을 빌려 칠하는 대신, 우리 격자에서 우리 기준으로 계산한다.

자료를 어디서 가져오나
--------------------
★ 추가 다운로드가 없다.
  ECMWF 는 지역을 잘라 주지 않아 어차피 전 지구 파일을 받는다.
  그 파일 안에 이미 우리 바다의 모든 격자칸이 들어 있다.
  그래서 지점 15곳을 뽑을 때 격자칸도 같이 뽑는다(ecmwf_open.collect).
  시정만 NOAA 에서 따로 받는데, 그쪽은 지역을 잘라 줘서 가볍다.

바다/육지 구분
-------------
표고(땅 높이) API 로 칸마다 높이를 받아 0 m 이하만 바다로 본다.
표고는 변하지 않으므로 한 번 받아 data/grid_mask.json 에 저장해 두고 재사용한다.
(표고는 예보가 아니라 지형 정보라 라이선스 문제가 없고, 한 번만 부른다.)

★ 중요한 한계
   격자 칸에는 기상청 특보를 반영하지 않는다.
   임의의 바다 좌표가 어느 특보구역에 드는지 알 수 없기 때문이다.
   따라서 격자 색 = 숫자 기준(풍속·돌풍·파고·시정)만으로 낸 판정이다.
   특보까지 반영한 판정은 지점 15곳의 동그라미에만 나온다.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

from . import judge
from .config import DATA_DIR, Config

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
CHUNK_ELEVATION = 100          # 표고 API 는 한 번에 100좌표까지
MASK_CACHE = DATA_DIR / "grid_mask.json"

DEFAULTS = {
    "enabled": True,
    "step_deg": 0.25,       # ECMWF 격자 간격과 같다. 따로 고를 이유가 없다.
    "mask_step_deg": 0.25,
    "margin_deg": 1.0,
}


def settings(config: Config) -> dict[str, Any]:
    raw = config.raw.get("grid") or {}
    out: dict[str, Any] = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if raw.get(key) is None:
            continue
        out[key] = bool(raw[key]) if isinstance(default, bool) else type(default)(raw[key])
    return out


def _floor_to(value: float, step: float) -> float:
    return round((value // step) * step, 4)


def _ceil_to(value: float, step: float) -> float:
    return round((-((-value) // step)) * step, 4)


def box_of(config: Config, opts: dict[str, Any]) -> dict[str, float]:
    lats = [loc.latitude for loc in config.locations.values()]
    lons = [loc.longitude for loc in config.locations.values()]
    margin = float(opts["margin_deg"])
    step = float(opts["mask_step_deg"])
    return {
        "lat0": _floor_to(min(lats) - margin, step),
        "lat1": _ceil_to(max(lats) + margin, step),
        "lon0": _floor_to(min(lons) - margin, step),
        "lon1": _ceil_to(max(lons) + margin, step),
    }


def _cell_centres(box: dict[str, float], step: float) -> list[tuple[float, float]]:
    """사각형 안을 step 간격으로 채운 칸 중심 좌표."""
    out: list[tuple[float, float]] = []
    lat = box["lat0"]
    while lat <= box["lat1"] + 1e-9:
        lon = box["lon0"]
        while lon <= box["lon1"] + 1e-9:
            out.append((round(lat, 4), round(lon, 4)))
            lon += step
        lat += step
    return out


def _chunks(seq: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(seq), size):
        yield seq[start:start + size]


def load_mask(box: dict[str, float], mask_step: float, timeout: int = 60,
              retries: int = 3, wait: int = 5,
              verbose: bool = True) -> list[tuple[float, float]]:
    """바다 칸의 중심 좌표 목록. 한 번 받아 저장해 두고 재사용한다."""
    key = {"lat0": box["lat0"], "lat1": box["lat1"],
           "lon0": box["lon0"], "lon1": box["lon1"],
           "mask_step_deg": mask_step}

    if MASK_CACHE.exists():
        try:
            cached = json.loads(MASK_CACHE.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                if verbose:
                    print("[격자] 저장된 바다 마스크 사용 (칸 %d개)" % len(cached["sea"]))
                return [tuple(p) for p in cached["sea"]]
        except Exception:
            pass

    cells = _cell_centres(box, mask_step)
    if verbose:
        print("[격자] 바다 마스크를 새로 만듭니다. 표고 조회 %d점 "
              "(한 번만 하고 저장해 둡니다)" % len(cells))

    sea: list[tuple[float, float]] = []
    for part in _chunks(cells, CHUNK_ELEVATION):
        payload = None
        for attempt in range(1, retries + 1):
            try:
                query = urllib.parse.urlencode({
                    "latitude": ",".join(str(p[0]) for p in part),
                    "longitude": ",".join(str(p[1]) for p in part)})
                with urllib.request.urlopen(ELEVATION_URL + "?" + query,
                                            timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < retries:
                    time.sleep(wait * (4 ** attempt))
                    continue
                if attempt >= retries:
                    raise
            except Exception:
                if attempt >= retries:
                    raise
                time.sleep(wait)
        for point, height in zip(part, (payload or {}).get("elevation") or []):
            if height is not None and height <= 0:
                sea.append(point)

    MASK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    MASK_CACHE.write_text(json.dumps({"key": key, "sea": [list(p) for p in sea]},
                                     separators=(",", ":")), encoding="utf-8")
    if verbose:
        print("[격자] 바다 칸 %d / 전체 %d (저장 완료)" % (len(sea), len(cells)))
    return sea


def build(config: Config, grid_raw: dict[str, Any],
          cells: list[tuple[float, float]],
          visibility: dict[tuple[float, float], list[float | None]] | None = None,
          verbose: bool = True) -> dict[str, Any] | None:
    """ECMWF 에서 뽑아 온 격자 값으로 칸마다 판정을 계산한다.

    grid_raw : ecmwf_open.collect 가 돌려준 {"times": [...], "series": {...}}
    cells    : 바다 칸 중심 좌표 (grid_raw 와 같은 순서)
    """
    if not grid_raw or not cells:
        return None

    times: list[str] = grid_raw["times"]
    series = grid_raw["series"]
    thresholds = config.thresholds

    out_cells: list[list[Any]] = []
    for index, (lat, lon) in enumerate(cells):
        chars: list[str] = []
        winds: list[Any] = []
        gusts: list[Any] = []
        waves: list[Any] = []

        for slot in range(len(times)):
            u = series["10u"][slot][index]
            v = series["10v"][slot][index]
            gust = series["10fg"][slot][index]
            wave = series["swh"][slot][index]

            wind = None
            if u is not None and v is not None:
                wind = math.hypot(u, v)

            winds.append(None if wind is None else round(wind, 1))
            gusts.append(None if gust is None else round(gust, 1))
            waves.append(None if wave is None else round(wave, 2))

            if wind is None or wave is None:
                chars.append("x")
                continue

            worst = judge.NORMAL
            checks = [("wind_speed_ms", wind), ("wind_gust_ms", gust),
                      ("wave_height_m", wave)]
            if visibility is not None:
                vis = visibility.get((lat, lon))
                if vis is not None and slot < len(vis):
                    checks.append(("visibility_km", vis[slot]))
            for column, value in checks:
                if value is None:
                    continue
                result = judge.evaluate_metric(column, value, thresholds)
                if result.status == judge.NO_DATA:
                    continue
                if judge.SEVERITY[result.status] > judge.SEVERITY[worst]:
                    worst = result.status
            chars.append({judge.NORMAL: "n", judge.CAUTION: "c",
                          judge.UNAVAILABLE: "u"}[worst])

        # 값이 하나도 없는 칸(육지로 잡힌 곳)은 빼서 용량을 아낀다.
        if any(c != "x" for c in chars):
            out_cells.append([lat, lon, "".join(chars), winds, gusts, waves])

    if verbose:
        print("[격자] 칸 %d개 x 시각 %d칸 판정 완료" % (len(out_cells), len(times)))

    opts = settings(config)
    return {
        "box": box_of(config, opts),
        "step_deg": opts["step_deg"],
        "cell_deg": opts["mask_step_deg"],
        "times": times,
        "cells": [{"lat": c[0], "lon": c[1], "st": c[2],
                   "wind": c[3], "gust": c[4], "wave": c[5]} for c in out_cells],
        "source": "ECMWF IFS/WAM 0.25 (CC BY 4.0)",
        "note": ("격자 색은 숫자 기준(풍속·돌풍·파고)만으로 낸 판정입니다. "
                 "기상청 특보는 반영되지 않습니다."),
    }
