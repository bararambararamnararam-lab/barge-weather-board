"""노르웨이 기상청(met.no)에서 바람을 받아 온다. — 보조 소스

왜 보조로 쓰나
-------------
ECMWF 는 하루 4번(6시간마다)만 갱신된다. 그 사이에는 자료가 최대 6시간 낡는다.
met.no 는 **매시간** 갱신되므로, 같은 시각의 바람 값을 더 새 것으로 덮어쓴다.

  파고는 met.no 가 주지 않는다(북유럽 해역만 제공).
  돌풍도 우리 지역에는 없다.
  그래서 '바람(풍속·풍향)만' 덮어쓰고 나머지는 ECMWF 값을 그대로 둔다.

라이선스: CC BY 4.0. 출처만 밝히면 상업적으로도 쓸 수 있다.
  표시 문구: "Data from MET Norway (CC BY 4.0)"

지켜야 할 규칙
-------------
met.no 는 User-Agent 에 연락처를 넣으라고 요구한다. 안 넣으면 403 을 준다.
지점 수만큼만 부르고(15회), 과하게 두드리지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from ..config import Config, Location

URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"

# met.no 이용 약관상 연락처를 넣어야 한다. 개인 이메일 대신 저장소 주소를 쓴다.
USER_AGENT = "barge-weather-board/1.0 (+https://github.com/bararambararamnararam-lab/barge-weather-board)"


def collect_wind(config: Config, valid_times: set[str],
                 locations: list[Location] | None = None,
                 verbose: bool = True) -> dict[tuple[str, str], dict[str, Any]]:
    """지점별 바람을 받아 {(지점ID, 유효시각): {풍속, 풍향}} 으로 돌려준다.

    valid_times 에 있는 시각만 남긴다. ECMWF 가 만든 시간축에 맞추기 위해서다.
    (met.no 는 1시간 간격이라 ECMWF 의 3/6시간 시각을 모두 포함한다.)
    """
    locs = locations if locations is not None else list(config.locations.values())
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    tz = config.timezone

    out: dict[tuple[str, str], dict[str, Any]] = {}
    failed = 0

    for loc in locs:
        try:
            response = session.get(URL, params={
                "lat": round(loc.latitude, 4),
                "lon": round(loc.longitude, 4),
            }, timeout=45)
            response.raise_for_status()
            series = response.json()["properties"]["timeseries"]
        except Exception:
            failed += 1
            continue

        for entry in series:
            try:
                moment = datetime.strptime(entry["time"], "%Y-%m-%dT%H:%M:%SZ")
            except (KeyError, ValueError):
                continue
            key_time = moment.replace(tzinfo=timezone.utc).astimezone(tz).strftime(
                "%Y-%m-%dT%H:%M")
            if key_time not in valid_times:
                continue

            details = entry.get("data", {}).get("instant", {}).get("details", {})
            speed = details.get("wind_speed")
            direction = details.get("wind_from_direction")
            if speed is None:
                continue

            cell: dict[str, Any] = {"wind_speed_ms": round(float(speed), 2)}
            if direction is not None:
                cell["wind_direction_deg"] = round(float(direction), 1)
            out[(loc.id, key_time)] = cell

    if verbose:
        note = " (%d곳 실패)" % failed if failed else ""
        print("[met.no] 바람 %d칸 확보%s" % (len(out), note))
    return out
