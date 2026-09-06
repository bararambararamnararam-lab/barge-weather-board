"""GRIB 파일에서 지점 값을 뽑는 공용 도구.

ECMWF 와 NOAA 둘 다 GRIB2 형식으로 자료를 준다.
JSON 처럼 바로 못 읽고 eccodes 라이브러리로 풀어야 한다.

여기서 하는 일
  1) GRIB 파일 안의 여러 메시지(항목)를 훑는다
  2) 각 지점에서 가장 가까운 격자칸 값을 꺼낸다
  3) 바다 항목(파고 등)이 육지로 잡히면 근처 바다칸을 찾아 대신 쓴다
"""

from __future__ import annotations

from typing import Any, Iterable

import eccodes as ec

# 일반 필드의 결측값은 1e20 이상으로 표시된다.
BIG = 1e19

# 파랑 모델만 육지를 9999 로 채운다.
# ★ 이 기준을 모든 항목에 적용하면 안 된다.
#   시정은 미터 단위라 맑은 날 24,000 m 가 나오는데 그것까지 결측으로 버리게 된다.
#   (실제로 그 실수를 해서 시정이 통째로 비었다.)
WAVE_MISSING = 9998.0

# 바다칸을 찾을 때 넓혀 갈 거리(도). 0.25도 ≈ 28 km.
SEA_SEARCH_STEPS = (0.25, 0.5, 0.75, 1.0)


def _value_at(gid: int, lat: float, lon: float,
              missing: float | None = None) -> float | None:
    """한 격자점 값. 결측이면 None.

    missing 을 주면 그 값 이상도 결측으로 본다(파랑 모델의 9999 처리용).
    """
    try:
        near = ec.codes_grib_find_nearest(gid, lat, lon)[0]
    except Exception:
        return None
    value = near.value
    if value is None or value > BIG:
        return None
    if missing is not None and value >= missing:
        return None
    return float(value)


def _value_with_sea_fallback(gid: int, lat: float, lon: float) -> float | None:
    """육지로 잡히면 주변 바다칸을 찾아 그 값을 쓴다.

    조선소(영성법인·영성가야)처럼 물가에 붙은 지점은 파랑모델 격자가
    육지라 값이 없다. 그럴 때 가까운 바다칸 값으로 대신한다.
    Open-Meteo 의 cell_selection=sea 와 같은 취지다.
    """
    direct = _value_at(gid, lat, lon, WAVE_MISSING)
    if direct is not None:
        return direct

    for offset in SEA_SEARCH_STEPS:
        for dlat, dlon in ((0, offset), (0, -offset), (offset, 0), (-offset, 0),
                           (offset, offset), (offset, -offset),
                           (-offset, offset), (-offset, -offset)):
            value = _value_at(gid, lat + dlat, lon + dlon, WAVE_MISSING)
            if value is not None:
                return value
    return None


def read_points(path: str, points: list[tuple[str, float, float]],
                sea_params: Iterable[str] = ()) -> dict[str, dict[str, float]]:
    """GRIB 파일에서 지점별 값을 뽑는다.

    points     : (지점ID, 위도, 경도) 목록
    sea_params : 바다 항목 이름들. 육지로 잡히면 근처 바다칸을 찾는다.

    돌려주는 값: {지점ID: {항목이름: 값}}
    """
    sea = set(sea_params)
    out: dict[str, dict[str, float]] = {pid: {} for pid, _, _ in points}

    with open(path, "rb") as handle:
        while True:
            gid = ec.codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                name = ec.codes_get(gid, "shortName")
                is_sea = name in sea
                for pid, lat, lon in points:
                    if is_sea:
                        value = _value_with_sea_fallback(gid, lat, lon)
                    else:
                        value = _value_at(gid, lat, lon)
                    if value is not None:
                        out[pid][name] = value
            finally:
                ec.codes_release(gid)
    return out


def read_cells(path: str, cells: list[tuple[float, float]],
               sea_params: Iterable[str] = ()) -> dict[str, list[float | None]]:
    """격자칸 여러 개의 값을 한 번에 뽑는다. (해역 색칠용)

    지점이 900개가 넘으면 read_points 의 find_nearest 방식은 느리다.
    여기서는 값 배열을 통째로 한 번만 읽고 위치를 계산해서 꺼낸다.
    (규칙 격자라 '몇 번째 칸인지'를 산수로 바로 구할 수 있다.)

    돌려주는 값: {항목이름: [칸 순서대로의 값]}
    """
    sea = set(sea_params)
    out: dict[str, list[float | None]] = {}

    with open(path, "rb") as handle:
        while True:
            gid = ec.codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                name = ec.codes_get(gid, "shortName")
                ni = ec.codes_get(gid, "Ni")
                nj = ec.codes_get(gid, "Nj")
                lat1 = ec.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                lon1 = ec.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                di = ec.codes_get(gid, "iDirectionIncrementInDegrees")
                dj = ec.codes_get(gid, "jDirectionIncrementInDegrees")
                values = ec.codes_get_array(gid, "values")
                limit = WAVE_MISSING if name in sea else None

                picked: list[float | None] = []
                for lat, lon in cells:
                    row = int(round((lat1 - lat) / dj))
                    col = int(round(((lon - lon1) % 360.0) / di))
                    if not (0 <= row < nj and 0 <= col < ni):
                        picked.append(None)
                        continue
                    value = float(values[row * ni + col])
                    if value > BIG or (limit is not None and value >= limit):
                        picked.append(None)
                    else:
                        picked.append(value)
                out[name] = picked
            finally:
                ec.codes_release(gid)
    return out


def wind_to_speed_dir(u: float, v: float) -> tuple[float, float]:
    """동서(u)·남북(v) 바람 성분을 풍속과 풍향으로 바꾼다.

    풍향은 기상 관례대로 '바람이 불어오는 방향'이다.
    예) 북풍(북에서 불어옴) = 0도
    """
    import math
    speed = math.hypot(u, v)
    direction = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    return speed, direction
