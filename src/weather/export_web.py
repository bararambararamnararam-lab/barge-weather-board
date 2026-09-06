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


def build_meta(config: Config, collected_at: str | None,
               warning_collected: str | None) -> dict[str, Any]:
    """설정과 지점 정보를 담은 meta.json 내용을 만든다."""
    locations: dict[str, Any] = {}
    for loc in config.locations.values():
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
