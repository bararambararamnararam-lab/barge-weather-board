"""SQLite 에 있는 자료를 웹 화면이 읽을 JSON 파일로 내보낸다.

만들어지는 파일 (전부 web/data/ 안)
  meta.json      설정: 항로, 지점, 한계값, 항목 목록
  forecast.json  예보 격자 (모든 지점, 3~6시간 간격, 10일)
  warnings.json  기상청 특보 현황

왜 이렇게 나누나
---------------
web/ 폴더는 '그대로 인터넷에 올릴 수 있는 정적 사이트'다.
파이썬이 도는 서버가 필요 없다. 파일만 있으면 브라우저가 알아서 그린다.
나중에 Netlify 같은 곳에 web/ 폴더를 통째로 올리면 그대로 동작한다.

용량을 줄이려고 값을 '열 단위 배열'로 담는다.
  {"wind": [6.9, 6.5, 6.4, ...]}  <- 이렇게
  [{"wind": 6.9}, {"wind": 6.5}]  <- 이렇게 하지 않는다 (3배 커진다)

운항 판단(가능/조건/불가)은 파이썬이 계산해서 글자 하나로 넣는다.
  n = 가능(normal), c = 조건(caution), u = 불가(unavailable), x = 데이터 없음
이렇게 하면 화면 쪽에서 판정 규칙을 다시 구현하지 않아도 되고,
설정 파일의 한계값을 고쳤을 때 계산 결과가 어긋날 일이 없다.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import judge
from .config import PROJECT_ROOT, Config, load_config
from .db import COLUMN_LABELS, VALUE_COLUMNS, fetch_forecast, fetch_latest_warnings, session

WEB_DIR = PROJECT_ROOT / "web"
DATA_DIR = WEB_DIR / "data"

# 화면에서 쓰는 짧은 이름 <-> DB 열 이름
SERIES_MAP: list[tuple[str, str]] = [
    ("wind", "wind_speed_ms"),
    ("wdir", "wind_direction_deg"),
    ("gust", "wind_gust_ms"),
    ("vis", "visibility_km"),
    ("prec", "precipitation_mm"),
    ("wave", "wave_height_m"),
    ("vdir", "wave_direction_deg"),
    ("vper", "wave_period_s"),
]
# 기상코드·뇌우지수·해류·수온은 내보내지 않는다.
# 지금 자료원(ECMWF·NOAA·met.no)이 주지 않아 항상 비고, 파일만 커진다.

# 화면 탭 구성. 왼쪽부터 순서대로 나온다.
METRICS: list[dict[str, Any]] = [
    {"key": "judge", "label": "운항 판단", "unit": "", "kind": "judge"},
    {"key": "wind", "label": "평균 풍속", "unit": "m/s", "kind": "number", "dir": "wdir"},
    {"key": "gust", "label": "순간 풍속", "unit": "m/s", "kind": "number"},
    {"key": "wave", "label": "유의파고", "unit": "m", "kind": "number", "dir": "vdir"},
    {"key": "vis", "label": "시정", "unit": "km", "kind": "number"},
    {"key": "prec", "label": "강수", "unit": "mm", "kind": "number"},
]
# 해류는 뺐다. ECMWF 도 NOAA 도 해류를 주지 않아 값이 항상 비기 때문이다.
# (예전 Open-Meteo 는 줬지만 라이선스 문제로 더 이상 쓰지 않는다.)

STATUS_CHAR = {
    judge.NORMAL: "n",
    judge.CAUTION: "c",
    judge.UNAVAILABLE: "u",
    judge.NO_DATA: "x",
}

# 한계값을 화면 쪽에서도 쓰도록 짧은 이름으로 다시 담는다.
THRESHOLD_KEYS = {
    "wind": "wind_speed_ms",
    "gust": "wind_gust_ms",
    "wave": "wave_height_m",
    "vis": "visibility_km",
    "prec": "precipitation_mm",
}


def _round(value: Any, digits: int = 2) -> Any:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이 거리(해리). 지구를 공으로 보고 잰 대권 거리다.

    실제 항로는 섬을 피해 돌아가므로 이보다 조금 길다.
    다만 우리 지점들이 이미 항로를 따라 찍혀 있어서, 지점 사이만
    직선으로 봐도 실제 항정과 거의 같다.
    (실제로 이 방법으로 잰 고현항~영성이 404 해리로, 실측과 일치한다.)
    """
    radius = 3440.065          # 지구 반지름을 해리로
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def route_legs(config: Config, route: Any) -> list[dict[str, Any]]:
    """항로를 이루는 구간 목록. 각 구간의 두 지점과 거리(해리).

    본선을 순서대로 잇고, 도착지는 본선 마지막 지점에서 각각 뻗는다.
    (영성 묘박지에서 영성법인·영성가야로 갈라지는 모양)
    """
    legs: list[dict[str, Any]] = []
    # path_ids 를 쓴다. 길을 꺾기 위한 점까지 들어 있어야 실제 항정이 나온다.
    # (고현항 -> 거제 동방을 직선으로 이으면 거제도 육지를 뚫는다)
    dest_set = set(route.destination_ids)
    main = [lid for lid in route.path_ids if lid not in dest_set]
    for i in range(len(main) - 1):
        a, b = config.locations[main[i]], config.locations[main[i + 1]]
        legs.append({"from": a.id, "to": b.id,
                     "nm": round(distance_nm(a.latitude, a.longitude,
                                             b.latitude, b.longitude), 1)})
    if main:
        last = config.locations[main[-1]]
        for dest_id in route.destination_ids:
            b = config.locations[dest_id]
            legs.append({"from": last.id, "to": b.id,
                         "nm": round(distance_nm(last.latitude, last.longitude,
                                                 b.latitude, b.longitude), 1)})
    return legs


def build_meta(config: Config, collected_at: str | None,
               warning_collected: str | None) -> dict[str, Any]:
    """설정과 지점 정보를 담은 meta.json 내용을 만든다."""
    voyage = config.raw.get("voyage") or {}
    locations: dict[str, Any] = {}
    # 길 꺾는 점은 화면에 안 쓰므로 내보내지 않는다.
    # (지도 선은 route.legs 의 좌표로 그린다)
    for loc in config.forecast_locations:
        locations[loc.id] = {
            "name": loc.name,
            "lat": loc.latitude,
            "lon": loc.longitude,
            "type": loc.type,
            "country": loc.country,
            "zones": list(loc.kma_zones),
        }

    routes = []
    for route_id, route in config.routes.items():
        routes.append({
            "id": route_id,
            "short": route.label,      # 화면 단추에 찍히는 이름 (영성/CSME)
            "name": route.name,
            "locations": list(route.location_ids),
            # 지도에서 선을 그릴 때 쓴다.
            # main 은 한 줄로 잇고, dests 는 main 의 마지막 지점에서 각각 뻗는다.
            "main": list(route.main_ids),
            "dests": list(route.destination_ids),
            # 구간별 거리(해리). 소요 시간 계산에 쓴다.
            "legs": route_legs(config, route),
            # 지도에 선을 그릴 좌표. 길 꺾는 점까지 들어 있어서
            # 거제도를 뚫지 않고 실제 항로대로 그려진다.
            "path": [[config.locations[lid].latitude,
                      config.locations[lid].longitude]
                     for lid in route.path_ids
                     if lid not in set(route.destination_ids)],
        })

    thresholds: dict[str, Any] = {}
    for short, column in THRESHOLD_KEYS.items():
        rule = config.thresholds.get(column) or {}
        thresholds[short] = {
            "caution_at": rule.get("caution_at"),
            "unavailable_at": rule.get("unavailable_at"),
            "caution_below": rule.get("caution_below"),
            "unavailable_below": rule.get("unavailable_below"),
            "auto": rule.get("automatic_judgement", True),
        }

    return {
        "generated_at": datetime.now(config.timezone).strftime("%Y-%m-%dT%H:%M"),
        "timezone": str(config.timezone),
        "forecast_collected_at": collected_at,
        "warning_collected_at": warning_collected,
        "routes": routes,
        "locations": locations,
        # 소요 시간 계산에 쓰는 값. 화면에서 그때그때 계산한다.
        "voyage": {
            "vessels": [
                {"id": str(v.get("id", "")),
                 "name": str(v.get("name", "")),
                 "short": str(v.get("short", v.get("name", ""))),
                 "speed_kn": float(v.get("speed_kn", 6.5))}
                for v in (voyage.get("vessels") or [])
            ],
            "caution_factor": float(voyage.get("caution_factor", 0.75)),
            "wait_when_unavailable": bool(voyage.get("wait_when_unavailable", True)),
            "max_wait_hours": float(voyage.get("max_wait_hours", 72)),
        },
        "metrics": METRICS,
        "thresholds": thresholds,
        "labels": {short: COLUMN_LABELS.get(col, short)
                   for short, col in SERIES_MAP},
        "units": {short: VALUE_COLUMNS.get(col, "")
                  for short, col in SERIES_MAP},
        "wmo": judge.WMO_CODES,
        "status_labels": {
            "n": "가능", "c": "조건", "u": "불가", "x": "데이터 없음",
        },
        "metric_status_labels": {
            "n": "정상", "c": "주의", "u": "불가", "x": "데이터 없음",
        },
    }


def build_forecast(config: Config, rows: list[dict[str, Any]],
                   warning_rows: list[dict[str, Any]],
                   times: list[str]) -> dict[str, Any]:
    """예보 격자를 열 단위 배열로 담은 forecast.json 내용을 만든다."""
    index = {t: i for i, t in enumerate(times)}
    by_location: dict[str, dict[str, Any]] = {}

    for loc_id in config.locations:
        series: dict[str, list[Any]] = {
            short: [None] * len(times) for short, _ in SERIES_MAP
        }
        series["st"] = ["x"] * len(times)
        by_location[loc_id] = series

    warnings_cache: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        loc_id = row["location_id"]
        slot = index.get(row["valid_time"])
        if slot is None or loc_id not in by_location:
            continue

        target = by_location[loc_id]
        for short, column in SERIES_MAP:
            digits = 0 if column == "weather_code" else 2
            target[short][slot] = _round(row.get(column), digits)

        if loc_id not in warnings_cache:
            warnings_cache[loc_id] = judge.warnings_for_location(
                config, warning_rows, loc_id)
        hits = judge.active_warnings_at(
            config, warnings_cache[loc_id], row["valid_time"])
        verdict = judge.judge_cell(config, row, hits)
        target["st"][slot] = STATUS_CHAR[verdict.status]

    # 지점별로 지금 걸려 있는 특보 요약도 같이 넣는다.
    active: dict[str, list[dict[str, Any]]] = {}
    for loc_id in config.locations:
        applicable = judge.warnings_for_location(config, warning_rows, loc_id)
        active[loc_id] = [
            {
                "reg_id": w.get("reg_id"),
                "reg_ko": w.get("reg_ko"),
                "wrn": w.get("wrn"),
                "lvl": w.get("lvl"),
                "cmd": w.get("cmd"),
                "tm_ef": w.get("tm_ef"),
                "ed_tm": w.get("ed_tm"),
            }
            for w in applicable
        ]

    return {
        "times": times,
        "series": by_location,
        "warnings_by_location": active,
    }


def export(config: Config | None = None, verbose: bool = True) -> dict[str, Any]:
    """JSON 파일 3개를 web/data/ 에 쓴다."""
    cfg = config or load_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(cfg.timezone).replace(minute=0, second=0, microsecond=0)
    valid_from = now.strftime("%Y-%m-%dT%H:%M")
    valid_to = (now + timedelta(days=cfg.forecast_days)).strftime("%Y-%m-%dT%H:%M")

    with session() as conn:
        rows = [dict(r) for r in fetch_forecast(
            conn, list(cfg.locations), None, valid_from, valid_to)]
        warning_collected, warning_raw = fetch_latest_warnings(conn)
        warning_rows = [dict(r) for r in warning_raw]

    if not rows:
        raise RuntimeError(
            "내보낼 예보가 없습니다. 1_수집하기.bat 을 먼저 실행하세요.")

    times = sorted({r["valid_time"] for r in rows})
    collected_at = max(r["collected_at"] for r in rows)

    meta = build_meta(cfg, collected_at, warning_collected)
    forecast = build_forecast(cfg, rows, warning_rows, times)
    warnings_payload = {
        "collected_at": warning_collected,
        "rows": [
            {
                "reg_up_ko": w.get("reg_up_ko"),
                "reg_id": w.get("reg_id"),
                "reg_ko": w.get("reg_ko"),
                "wrn": w.get("wrn"),
                "lvl": w.get("lvl"),
                "cmd": w.get("cmd"),
                "tm_fc": w.get("tm_fc"),
                "tm_ef": w.get("tm_ef"),
                "ed_tm": w.get("ed_tm"),
            }
            for w in warning_rows
        ],
    }

    written: dict[str, int] = {}
    for name, payload in (("meta.json", meta),
                          ("forecast.json", forecast),
                          ("warnings.json", warnings_payload)):
        path = DATA_DIR / name
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        path.write_text(text, encoding="utf-8")
        written[name] = len(text.encode("utf-8"))

    if verbose:
        total = sum(written.values())
        print("[웹] JSON 내보내기 완료 ({:,} bytes)".format(total))
        for name, size in written.items():
            print("      {:<16} {:>9,} bytes".format(name, size))
        print("      위치: {}".format(DATA_DIR))

    return {"files": written, "times": len(times), "rows": len(rows)}


def write_grid(payload: dict[str, Any] | None, verbose: bool = True) -> int:
    """격자 자료를 web/data/grid.json 으로 쓴다.

    payload 가 None 이면(격자 기능이 꺼져 있으면) 기존 파일을 지운다.
    안 지우면 꺼 놓고도 옛 격자가 계속 화면에 남는다.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "grid.json"

    if payload is None:
        if path.exists():
            path.unlink()
            if verbose:
                print("[격자] 기능이 꺼져 있어 grid.json 을 지웠습니다.")
        return 0

    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    size = len(text.encode("utf-8"))
    if verbose:
        print("[격자] grid.json 저장 ({:,} bytes, 칸 {}개)".format(
            size, len(payload.get("cells", []))))
    return size


def main(argv: list[str] | None = None) -> int:
    try:
        config = load_config()
    except Exception as exc:
        print("설정 파일을 읽지 못했습니다.\n{}".format(exc))
        return 1
    try:
        export(config)
    except Exception as exc:
        print("내보내기 실패: {}".format(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
