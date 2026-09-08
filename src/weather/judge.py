"""색상 판정과 운항 판단(가능/조건/불가)을 계산하는 모듈.

여기 있는 규칙은 전부 config/weather_config.yaml 의 값을 읽어서 동작한다.
숫자를 바꾸고 싶으면 이 파일이 아니라 설정 파일을 고치면 된다.

★ 이 판정은 화면 경보용이다. 자동 출항 승인 기준이 아니다.
  실제 피항/출항 판단은 담당자가 한다. (PRD 1절)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from .config import Config
from .db import COLUMN_LABELS, VALUE_COLUMNS

# 상태 값 4가지
NORMAL = "normal"            # 초록: 정상 범위
CAUTION = "caution"          # 노랑: 주의
UNAVAILABLE = "unavailable"  # 빨강: 불가
NO_DATA = "no_data"          # 회색: 데이터 없음

# 심각도 비교용 순서(숫자가 클수록 나쁨). NO_DATA 는 따로 다룬다.
SEVERITY = {NORMAL: 0, CAUTION: 1, UNAVAILABLE: 2}

STATUS_COLORS = {
    NORMAL: "#1a7f37",       # 초록
    CAUTION: "#b58100",      # 노랑(글자 대비를 위해 진한 톤)
    UNAVAILABLE: "#c62828",  # 빨강
    NO_DATA: "#9e9e9e",      # 회색
}

STATUS_BACKGROUNDS = {
    NORMAL: "#e6f4ea",
    CAUTION: "#fff4d6",
    UNAVAILABLE: "#fde7e7",
    NO_DATA: "#f0f0f0",
}

STATUS_LABELS_KO = {
    NORMAL: "정상",
    CAUTION: "주의",
    UNAVAILABLE: "불가",
    NO_DATA: "데이터 없음",
}

JUDGEMENT_LABELS_KO = {
    NORMAL: "가능",
    CAUTION: "조건",
    UNAVAILABLE: "불가",
    NO_DATA: "데이터 없음",
}

# 운항 판단에 반드시 필요한 항목. 이 중 하나라도 값이 없으면 '데이터 없음'.
REQUIRED_FOR_JUDGEMENT = ("wind_speed_ms", "wave_height_m")

# 16방위 이름 (한글).
# N/S/E/W 대신 북/남/동/서 를 쓴다. 해사·기상청에서 쓰는 표기다.
COMPASS_16 = [
    "북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
    "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서",
]

# WMO 기상코드 -> 한글 설명. 지금 자료원은 이 값을 주지 않아 쓰이지 않는다.
WMO_CODES: dict[int, str] = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "착빙 안개",
    51: "이슬비 약", 53: "이슬비 보통", 55: "이슬비 강",
    56: "어는 이슬비 약", 57: "어는 이슬비 강",
    61: "비 약", 63: "비 보통", 65: "비 강",
    66: "어는 비 약", 67: "어는 비 강",
    71: "눈 약", 73: "눈 보통", 75: "눈 강", 77: "싸락눈",
    80: "소나기 약", 81: "소나기 보통", 82: "소나기 강",
    85: "소낙눈 약", 86: "소낙눈 강",
    95: "뇌우", 96: "뇌우(약한 우박)", 99: "뇌우(강한 우박)",
}

# 뇌우로 간주하는 기상코드
THUNDERSTORM_CODES = {95, 96, 99}


def compass_name(degrees: float | None) -> str:
    """각도를 16방위 이름으로 바꾼다. 예) 45 -> 북동"""
    if degrees is None:
        return ""
    index = int((float(degrees) % 360) / 22.5 + 0.5) % 16
    return COMPASS_16[index]


def describe_weather_code(code: Any) -> str:
    """WMO 기상코드를 한글 설명으로 바꾼다."""
    if code is None:
        return ""
    try:
        return WMO_CODES.get(int(code), "코드 {}".format(int(code)))
    except (TypeError, ValueError):
        return ""


@dataclass
class MetricResult:
    """항목 하나에 대한 판정 결과."""
    column: str
    label: str
    value: Any
    unit: str
    status: str
    reason: str          # 툴팁에 넣을 한 줄 설명


@dataclass
class WarningHit:
    """어떤 시각에 적용되는 기상특보 하나."""
    reg_id: str
    reg_ko: str
    wrn: str
    lvl: str
    cmd: str
    tm_fc: str
    tm_ef: str
    ed_tm: str
    effect: str          # normal / condition(=caution) / unavailable


@dataclass
class CellJudgement:
    """한 칸(위치 x 시각)의 종합 판정."""
    status: str
    metrics: list[MetricResult] = field(default_factory=list)
    warnings: list[WarningHit] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 항목별 색상 판정
# ---------------------------------------------------------------------------

def evaluate_metric(column: str, value: Any, thresholds: dict[str, Any]) -> MetricResult:
    """항목 하나를 한계값과 비교해 상태를 정한다."""
    label = COLUMN_LABELS.get(column, column)
    unit = VALUE_COLUMNS.get(column, "")
    rule = thresholds.get(column) or {}
    shown = format_value(column, value)

    if value is None:
        return MetricResult(column, label, None, unit, NO_DATA,
                            "{}: 데이터 없음".format(label))

    # 해류처럼 자동 판정을 하지 않는 항목
    if rule.get("automatic_judgement") is False:
        return MetricResult(column, label, value, unit, NORMAL,
                            "{}: {} {} / 자동 판정 대상 아님".format(label, shown, unit))

    # 방향·코드처럼 한계값 자체가 없는 항목은 항상 정상으로 둔다.
    if not rule:
        return MetricResult(column, label, value, unit, NORMAL,
                            "{}: {} {} / 기준 없음".format(label, shown, unit))

    number = float(value)

    # 값이 클수록 나쁜 항목 (풍속, 돌풍, 파고, 강수, CAPE)
    unavailable_at = rule.get("unavailable_at")
    caution_at = rule.get("caution_at")
    if unavailable_at is not None or caution_at is not None:
        if unavailable_at is not None and number >= float(unavailable_at):
            return MetricResult(
                column, label, value, unit, UNAVAILABLE,
                "{}: {} {} / 불가 기준 {} {} (초과)".format(
                    label, shown, unit, unavailable_at, unit),
            )
        if caution_at is not None and number >= float(caution_at):
            limit_text = ("불가 기준 {} {}".format(unavailable_at, unit)
                          if unavailable_at is not None else "불가 기준 없음")
            return MetricResult(
                column, label, value, unit, CAUTION,
                "{}: {} {} / 주의 기준 {} {} (충족), {}".format(
                    label, shown, unit, caution_at, unit, limit_text),
            )
        limit_text = ("불가 기준 {} {}".format(unavailable_at, unit)
                      if unavailable_at is not None else "불가 기준 없음")
        return MetricResult(
            column, label, value, unit, NORMAL,
            "{}: {} {} / {} (정상)".format(label, shown, unit, limit_text),
        )

    # 값이 작을수록 나쁜 항목 (시정)
    unavailable_below = rule.get("unavailable_below")
    caution_below = rule.get("caution_below")
    if unavailable_below is not None and number < float(unavailable_below):
        return MetricResult(
            column, label, value, unit, UNAVAILABLE,
            "{}: {} {} / 불가 기준 {} {} 미만 (해당)".format(
                label, shown, unit, unavailable_below, unit),
        )
    if caution_below is not None and number < float(caution_below):
        return MetricResult(
            column, label, value, unit, CAUTION,
            "{}: {} {} / 주의 기준 {} {} 미만 (해당)".format(
                label, shown, unit, caution_below, unit),
        )
    return MetricResult(
        column, label, value, unit, NORMAL,
        "{}: {} {} / 불가 기준 {} {} 미만 (정상)".format(
            label, shown, unit, unavailable_below, unit),
    )


# ---------------------------------------------------------------------------
# 기상특보 적용
# ---------------------------------------------------------------------------

def _warning_effect(rules: dict[str, Any], wrn: str, lvl: str) -> str | None:
    """특보 하나가 운항 판단에 어떤 영향을 주는지 정한다.

    돌려주는 값: 'condition' / 'unavailable' / 'normal' / None(무시)
    """
    ignore = set(rules.get("ignore_types") or [])
    if wrn in ignore:
        return None

    override = (rules.get("by_type_override") or {}).get(wrn)
    if override:
        return str(override)

    by_level = rules.get("by_level") or {}
    effect = by_level.get(lvl)
    return str(effect) if effect else None


def _effect_to_status(effect: str) -> str:
    """설정 파일의 표현(condition 등)을 내부 상태 값으로 바꾼다."""
    return {
        "normal": NORMAL,
        "condition": CAUTION,
        "caution": CAUTION,
        "unavailable": UNAVAILABLE,
    }.get(effect, NORMAL)


def _kma_time_to_iso(value: str | None) -> str | None:
    """YYYYMMDDHHMM -> 2026-09-06T17:58 (비교하기 쉬운 형태)"""
    if not value or len(value) < 12 or not value.isdigit():
        return None
    return "{}-{}-{}T{}:{}".format(
        value[0:4], value[4:6], value[6:8], value[8:10], value[10:12]
    )


def warnings_for_location(
    config: Config,
    warning_rows: Iterable[dict[str, Any]],
    location_id: str,
) -> list[dict[str, Any]]:
    """한 지점에 적용되는 특보 행만 걸러 낸다."""
    location = config.locations.get(location_id)
    if not location or not location.kma_zones:
        return []
    zones = set(location.kma_zones)
    return [row for row in warning_rows if row.get("reg_id") in zones]


# 해제 예고를 못 읽었을 때 특보를 얼마나 오래 유효하다고 볼지(시간).
# 기상특보는 보통 하루이틀 안에 풀린다. 열흘 뒤까지 걸어 두는 것은
# 보수적인 게 아니라 그냥 틀린 것이다.
WARNING_FALLBACK_HOURS = 48


def parse_end_time(ed_tm: str | None, tm_ef: str | None) -> str | None:
    """해제 예고 글에서 실제 해제 시각을 뽑는다.

    기상청이 주는 글은 형식이 일정하다.

        "07일 늦은 오후(15시~18시)"  ->  그달 7일 18시
        "08일 오전(09시~12시)"       ->  8일 12시
        "08일 밤(21시~24시)"         ->  9일 00시  (24시는 다음날 0시)

    괄호 안의 '끝 시각' 을 쓴다. 괄호가 없으면 시간대 이름으로 추정한다.
    일자만 있고 달은 없으므로, 발효 시각(TM_EF)을 기준으로 이번 달인지
    다음 달인지 정한다. (월말에 "01일 …" 이면 다음 달이다)

    읽지 못하면 None 을 돌려준다.
    """
    if not ed_tm:
        return None
    text = str(ed_tm).strip()
    if not text:
        return None

    day_match = re.search(r"(\d{1,2})\s*일", text)
    if not day_match:
        return None
    day = int(day_match.group(1))

    # 괄호 안 "~18시" 가 가장 정확하다
    hour_match = re.search(r"~\s*(\d{1,2})\s*시", text)
    if hour_match:
        hour = int(hour_match.group(1))
    else:
        # 괄호가 없으면 시간대 이름으로 그 구간의 끝을 잡는다
        hour = 24
        for word, end in (("새벽", 6), ("아침", 9), ("오전", 12), ("낮", 15),
                          ("늦은 오후", 18), ("오후", 18), ("저녁", 21), ("밤", 24)):
            if word in text:
                hour = end
                break

    base_iso = _kma_time_to_iso(tm_ef)
    try:
        base = datetime.strptime(base_iso, "%Y-%m-%dT%H:%M") if base_iso else datetime.now()
    except Exception:
        base = datetime.now()

    year, month = base.year, base.month
    # 발효일보다 한참 앞선 날짜면 다음 달로 넘어간 것이다.
    if day < base.day - 15:
        month += 1
        if month > 12:
            month = 1
            year += 1

    extra_days = 0
    if hour >= 24:          # 24시는 다음날 0시
        hour -= 24
        extra_days = 1

    try:
        end = datetime(year, month, day, hour) + timedelta(days=extra_days)
    except ValueError:
        return None
    return end.strftime("%Y-%m-%dT%H:%M")


def active_warnings_at(
    config: Config,
    location_warnings: list[dict[str, Any]],
    valid_time: str,
) -> list[WarningHit]:
    """특정 시각에 유효한 특보만 골라 WarningHit 목록으로 만든다.

    '유효'의 기준
      1) 발효시각(TM_EF) <= 그 시각
      2) 그 시각 < 해제 시각

    해제 시각은 ED_TM("07일 늦은 오후(15시~18시)")을 읽어서 구한다.
    parse_end_time 참고. 읽지 못하면 발효 후 WARNING_FALLBACK_HOURS 까지만
    유효로 본다.

    ★ 예전에는 해제 시각을 아예 무시하고 발효 이후 전부를 유효로 봤다.
      그래서 7일에 풀릴 강풍주의보 때문에 16일 예보까지 '조건부' 로
      나왔다. 보수적인 게 아니라 틀린 결과였다.
    """
    rules = config.warning_rules
    hits: list[WarningHit] = []
    for row in location_warnings:
        if (row.get("cmd") or "").strip() == "해제":
            continue
        effective = _kma_time_to_iso(row.get("tm_ef"))
        if effective and valid_time < effective:
            continue

        # 해제 시각을 지났으면 더 이상 유효하지 않다.
        end = parse_end_time(row.get("ed_tm"), row.get("tm_ef"))
        if end is None and effective:
            # 해제 예고를 못 읽었을 때의 안전장치
            try:
                end = (datetime.strptime(effective, "%Y-%m-%dT%H:%M")
                       + timedelta(hours=WARNING_FALLBACK_HOURS)
                       ).strftime("%Y-%m-%dT%H:%M")
            except Exception:
                end = None
        if end and valid_time >= end:
            continue
        effect = _warning_effect(rules, (row.get("wrn") or "").strip(),
                                 (row.get("lvl") or "").strip())
        if effect is None:
            effect = "normal"
        hits.append(WarningHit(
            reg_id=row.get("reg_id", ""),
            reg_ko=row.get("reg_ko", ""),
            wrn=(row.get("wrn") or "").strip(),
            lvl=(row.get("lvl") or "").strip(),
            cmd=(row.get("cmd") or "").strip(),
            tm_fc=row.get("tm_fc", ""),
            tm_ef=row.get("tm_ef", ""),
            ed_tm=(row.get("ed_tm") or "").strip(),
            effect=effect,
        ))
    return hits


# ---------------------------------------------------------------------------
# 한 칸 종합 판정
# ---------------------------------------------------------------------------

def adjusted_thresholds(config: Config, side: str | None = None) -> dict[str, Any]:
    """파도를 어느 쪽에서 맞느냐에 따라 파고 한계를 조정한 기준 묶음.

    옆에서 맞으면(횡파) 롤링이 심해 바지 화물이 위험하므로 더 엄격하게,
    뒤에서 맞으면(등파) 덜 위험하므로 느슨하게 본다.

        맞파 x1.00 -> 불가 1.80 m
        횡파 x0.80 -> 불가 1.44 m
        등파 x1.30 -> 불가 2.34 m

    side 를 주지 않으면 원래 기준을 그대로 돌려준다.
    """
    base = config.thresholds
    if not side:
        return base
    factors = ((config.raw.get("wave_direction") or {}).get("safety_factor") or {})
    factor = factors.get(side)
    if not factor or abs(factor - 1.0) < 1e-9:
        return base

    out = dict(base)
    wave = dict(base.get("wave_height_m") or {})
    for key in ("caution_at", "unavailable_at"):
        if wave.get(key) is not None:
            wave[key] = round(float(wave[key]) * float(factor), 3)
    out["wave_height_m"] = wave
    return out


def berthing_status(config: Config, values: dict[str, Any],
                    loc_type: str | None) -> str | None:
    """선하역(짐 싣고 내리기)을 할 수 있는 상태인지.

    고현항에서는 하역, 도착지에서는 선적을 하는데 둘 다 크레인 작업이라
    기준이 같다. 부두(terminal) 네 곳에서만 뜻이 있다.
    묘박지와 항로점에는 크레인이 없으므로 None 이 나온다.

    ★ 이 값은 화면 색(운항 판단)과 섞지 않는다.
      색은 "거기까지 갈 수 있나" 하나만 뜻하고, 이건 "가서 짐을 싣고
      내릴 수 있나" 라서 다른 이야기다. 화면에는 작은 표시로 따로 붙인다.

    어떤 값을 볼지는 설정이 정한다. 지금은 순간풍속(돌풍) 하나만 본다.
    항목을 늘리려면 berthing_thresholds 에 줄을 더하면 되고,
    여러 개면 그중 가장 나쁜 것을 따른다.

    돌려주는 값: 'n'(가능) / 'c'(주의) / 'u'(곤란) / None(해당 없음)
    """
    rule = config.raw.get("berthing_thresholds") or {}
    if not rule or loc_type not in (rule.get("applies_to") or []):
        return None

    worst = NORMAL
    for column, limits in rule.items():
        if column in ("applies_to", "basis") or not isinstance(limits, dict):
            continue
        value = values.get(column)
        if value is None:
            continue
        if limits.get("unavailable_at") is not None and value >= limits["unavailable_at"]:
            status = UNAVAILABLE
        elif limits.get("caution_at") is not None and value >= limits["caution_at"]:
            status = CAUTION
        else:
            status = NORMAL
        if SEVERITY[status] > SEVERITY[worst]:
            worst = status
    return {NORMAL: "n", CAUTION: "c", UNAVAILABLE: "u"}[worst]


def judge_cell(
    config: Config,
    values: dict[str, Any],
    warning_hits: list[WarningHit] | None = None,
    wave_side: str | None = None,
) -> CellJudgement:
    """한 칸의 값들과 특보를 합쳐 가능/조건/불가 를 정한다.

    규칙 (PRD 4절):
      가능      : 모든 적용 지표가 정상 범위
      조건      : 하나 이상이 주의 범위이나 불가 기준은 미초과
      불가      : 하나 이상이 불가 기준 초과 또는 적용 특보 규칙에 해당
      데이터 없음: 판단에 필요한 필수 지표가 없음
    """
    # 파도를 어느 쪽에서 맞느냐에 따라 파고 한계가 달라진다.
    thresholds = adjusted_thresholds(config, wave_side)
    hits = warning_hits or []

    metrics: list[MetricResult] = []
    for column in VALUE_COLUMNS:
        # 방향 항목은 색상 판정 대상이 아니다(툴팁에는 값이 나온다).
        if column.endswith("_direction_deg"):
            continue
        metrics.append(evaluate_metric(column, values.get(column), thresholds))

    missing = [c for c in REQUIRED_FOR_JUDGEMENT if values.get(c) is None]
    if missing:
        return CellJudgement(NO_DATA, metrics, hits, missing)

    worst = NORMAL
    for metric in metrics:
        if metric.status == NO_DATA:
            continue
        if SEVERITY[metric.status] > SEVERITY[worst]:
            worst = metric.status

    # 뇌우 기상코드는 별도로 '조건' 이상으로 올린다.
    code = values.get("weather_code")
    try:
        if code is not None and int(code) in THUNDERSTORM_CODES:
            if SEVERITY[CAUTION] > SEVERITY[worst]:
                worst = CAUTION
    except (TypeError, ValueError):
        pass

    for hit in hits:
        status = _effect_to_status(hit.effect)
        if SEVERITY[status] > SEVERITY[worst]:
            worst = status

    return CellJudgement(worst, metrics, hits, [])


def build_tooltip(
    judgement: CellJudgement,
    location_name: str,
    valid_time: str,
    collected_at: str | None,
    data_kind: str = "예보",
    tab_status: str | None = None,
    tab_label: str | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    """PRD 4절이 요구하는 툴팁 문구를 만든다.

    tab_status 를 주면 '이 셀 색이 왜 그런지'를 먼저 알려 준다.
    수치 탭의 셀 색은 그 탭에 나오는 항목만 보고 정하므로,
    항로 전체의 종합 운항 판단(judgement.status)과 다를 수 있다.
    예) 풍속 탭 셀은 초록인데 순간풍속 때문에 종합 판단은 불가인 경우.
    """
    lines: list[str] = []
    lines.append("{} / {}".format(location_name, valid_time.replace("T", " ")))

    if tab_status is not None:
        lines.append("이 칸 색: {} ({} 기준)".format(
            STATUS_LABELS_KO.get(tab_status, "?"), tab_label or "이 탭 항목"))
        lines.append("종합 운항 판단: {}".format(
            JUDGEMENT_LABELS_KO.get(judgement.status, "?")))
    else:
        lines.append("판정: {}".format(JUDGEMENT_LABELS_KO.get(judgement.status, "?")))

    if judgement.missing_required:
        labels = ", ".join(COLUMN_LABELS.get(c, c) for c in judgement.missing_required)
        lines.append("필수 지표 없음: {}".format(labels))

    for metric in judgement.metrics:
        # 자료원이 바뀌어 값이 항상 없는 항목은 툴팁에서 뺀다.
        if metric.status == NO_DATA and metric.column in (
                "weather_code", "cape_jkg", "current_speed_kn",
                "current_direction_deg", "sea_surface_temp_c"):
            continue
        lines.append(metric.reason)

    if judgement.warnings:
        lines.append("---- 기상특보 ----")
        for hit in judgement.warnings:
            text = "{} {} ({})".format(hit.wrn, hit.lvl, hit.cmd)
            if hit.tm_ef and len(hit.tm_ef) >= 12:
                text += " 발효 {}-{}-{} {}:{}".format(
                    hit.tm_ef[0:4], hit.tm_ef[4:6], hit.tm_ef[6:8],
                    hit.tm_ef[8:10], hit.tm_ef[10:12])
            if hit.ed_tm:
                text += " / 해제예고 {}".format(hit.ed_tm)
            text += " [{}]".format(hit.reg_ko)
            lines.append(text)

    if extra_lines:
        lines.extend(extra_lines)

    lines.append("데이터 기준: {}".format(data_kind))
    if collected_at:
        lines.append("예보 수집 시각: {} KST".format(collected_at.replace("T", " ")))
    lines.append("출처/모델: ECMWF IFS+WAM, NOAA GFS, MET Norway, KMA 특보현황")
    return "\n".join(lines)


def format_value(column: str, value: Any) -> str:
    """화면 셀에 넣을 짧은 글자를 만든다."""
    if value is None:
        return "-"
    if column == "weather_code":
        return describe_weather_code(value)
    if column.endswith("_direction_deg"):
        return "{}({:.0f}°)".format(compass_name(value), float(value))
    if column in ("wave_height_m", "current_speed_kn"):
        return "{:.1f}".format(float(value))
    if column in ("visibility_km",):
        return "{:.1f}".format(float(value))
    if column in ("wind_speed_ms", "wind_gust_ms", "wave_period_s",
                  "sea_surface_temp_c"):
        return "{:.1f}".format(float(value))
    if column == "precipitation_mm":
        return "{:.1f}".format(float(value))
    if column == "cape_jkg":
        return "{:.0f}".format(float(value))
    return str(value)
