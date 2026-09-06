"""기상청 API 허브에서 특보 현황을 받아 오는 모듈.

★ 중요: 이 API 응답은 JSON 이 아니다.
   - 형식: 사람이 읽는 표 모양 텍스트. 각 줄이 콤마(,)로 나뉘고 줄 끝에 '=' 가 붙는다.
   - 인코딩: EUC-KR (UTF-8 아님). UTF-8 로 읽으면 한글이 깨진다.
   - '#' 으로 시작하는 줄은 설명(주석)이라 데이터가 아니다.
   PRD 5.3 에 "HTTP JSON 응답을 직접 파싱한다" 고 적혀 있으나 실제 응답은 위와 같다.

한 줄의 열 순서 (특보현황 조회):
   REG_UP, REG_UP_KO, REG_ID, REG_KO, TM_FC, TM_EF, WRN, LVL, CMD, ED_TM, =

   REG_UP    상위 특보구역코드          예: S1311000
   REG_UP_KO 상위 특보구역명            예: 남해동부앞바다
   REG_ID    특보구역코드               예: S1311400
   REG_KO    특보구역명                 예: 거제시동부앞바다
   TM_FC     발표시각 (YYYYMMDDHHMM)
   TM_EF     발효시각 (YYYYMMDDHHMM)
   WRN       특보종류                   예: 풍랑, 강풍, 호우, 태풍
   LVL       특보수준                   예: 예비, 주의, 경보
   CMD       특보명령                   예: 발표, 연장, 변경, 해제
   ED_TM     해제예고 시점              예: 08일 오전(09시~12시)
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

WARNING_NOW_URL = "https://apihub.kma.go.kr/api/typ01/url/wrn_now_data_new.php"

# 응답 본문의 열 순서 그대로.
FIELD_ORDER = [
    "reg_up", "reg_up_ko", "reg_id", "reg_ko",
    "tm_fc", "tm_ef", "wrn", "lvl", "cmd", "ed_tm",
]

# 기상청 응답은 EUC-KR 이지만, 혹시 서버가 바뀌어도 동작하도록 순서대로 시도한다.
ENCODING_CANDIDATES = ("euc-kr", "cp949", "utf-8")


class KmaError(Exception):
    """기상청 API 호출 또는 해석이 실패했을 때 내는 오류."""


def fetch_warning_text(
    api_key: str,
    timeout: int = 30,
    retries: int = 3,
    wait: int = 5,
) -> str:
    """특보 현황 원문 텍스트를 받아 온다(한글이 제대로 보이도록 디코딩까지 마친 상태)."""
    if not api_key:
        raise KmaError(
            "기상청 API 키가 없습니다. .env 파일에 KMA_HUB_API_KEY 를 넣어 주세요."
        )

    params = {"fe": "f", "tm": "", "disp": "0", "help": "1", "authKey": api_key}
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(WARNING_NOW_URL, params=params, timeout=timeout)
            response.raise_for_status()
            raw = response.content
            for encoding in ENCODING_CANDIDATES:
                try:
                    text = raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
                if "START7777" not in text:
                    raise KmaError(
                        "기상청 응답이 예상과 다릅니다. API 키가 맞는지 확인하세요.\n"
                        "받은 내용 앞부분: {}".format(text[:200])
                    )
                return text
            raise KmaError("기상청 응답의 인코딩을 알 수 없습니다.")
        except KmaError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(wait)

    raise KmaError(
        "기상청 특보 API 호출에 {}번 실패했습니다. 마지막 오류: {}".format(retries, last_error)
    )


def parse_warning_text(text: str) -> tuple[str | None, list[dict[str, Any]]]:
    """특보 현황 원문을 (기준시각, 행 목록) 으로 해석한다.

    기준시각은 응답 첫 줄의 '#기준시각:202609060147' 에서 뽑는다.
    이 줄이 없는 옛 버전 API 응답도 있으므로, 없으면 None 을 돌려준다.
    """
    base_time: str | None = None
    rows: list[dict[str, Any]] = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            if "기준시각" in stripped:
                digits = "".join(ch for ch in stripped if ch.isdigit())
                if len(digits) >= 12:
                    base_time = digits[:12]
            continue

        if stripped.startswith("7777END"):
            break

        # 줄 끝의 '=' 와 그 앞 콤마를 떼어 낸다.
        body = stripped.rstrip("=").rstrip().rstrip(",")
        parts = [part.strip() for part in body.split(",")]
        if len(parts) < len(FIELD_ORDER):
            # ED_TM 이 비어 있으면 열이 하나 모자랄 수 있으므로 빈 값으로 채운다.
            parts += [""] * (len(FIELD_ORDER) - len(parts))

        row = {name: parts[index] for index, name in enumerate(FIELD_ORDER)}
        if not row["reg_id"] or not row["wrn"]:
            continue
        rows.append(row)

    return base_time, rows


def build_warning_rows(
    api_key: str,
    collected_at: datetime,
    timeout: int = 30,
    retries: int = 3,
    wait: int = 5,
) -> tuple[list[dict[str, Any]], str | None]:
    """특보 현황을 받아 DB 에 넣을 행 목록으로 만든다."""
    text = fetch_warning_text(api_key, timeout, retries, wait)
    base_time, parsed = parse_warning_text(text)
    collected_key = collected_at.strftime("%Y-%m-%dT%H:%M")

    rows: list[dict[str, Any]] = []
    for item in parsed:
        row = {"collected_at": collected_key, "base_time": base_time}
        row.update(item)
        rows.append(row)
    return rows, base_time


def format_kma_time(value: str | None) -> str:
    """YYYYMMDDHHMM 형태를 2026-09-06 01:47 처럼 보기 좋게 바꾼다."""
    if not value or len(value) < 12 or not value.isdigit():
        return value or ""
    return "{}-{}-{} {}:{}".format(
        value[0:4], value[4:6], value[6:8], value[8:10], value[10:12]
    )
