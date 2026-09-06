"""유럽중기예보센터(ECMWF) 무료 공개자료에서 예보를 받아 온다.

왜 ECMWF 인가
-------------
Open-Meteo 무료 API 는 비상업적 용도만 허용된다. 실제 회사 업무에 쓰려면
라이선스를 해결해야 한다. ECMWF Open Data 는 CC BY 4.0 이라
출처만 밝히면 상업적으로도 쓸 수 있다. 전 세계를 다루므로 중국 해역도 나온다.

  출처 표시 문구: "Data from ECMWF (CC BY 4.0)"

받는 항목
---------
  oper 스트림 : 10u, 10v (10m 바람), 10fg (돌풍), tp (누적 강수)
  wave 스트림 : swh (유의파고), mwd (파향), mwp (파주기)

시정은 ECMWF 가 주지 않는다. NOAA 에서 따로 받는다(noaa.py).
해류는 둘 다 주지 않는다. 값이 비게 된다.

용량 주의
---------
ECMWF 는 지역을 잘라 주지 않는다. 우리 지점 15곳만 필요해도 전 지구
파일을 받아야 한다. 10일치 53스텝이면 약 330 MB, 12분쯤 걸린다.

그래서 '새 사이클이 올라왔을 때만' 받는다. ECMWF 모델은 하루 4번
(00/06/12/18 UTC) 돌기 때문에, 3시간마다 받아도 절반은 같은 자료다.
"""

from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from ecmwf.opendata import Client

from ..config import Config, Location
from . import gribtools

# 바람과 강수. 이 셋은 모든 스텝에 반드시 있다.
OPER_PARAMS = ["10u", "10v", "tp"]
WAVE_PARAMS = ["swh", "mwd", "mwp"]
SEA_PARAMS = {"swh", "mwd", "mwp"}

# 돌풍은 이름이 두 가지다. 아래 gust_param() 참고.
GUST_NAMES = ("10fg", "10fg3")

# 다운로드 동시 실행 수.
# 구글 복제본은 클라우드 스토리지라 동시 접속 제한이 사실상 없다.
# 실제로 재 보니 (스텝 12개 기준, 53스텝 환산):
#     4개  169초
#     8개  114초
#    12개   74초   <- 채택
#    16개   75초   (더 늘려도 그대로. 여기서 한계)
WORKERS = 12

# 자료를 어디서 받을지의 기본값. 설정 파일에서 바꿀 수 있다.
DEFAULT_SOURCE = "google"
DEFAULT_RETRY_WAIT = 20
DEFAULT_MAX_RETRIES = 15


def make_client(config: Config | None = None) -> Client:
    """자료를 받아 올 통로를 만든다.

    ECMWF 본서버는 전 세계가 같이 쓰다 보니 자주 "429 Too Many Requests" 로
    막힌다. 막히면 기본 설정상 120초를 기다렸다 다시 받는데, 스텝이 53개나
    되다 보니 수집이 몇 분씩 늘어났다.

    같은 자료가 구글·아마존·애저 클라우드에 그대로 복제돼 있고 그쪽은
    이런 제한이 없다. 그래서 기본을 구글로 두고, 막혔을 때 기다리는 시간도
    120초에서 20초로 줄였다.
    """
    opts = (config.raw.get("ecmwf_download") if config else None) or {}
    return Client(
        source=str(opts.get("source", DEFAULT_SOURCE)),
        retry_after=int(opts.get("retry_wait_seconds", DEFAULT_RETRY_WAIT)),
        maximum_retries=int(opts.get("max_retries", DEFAULT_MAX_RETRIES)),
    )


def gust_param(step: int) -> str:
    """그 스텝에서 돌풍 항목이 어떤 이름으로 들어 있는지.

    ECMWF 는 구간마다 다른 이름을 쓴다. 2026-09-06 에 직접 확인한 결과:

        0 ~ 90h    : 10fg
        96 ~ 144h  : 10fg3
        150h 이상  : 10fg

    이름을 틀리면 그 요청 전체가 거부된다("No index entries for param=10fg").
    예전에는 돌풍을 바람과 같은 요청에 묶어 뒀던 탓에, 돌풍 이름이 틀린
    스텝에서는 바람과 강수까지 통째로 빠졌다. 지금은 아래 _fetch_step 이
    한 번 더 시도해서 최소한 바람은 반드시 건지도록 해 뒀다.
    """
    return "10fg3" if 96 <= step <= 144 else "10fg"


def build_steps(forecast_days: int, fine_hours: int = 72,
                fine_step: int = 3, coarse_step: int = 6) -> list[int]:
    """받을 예보 시간(스텝) 목록.

    앞쪽(기본 72시간)은 3시간 간격, 그 뒤는 6시간 간격으로 띄운다.
    출항 판단이 중요한 가까운 날짜는 촘촘히, 먼 날짜는 추세만 본다.
    """
    total = forecast_days * 24
    steps = list(range(0, min(fine_hours, total) + 1, fine_step))
    start = ((min(fine_hours, total) // coarse_step) + 1) * coarse_step
    steps += list(range(start, total + 1, coarse_step))
    return sorted(set(s for s in steps if s <= total))


def latest_cycle(client: Client | None = None,
                 config: Config | None = None) -> datetime | None:
    """지금 받을 수 있는 가장 최근 ECMWF 사이클(UTC)."""
    try:
        return (client or make_client(config)).latest(type="fc")
    except Exception:
        return None


def _fetch_step(client: Client, step: int, points: list[tuple[str, float, float]],
                cells: list[tuple[float, float]],
                workdir: str) -> tuple[int, dict[str, dict[str, float]],
                                       dict[str, list[float | None]]]:
    """스텝 하나를 받아 지점 값과 격자 값을 뽑고 파일은 지운다.

    격자(해역 색칠)도 같은 파일에서 뽑는다. 어차피 전 지구 파일을 받았으니
    격자를 추가로 내려받을 필요가 없다. 다운로드가 늘지 않는다.
    """
    merged: dict[str, dict[str, float]] = {pid: {} for pid, _, _ in points}
    grid: dict[str, list[float | None]] = {}

    def pull(name: str, params: list[str], stream: str) -> bool:
        """한 묶음을 받아서 값을 뽑는다. 성공하면 True."""
        path = os.path.join(workdir, "ec_%s_%03d.grib2" % (name, step))
        try:
            request: dict[str, Any] = {"type": "fc", "step": step, "param": params}
            if stream != "oper":
                request["stream"] = stream
            client.retrieve(target=path, **request)
            got = gribtools.read_points(path, points, sea_params=SEA_PARAMS)
            for pid, values in got.items():
                merged[pid].update(values)
            if cells:
                grid.update(gribtools.read_cells(path, cells,
                                                 sea_params=SEA_PARAMS))
            return True
        except Exception:
            # 한 묶음이 실패해도 나머지는 살린다. 그 항목만 비게 된다.
            return False
        finally:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    # 바람·강수에 그 스텝에 맞는 돌풍 이름을 얹어 한 번에 받는다.
    if not pull("oper", OPER_PARAMS + [gust_param(step)], "oper"):
        # 돌풍 이름이 예상과 달라 거부됐을 수 있다.
        # 돌풍을 빼고 다시 받아 바람과 강수만이라도 반드시 확보한다.
        pull("operbase", OPER_PARAMS, "oper")
        # 그러고 나서 다른 이름으로 돌풍만 따로 시도해 본다.
        for name in GUST_NAMES:
            if name != gust_param(step) and pull("gust", [name], "oper"):
                break

    pull("wave", WAVE_PARAMS, "wave")

    # 돌풍은 어느 이름으로 들어왔든 '10fg' 하나로 맞춰 둔다.
    # 이렇게 해야 이 함수를 쓰는 쪽에서 이름을 신경 쓰지 않아도 된다.
    for values in merged.values():
        if "10fg" not in values and "10fg3" in values:
            values["10fg"] = values.pop("10fg3")
    if "10fg" not in grid and "10fg3" in grid:
        grid["10fg"] = grid.pop("10fg3")

    return step, merged, grid


def collect(config: Config, locations: list[Location] | None = None,
            grid_cells: list[tuple[float, float]] | None = None,
            verbose: bool = True) -> tuple[dict[tuple[str, str], dict[str, Any]],
                                           datetime,
                                           dict[str, Any]]:
    """ECMWF 예보를 받는다.

    돌려주는 값 3개
      1) {(지점ID, 유효시각): {열이름: 값}}
      2) 사용한 사이클 시각
      3) 격자 자료 {"times": [...], "series": {항목: [스텝][칸]}}
         grid_cells 를 주지 않으면 빈 값이다.
    """
    # geometry_only(길 꺾는 점)는 기상을 받지 않는다.
    locs = locations if locations is not None else config.forecast_locations
    points = [(loc.id, loc.latitude, loc.longitude) for loc in locs]

    client = make_client(config)
    cycle = latest_cycle(client)
    if cycle is None:
        raise RuntimeError("ECMWF 사이클을 확인하지 못했습니다. 인터넷 연결을 확인하세요.")

    steps = build_steps(config.forecast_days)
    if verbose:
        print("[ECMWF] 사이클 %s UTC, 스텝 %d개 (%d일치) 내려받기 시작"
              % (cycle.strftime("%Y-%m-%d %H:%M"), len(steps), config.forecast_days))

    cells = grid_cells or []
    workdir = tempfile.mkdtemp(prefix="ecmwf_")
    results: dict[int, dict[str, dict[str, float]]] = {}
    grids: dict[int, dict[str, list[float | None]]] = {}
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(_fetch_step, client, s, points, cells, workdir)
                       for s in steps]
            done = 0
            for fut in futures:
                step, values, grid = fut.result()
                results[step] = values
                if grid:
                    grids[step] = grid
                done += 1
                if verbose and done % 10 == 0:
                    print("        %d/%d 스텝" % (done, len(steps)))
    finally:
        try:
            os.rmdir(workdir)
        except OSError:
            pass

    # 스텝 -> 유효시각(KST 문자열)
    tz = config.timezone
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    prev_tp: dict[str, float] = {}

    for step in steps:
        valid_utc = cycle.replace(tzinfo=timezone.utc) + timedelta(hours=step)
        key_time = valid_utc.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        got = results.get(step, {})

        for loc in locs:
            values = got.get(loc.id) or {}
            cell: dict[str, Any] = {}

            u, v = values.get("10u"), values.get("10v")
            if u is not None and v is not None:
                speed, direction = gribtools.wind_to_speed_dir(u, v)
                cell["wind_speed_ms"] = round(speed, 2)
                cell["wind_direction_deg"] = round(direction, 1)

            if values.get("10fg") is not None:
                cell["wind_gust_ms"] = round(values["10fg"], 2)
            if values.get("swh") is not None:
                cell["wave_height_m"] = round(values["swh"], 2)
            if values.get("mwd") is not None:
                cell["wave_direction_deg"] = round(values["mwd"], 1)
            if values.get("mwp") is not None:
                cell["wave_period_s"] = round(values["mwp"], 2)

            # tp 는 예보 시작부터의 '누적' 강수(m)다.
            # 그 칸의 강수량을 알려면 앞 스텝과의 차이를 내야 한다.
            if values.get("tp") is not None:
                total_mm = values["tp"] * 1000.0
                before = prev_tp.get(loc.id)
                step_mm = total_mm if before is None else max(0.0, total_mm - before)
                prev_tp[loc.id] = total_mm
                cell["precipitation_mm"] = round(step_mm, 2)

            if cell:
                cell["source_forecast"] = "ECMWF IFS 0.25 (CC BY 4.0)"
                cell["source_marine"] = "ECMWF WAM 0.25 (CC BY 4.0)"
                rows[(loc.id, key_time)] = cell

    # 격자 자료를 스텝 순서대로 정리한다.
    grid_out: dict[str, Any] = {}
    if cells:
        times = []
        series: dict[str, list[list[float | None]]] = {}
        for step in steps:
            valid_utc = cycle.replace(tzinfo=timezone.utc) + timedelta(hours=step)
            times.append(valid_utc.astimezone(tz).strftime("%Y-%m-%dT%H:%M"))
            got = grids.get(step, {})
            for name in ("10u", "10v", "10fg", "swh"):
                series.setdefault(name, []).append(
                    got.get(name) or [None] * len(cells))
        grid_out = {"times": times, "series": series}

    if verbose:
        print("[ECMWF] 완료: 지점 %d곳 x 시각 %d칸 = %d개%s"
              % (len(locs), len(steps), len(rows),
                 ", 격자 %d칸" % len(cells) if cells else ""))
    return rows, cycle, grid_out
