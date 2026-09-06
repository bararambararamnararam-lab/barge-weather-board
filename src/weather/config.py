"""설정 파일(config/weather_config.yaml)을 읽어 들이는 모듈.

프로그램의 다른 모든 부분은 여기를 통해서만 설정을 읽는다.
설정 파일에 오타가 있으면 여기서 사람이 읽을 수 있는 오류 메시지를 낸다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

# 프로젝트 최상위 폴더 (이 파일 기준으로 두 단계 위 = claude-weather/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "weather_config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "weather.db"
LOG_DIR = DATA_DIR / "logs"


class ConfigError(Exception):
    """설정 파일이 잘못됐을 때 내는 오류."""


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    latitude: float
    longitude: float
    type: str
    country: str
    kma_zones: tuple[str, ...]


@dataclass(frozen=True)
class Route:
    id: str
    name: str
    location_ids: tuple[str, ...]   # 화면 목록·표에 쓰는 전체 순서 (본선 + 도착지)
    short: str = ""                 # 화면 단추에 쓰는 짧은 이름 (예: 영성, CSME)
    main_ids: tuple[str, ...] = ()  # 한 줄로 이어지는 본선 경로
    destination_ids: tuple[str, ...] = ()  # 본선 끝에서 갈라지는 도착지들

    @property
    def label(self) -> str:
        """사람에게 보여 줄 이름. short 가 없으면 ID 를 쓴다."""
        return self.short or self.id

    @property
    def branch_point(self) -> str | None:
        """가지가 갈라지는 지점. 본선의 마지막 지점이다."""
        return self.main_ids[-1] if self.main_ids else None


@dataclass
class Config:
    raw: dict[str, Any]
    locations: dict[str, Location] = field(default_factory=dict)
    routes: dict[str, Route] = field(default_factory=dict)

    # ---------- 자주 쓰는 값들을 편하게 꺼내 쓰는 속성 ----------
    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.raw["settings"].get("timezone", "Asia/Seoul"))

    @property
    def forecast_days(self) -> int:
        return int(self.raw["settings"].get("forecast_days", 14))

    @property
    def default_display_resolution(self) -> str:
        return str(self.raw["settings"].get("default_display_resolution", "3h"))

    @property
    def collection(self) -> dict[str, Any]:
        return self.raw.get("collection", {})

    @property
    def retention(self) -> dict[str, Any]:
        return self.raw.get("retention", {})

    @property
    def thresholds(self) -> dict[str, Any]:
        return self.raw.get("operational_thresholds", {})

    @property
    def warning_rules(self) -> dict[str, Any]:
        return self.raw.get("kma_warning_rules", {})

    def route_locations(self, route_id: str) -> list[Location]:
        """항로 ID를 주면 그 항로에 포함된 위치 객체를 항해 순서대로 돌려준다."""
        route = self.routes[route_id]
        return [self.locations[lid] for lid in route.location_ids]

    def zone_to_locations(self) -> dict[str, list[str]]:
        """특보구역 코드 -> 그 구역이 적용되는 위치 ID 목록."""
        mapping: dict[str, list[str]] = {}
        for loc in self.locations.values():
            for zone in loc.kma_zones:
                mapping.setdefault(zone, []).append(loc.id)
        return mapping


def load_config(path: Path | str | None = None) -> Config:
    """설정 파일을 읽어 Config 객체로 만든다."""
    config_path = Path(path) if path else CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"설정 파일을 찾을 수 없습니다: {config_path}\n"
            "claude-weather/config/weather_config.yaml 파일이 있는지 확인하세요."
        )

    try:
        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"설정 파일의 형식이 잘못됐습니다: {config_path}\n"
            f"들여쓰기에 탭(Tab)을 쓰지 않았는지, 콜론(:) 뒤에 공백이 있는지 확인하세요.\n"
            f"원본 오류: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"설정 파일이 비어 있거나 형식이 잘못됐습니다: {config_path}")

    for key in ("settings", "locations", "routes"):
        if key not in raw:
            raise ConfigError(f"설정 파일에 '{key}:' 항목이 없습니다: {config_path}")

    locations: dict[str, Location] = {}
    for loc_id, body in (raw["locations"] or {}).items():
        if body is None:
            continue
        missing = [k for k in ("name", "latitude", "longitude") if k not in body]
        if missing:
            raise ConfigError(
                f"위치 '{loc_id}' 에 다음 항목이 빠졌습니다: {', '.join(missing)}"
            )
        locations[loc_id] = Location(
            id=loc_id,
            name=str(body["name"]),
            latitude=float(body["latitude"]),
            longitude=float(body["longitude"]),
            type=str(body.get("type", "unknown")),
            country=str(body.get("country", "")),
            kma_zones=tuple(str(z) for z in (body.get("kma_zones") or [])),
        )

    routes: dict[str, Route] = {}
    for route_id, body in (raw["routes"] or {}).items():
        if body is None:
            continue
        endpoints = list(body.get("endpoints") or [])
        waypoints = list(body.get("waypoints") or [])
        destinations = list(body.get("destinations") or [])
        main = _merge_route_points(endpoints, waypoints)
        # 목록과 표에는 본선 다음에 도착지들을 이어 붙여 보여 준다.
        ordered = main + [d for d in destinations if d not in main]
        unknown = [lid for lid in ordered if lid not in locations]
        if unknown:
            raise ConfigError(
                f"항로 '{route_id}' 가 존재하지 않는 위치 ID를 가리킵니다: {', '.join(unknown)}\n"
                "locations: 목록에 해당 ID가 있는지 확인하세요."
            )
        routes[route_id] = Route(
            id=route_id,
            name=str(body.get("name", route_id)),
            location_ids=tuple(ordered),
            short=str(body.get("short", "") or ""),
            main_ids=tuple(main),
            destination_ids=tuple(d for d in destinations if d in locations),
        )

    return Config(raw=raw, locations=locations, routes=routes)


def _merge_route_points(endpoints: list[str], waypoints: list[str]) -> list[str]:
    """출발·경유·도착 지점과 중간 웨이포인트를 항해 순서로 합친다.

    웨이포인트가 아직 없으면(현재 MVP 상태) endpoints 를 그대로 쓴다.
    웨이포인트가 생기면 첫 구간(출발 -> 첫 경유지) 사이에 끼워 넣는다.
    구간별로 더 정교하게 나눠야 하면 이 함수만 고치면 된다.
    """
    if not waypoints:
        return list(endpoints)
    if not endpoints:
        return list(waypoints)
    head, *rest = endpoints
    return [head, *waypoints, *rest]


def load_api_key() -> str | None:
    """기상청 API 키를 환경변수 또는 .env 파일에서 읽는다.

    찾는 순서:
      1) 이미 설정된 환경변수 KMA_HUB_API_KEY
      2) claude-weather/.env
      3) 상위 폴더(F:/AI/Weather_gathering)/.env
    키가 없으면 None 을 돌려준다(특보 수집만 건너뛰고 나머지는 동작).
    """
    key = os.environ.get("KMA_HUB_API_KEY")
    if key and key.strip():
        return key.strip()

    for env_path in (PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            # 원본 .env 는 'KMA_HUB_API_KEY =키' 처럼 공백이 섞여 있을 수 있어 양쪽을 정리한다.
            if name.strip() == "KMA_HUB_API_KEY":
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    return None
