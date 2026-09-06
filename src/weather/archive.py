"""원본 스냅샷을 '하루 1개짜리 아카이브'로 줄이는 부분.

왜 필요한가
-----------
원본 스냅샷은 3시간마다(하루 8회) 20지점 × 336시간 = 하루 53,760행씩 쌓인다.
그대로 1년 두면 20 GB 가까이 된다. 그래서 이렇게 줄인다.

  1) 하루에 한 번, 한국시간 09시에 가장 가까운 수집분 하나만 고른다.
  2) 항목을 7개로 줄인다(풍속·풍향·순간풍속·시정·유의파고 + 운항 판단 + 특보 요약).
  3) 시간 간격을 3시간으로 벌린다.
  4) 예보 범위를 10일 → 7일로 자른다.

결과: 하루 20지점 × 56칸 = 1,120행. 1년 40만 행 정도.

언제 도는가
-----------
예보를 수집할 때마다 자동으로 돈다(collect.py).
'아직 아카이브 안 만든 지난 날짜'를 찾아서 만들고, 그다음 원본을 지운다.
그래서 컴퓨터를 며칠 꺼 뒀다 켜도 빠진 날이 알아서 채워진다.

★ 오늘 날짜는 아카이브하지 않는다. 하루가 다 지나야 09시 수집분이 확정되기 때문이다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from . import judge
from .config import Config
from .db import (
    ARCHIVE_VALUE_COLUMNS,
    archived_dates_set,
    fetch_warnings_seen_at,
    insert_archive_rows,
    list_collected_at,
)

DEFAULTS = {
    "target_hour": 9,
    "hour_window": 6,
    "forecast_days": 7,
    "step_hours": 3,
}


def _settings(config: Config) -> dict[str, int]:
    raw = config.raw.get("archive") or {}
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if raw.get(key) is not None:
            out[key] = int(raw[key])
    return out


def pick_daily_snapshot(collected_list: list[str], day: str,
                        target_hour: int, hour_window: int) -> str | None:
    """어느 하루(day='2026-09-06')의 수집 시각들 중 대표 하나를 고른다.

    목표 시각(기본 09시)에 가장 가까운 것을 고른다.
    목표에서 hour_window 시간을 넘게 벗어나면 그날은 대표가 없다고 본다.
    (그날 수집이 아예 안 돌았거나 새벽에 한 번만 돈 경우.)
    """
    same_day = [value for value in collected_list if value.startswith(day)]
    if not same_day:
        return None

    def distance(value: str) -> float:
        hour = int(value[11:13])
        minute = int(value[14:16])
        return abs((hour + minute / 60.0) - target_hour)

    best = min(same_day, key=distance)
    return best if distance(best) <= hour_window else None


def _warning_summary(hits: list[judge.WarningHit]) -> str:
    """특보 목록을 한 줄 글자로 줄인다. 예) '강풍 예비; 풍랑 주의'"""
    if not hits:
        return ""
    seen: list[str] = []
    for hit in hits:
        text = "{} {}".format(hit.wrn, hit.lvl).strip()
        if text and text not in seen:
            seen.append(text)
    return "; ".join(seen)


def build_archive_for_day(
    conn: sqlite3.Connection,
    config: Config,
    day: str,
    collected_at: str,
) -> int:
    """하루치 아카이브 행을 만들어 저장한다. 저장한 행 수를 돌려준다."""
    options = _settings(config)
    step = max(1, options["step_hours"])
    horizon = options["forecast_days"]

    # 수집한 시각부터 horizon 일 뒤까지만 남긴다.
    # 수집 시각보다 앞선 칸(그날 00시·03시 등)은 이미 지나간 시각이라
    # '그때 예보가 어땠나'를 보는 데 쓸모가 없으므로 버린다.
    base = datetime.strptime(collected_at, "%Y-%m-%dT%H:%M")
    valid_from = base.replace(minute=0).strftime("%Y-%m-%dT%H:%M")
    valid_to = (base + timedelta(days=horizon)).strftime("%Y-%m-%dT%H:%M")

    rows = conn.execute(
        "SELECT * FROM forecast_snapshot WHERE collected_at = ? "
        "AND valid_time >= ? AND valid_time <= ? "
        "ORDER BY location_id, valid_time",
        (collected_at, valid_from, valid_to),
    ).fetchall()
    if not rows:
        return 0

    # 그 시점에 발효 중이던 특보를 한 번만 읽어 재사용한다.
    warning_rows = [dict(r) for r in fetch_warnings_seen_at(conn, collected_at)]

    warnings_by_location: dict[str, list[dict[str, Any]]] = {}
    archive_rows: list[dict[str, Any]] = []

    for record in rows:
        valid_time = record["valid_time"]
        if int(valid_time[11:13]) % step != 0:
            continue

        location_id = record["location_id"]
        if location_id not in warnings_by_location:
            warnings_by_location[location_id] = judge.warnings_for_location(
                config, warning_rows, location_id)

        values = dict(record)
        hits = judge.active_warnings_at(
            config, warnings_by_location[location_id], valid_time)
        verdict = judge.judge_cell(config, values, hits)

        row: dict[str, Any] = {
            "archive_date": day,
            "location_id": location_id,
            "valid_time": valid_time,
            "collected_at": collected_at,
            "judgement": verdict.status,
            "warning_summary": _warning_summary(hits),
        }
        for column in ARCHIVE_VALUE_COLUMNS:
            row[column] = values.get(column)
        archive_rows.append(row)

    return insert_archive_rows(conn, archive_rows)


def build_pending_archives(
    conn: sqlite3.Connection,
    config: Config,
    now: datetime,
    verbose: bool = True,
) -> dict[str, Any]:
    """아직 아카이브 안 만든 '지난 날짜'를 모두 찾아 만든다."""
    options = _settings(config)
    today = now.strftime("%Y-%m-%d")

    collected_list = list_collected_at(conn, limit=100000)
    if not collected_list:
        return {"days": 0, "rows": 0, "detail": []}

    done = archived_dates_set(conn)
    # 오늘은 아직 하루가 안 끝났으므로 건너뛴다.
    candidate_days = sorted({value[:10] for value in collected_list
                             if value[:10] < today and value[:10] not in done})

    total_rows = 0
    detail: list[str] = []
    for day in candidate_days:
        chosen = pick_daily_snapshot(
            collected_list, day, options["target_hour"], options["hour_window"])
        if not chosen:
            detail.append("{}: 09시 근방 수집분이 없어 건너뜀".format(day))
            continue
        written = build_archive_for_day(conn, config, day, chosen)
        total_rows += written
        detail.append("{}: {} 스냅샷에서 {}행".format(
            day, chosen.replace("T", " "), written))

    if verbose and detail:
        for line in detail:
            print("[아카이브] {}".format(line))

    return {"days": len(candidate_days), "rows": total_rows, "detail": detail}


def archive_row_to_cell(row: dict[str, Any]) -> dict[str, Any]:
    """아카이브 행을 화면 표가 쓰는 모양(원본 스냅샷과 같은 키)으로 바꾼다.

    아카이브에 없는 항목은 None 으로 채워 회색(데이터 없음)으로 나오게 한다.
    """
    cell = dict(row)
    for column in ("precipitation_mm", "weather_code", "cape_jkg",
                   "wave_direction_deg", "wave_period_s",
                   "current_speed_kn", "current_direction_deg",
                   "sea_surface_temp_c"):
        cell.setdefault(column, None)
    return cell
