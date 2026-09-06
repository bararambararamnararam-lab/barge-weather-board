"""세 소스를 합쳐 예보 한 벌을 만든다.

  ECMWF  바람·돌풍·파고·파향·파주기·강수   하루 4회 (새 사이클일 때만)
  NOAA   시정                              ECMWF 와 같은 시각
  met.no 바람 (덮어쓰기)                    매시간

왜 ECMWF 를 매번 안 받나
-----------------------
ECMWF 는 지역을 잘라 주지 않아 전 지구 파일을 받아야 한다.
10일치 한 벌이 약 330 MB, 3~4분 걸린다.
그런데 모델은 하루 4번(00/06/12/18 UTC)만 돈다.
3시간마다 받으면 절반은 같은 자료를 다시 받는 셈이다.

그래서 마지막으로 처리한 사이클을 파일에 적어 두고,
새 사이클이 올라왔을 때만 내려받는다.
사이클이 그대로면 ECMWF·NOAA 는 건너뛰고 met.no 로 바람만 새로 고친다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, Config, Location
from . import ecmwf_open, metno, noaa

STATE_FILE = DATA_DIR / "source_state.json"


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def _cached_path() -> Path:
    return DATA_DIR / "last_forecast.json"


def _save_cache(rows: dict[tuple[str, str], dict[str, Any]], cycle: str) -> None:
    """ECMWF 결과를 다음 수집 때 재사용하려고 저장한다.

    새 사이클이 아니면 다시 받지 않고 이 값을 쓴다.
    (met.no 바람만 그 위에 새로 덮는다.)
    """
    payload = {"cycle": cycle,
               "rows": [{"loc": k[0], "time": k[1], **v} for k, v in rows.items()]}
    _cached_path().parent.mkdir(parents=True, exist_ok=True)
    _cached_path().write_text(json.dumps(payload, ensure_ascii=False,
                                         separators=(",", ":")), encoding="utf-8")


def _load_cache(cycle: str) -> dict[tuple[str, str], dict[str, Any]] | None:
    path = _cached_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("cycle") != cycle:
        return None
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload.get("rows", []):
        key = (item.pop("loc"), item.pop("time"))
        rows[key] = item
    return rows


def collect_forecast(config: Config, locations: list[Location] | None = None,
                     grid_cells: list[tuple[float, float]] | None = None,
                     verbose: bool = True) -> tuple[list[dict[str, Any]],
                                                    list[str], dict[str, Any]]:
    """예보 한 벌을 만든다.

    돌려주는 값 3개
      1) DB 에 넣을 행 목록
      2) 경고 메시지 목록
      3) 격자 원자료. 새로 받았을 때만 채워지고, 사이클이 그대로면 비어 있다.
         (비어 있으면 이미 만들어 둔 grid.json 을 그대로 쓰면 된다.)
    """
    # geometry_only(길 꺾는 점)는 기상을 받지 않는다.
    locs = locations if locations is not None else config.forecast_locations
    warnings: list[str] = []
    grid_raw: dict[str, Any] = {}
    state = _load_state()

    # --- 1) ECMWF: 새 사이클일 때만 내려받는다 ---
    cycle = ecmwf_open.latest_cycle()
    if cycle is None:
        raise RuntimeError("ECMWF 사이클을 확인하지 못했습니다. 인터넷 연결을 확인하세요.")
    cycle_key = cycle.strftime("%Y-%m-%dT%H:%M")

    rows: dict[tuple[str, str], dict[str, Any]] | None = None
    if state.get("ecmwf_cycle") == cycle_key:
        rows = _load_cache(cycle_key)
        if rows is not None and verbose:
            print("[ECMWF] 사이클 %s 는 이미 받아 둔 것을 씁니다 (새로 안 받음)"
                  % cycle_key)

    if rows is None:
        rows, cycle, grid_raw = ecmwf_open.collect(
            config, locs, grid_cells=grid_cells, verbose=verbose)
        cycle_key = cycle.strftime("%Y-%m-%dT%H:%M")

        # --- 2) NOAA 시정: ECMWF 와 같은 시각에 맞춰 받는다 ---
        try:
            steps = ecmwf_open.build_steps(config.forecast_days)
            vis = noaa.collect_visibility(config, steps, locs, verbose=verbose)
            for key, km in vis.items():
                if key in rows:
                    rows[key]["visibility_km"] = km
                    rows[key]["source_visibility"] = "NOAA GFS (public domain)"
        except Exception as exc:
            warnings.append("NOAA 시정 수집 실패(나머지는 정상): %s" % exc)

        _save_cache(rows, cycle_key)
        state["ecmwf_cycle"] = cycle_key
        state["ecmwf_fetched_at"] = datetime.now(config.timezone).strftime(
            "%Y-%m-%dT%H:%M")
        _save_state(state)

    # --- 3) met.no: 바람만 더 새 값으로 덮어쓴다 ---
    try:
        times = {t for _, t in rows}
        wind = metno.collect_wind(config, times, locs, verbose=verbose)
        replaced = 0
        for key, cell in wind.items():
            if key in rows:
                rows[key].update(cell)
                rows[key]["source_wind"] = "MET Norway (CC BY 4.0)"
                replaced += 1
        if verbose and replaced:
            print("[met.no] 바람 %d칸을 최신값으로 갱신" % replaced)
    except Exception as exc:
        warnings.append("met.no 바람 갱신 실패(ECMWF 값 유지): %s" % exc)

    # --- 4) DB 에 넣을 행 목록으로 바꾼다 ---
    collected_at = datetime.now(config.timezone).strftime("%Y-%m-%dT%H:%M")
    out: list[dict[str, Any]] = []
    for (loc_id, valid_time), cell in sorted(rows.items()):
        row: dict[str, Any] = {"collected_at": collected_at,
                               "location_id": loc_id,
                               "valid_time": valid_time}
        row.update({k: v for k, v in cell.items()
                    if not k.startswith("source_")})
        row["source_forecast"] = cell.get("source_forecast", "ECMWF IFS 0.25")
        row["source_marine"] = cell.get("source_marine", "ECMWF WAM 0.25")
        out.append(row)

    if verbose:
        print("[수집] 예보 %d행 준비 (지점 %d곳)" % (len(out), len(locs)))
    return out, warnings, grid_raw


def build_observation_rows(config: Config, forecast_rows: list[dict[str, Any]],
                           observed_at: datetime) -> list[dict[str, Any]]:
    """수집 시점의 '현재값'을 만든다.

    별도 호출 없이 방금 받은 예보에서 지금 시각에 가장 가까운 칸을 쓴다.
    관측값이 아니라 모델이 계산한 현재값이므로 data_kind 로 구분해 둔다.
    """
    key_now = observed_at.replace(minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M")
    observed_key = observed_at.strftime("%Y-%m-%dT%H:%M")

    # 예보 시각이 3시간 간격이라 '지금'과 딱 맞지 않을 수 있다.
    # 지금보다 같거나 앞선 것 중 가장 가까운 칸을 고른다.
    by_location: dict[str, dict[str, Any]] = {}
    for row in forecast_rows:
        if row["valid_time"] > key_now:
            continue
        current = by_location.get(row["location_id"])
        if current is None or row["valid_time"] > current["valid_time"]:
            by_location[row["location_id"]] = row

    out: list[dict[str, Any]] = []
    for loc_id, row in by_location.items():
        observation = {"observed_at": observed_key, "location_id": loc_id,
                       "data_kind": "model_current",
                       "source": "ECMWF + NOAA + MET Norway (모델 기반 현재값)"}
        for key, value in row.items():
            if key in ("collected_at", "location_id", "valid_time",
                       "source_forecast", "source_marine"):
                continue
            observation[key] = value
        out.append(observation)
    return out
