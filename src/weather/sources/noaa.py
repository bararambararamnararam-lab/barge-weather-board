"""미국 해양대기청(NOAA) GFS 에서 시정을 받아 온다.

왜 NOAA 인가
------------
ECMWF 공개자료에는 시정(visibility)이 없다. 시정은 항만 접근과
피항 판단에 쓰이는 항목이라 뺄 수 없어서 NOAA 에서 따로 받는다.

  NOAA 자료는 미국 정부 공공저작물이다. 저작권이 없고 상업 사용도 자유다.
  키가 필요 없고 사용량 제한도 없다.

ECMWF 와 다른 점: NOAA 는 '지역을 잘라서' 준다.
우리 해역만 요청하면 한 스텝에 수 KB 밖에 안 된다. 그래서 가볍다.
"""

from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from ..config import Config, Location
from . import gribtools

FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
WORKERS = 6

# 우리 항로를 넉넉히 감싸는 사각형. 이 범위만 잘라 받는다.
REGION = {"toplat": 40, "bottomlat": 30, "leftlon": 118, "rightlon": 132}


def latest_cycle(session: requests.Session | None = None) -> tuple[str, str] | None:
    """받을 수 있는 가장 최근 GFS 사이클 (날짜, 시각)."""
    s = session or requests.Session()
    now = datetime.now(timezone.utc)
    for back in range(0, 30, 6):
        t = now - timedelta(hours=back + 5)   # 발표 지연 감안
        day, cyc = t.strftime("%Y%m%d"), "%02d" % (t.hour // 6 * 6)
        try:
            r = s.get(FILTER_URL, params={
                "dir": "/gfs.%s/%s/atmos" % (day, cyc),
                "file": "gfs.t%sz.pgrb2.0p25.f000" % cyc,
                "var_VIS": "on", "lev_surface": "on",
                "subregion": "", **REGION}, timeout=60)
            if r.content[:4] == b"GRIB":
                return day, cyc
        except Exception:
            continue
    return None


def _fetch_step(session: requests.Session, day: str, cyc: str, step: int,
                points: list[tuple[str, float, float]],
                workdir: str) -> tuple[int, dict[str, float]]:
    path = os.path.join(workdir, "gfs_vis_%03d.grib2" % step)
    out: dict[str, float] = {}
    try:
        r = session.get(FILTER_URL, params={
            "dir": "/gfs.%s/%s/atmos" % (day, cyc),
            "file": "gfs.t%sz.pgrb2.0p25.f%03d" % (cyc, step),
            "var_VIS": "on", "lev_surface": "on",
            "subregion": "", **REGION}, timeout=90)
        if r.content[:4] != b"GRIB":
            return step, out
        with open(path, "wb") as fh:
            fh.write(r.content)
        got = gribtools.read_points(path, points)
        for pid, values in got.items():
            if "vis" in values:
                # GFS 시정 단위는 m. km 로 바꾼다.
                out[pid] = round(values["vis"] / 1000.0, 3)
    except Exception:
        pass
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    return step, out


def collect_visibility(config: Config, steps: list[int],
                       locations: list[Location] | None = None,
                       verbose: bool = True) -> dict[tuple[str, str], float]:
    """시정을 받아 {(지점ID, 유효시각): km} 로 돌려준다.

    steps 는 ECMWF 와 같은 예보 시간 목록을 넣는다.
    GFS 는 120시간까지 1시간 간격, 그 뒤 3시간 간격이라
    ECMWF 의 3/6시간 스텝은 전부 존재한다.
    """
    locs = locations if locations is not None else list(config.locations.values())
    points = [(loc.id, loc.latitude, loc.longitude) for loc in locs]

    session = requests.Session()
    session.headers.update({"User-Agent": "barge-weather/1.0"})
    cycle = latest_cycle(session)
    if cycle is None:
        raise RuntimeError("NOAA GFS 사이클을 확인하지 못했습니다.")
    day, cyc = cycle

    if verbose:
        print("[NOAA] 시정 수집: 사이클 %s %sz, 스텝 %d개" % (day, cyc, len(steps)))

    base = datetime.strptime(day + cyc, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    workdir = tempfile.mkdtemp(prefix="gfsvis_")
    out: dict[tuple[str, str], float] = {}
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(_fetch_step, session, day, cyc, s, points, workdir)
                       for s in steps]
            for fut in futures:
                step, values = fut.result()
                key_time = (base + timedelta(hours=step)).astimezone(
                    config.timezone).strftime("%Y-%m-%dT%H:%M")
                for pid, km in values.items():
                    out[(pid, key_time)] = km
    finally:
        try:
            os.rmdir(workdir)
        except OSError:
            pass

    if verbose:
        print("[NOAA] 시정 %d칸 확보" % len(out))
    return out
