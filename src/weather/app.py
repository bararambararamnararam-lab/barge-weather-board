"""화면(웹 브라우저에 뜨는 데이터 시트).

실행 방법은 두 가지 중 하나다.
  - 폴더의 `2_화면열기.bat` 을 더블클릭
  - 또는 명령창에서:  .venv\\Scripts\\streamlit run src/weather/app.py

화면 구조 (PRD 3절):
  왼쪽 사이드바 : 항로 선택, 시간 간격(1시간/3시간), 보기 기준일, 예보 이력 시점
  위쪽 탭       : 풍속·풍향 / 순간풍속 / 유의파고 / 파향·파주기 / 시정 /
                  강수 / 기상특보 / 운항 판단 / 과거 실황 / 예보 이력
  표            : 행 = 위치, 열 = 시각
  셀            : 숫자 + 색상(초록/노랑/빨강/회색), 마우스를 올리면 툴팁
"""

from __future__ import annotations

import html
import sys
from datetime import datetime, timedelta
from pathlib import Path

# `streamlit run src/weather/app.py` 로 실행해도 weather 패키지를 찾을 수 있게 한다.
_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import streamlit as st  # noqa: E402

from weather import judge, map_view  # noqa: E402
from weather.archive import archive_row_to_cell  # noqa: E402
from weather.config import DB_PATH, ConfigError, load_config  # noqa: E402
from weather.db import (  # noqa: E402
    ARCHIVE_VALUE_COLUMNS,
    COLUMN_LABELS,
    VALUE_COLUMNS,
    database_size_mb,
    fetch_archive,
    fetch_forecast,
    fetch_latest_warnings,
    fetch_observations,
    fetch_run_log,
    latest_collected_at,
    list_archive_dates,
    list_collected_at,
    session,
    storage_summary,
)
from weather.sources.kma import format_kma_time  # noqa: E402

st.set_page_config(page_title="한·중 바지선 운항 기상", layout="wide",
                   initial_sidebar_state="expanded")

# ---------------------------------------------------------------------------
# 탭 정의: (탭 이름, 이 탭에서 보여 줄 열 목록)
# ---------------------------------------------------------------------------
NUMERIC_TABS: list[tuple[str, list[str]]] = [
    ("풍속·풍향", ["wind_speed_ms", "wind_direction_deg"]),
    ("순간풍속", ["wind_gust_ms"]),
    ("유의파고", ["wave_height_m"]),
    ("파향·파주기", ["wave_direction_deg", "wave_period_s"]),
    ("시정", ["visibility_km"]),
    ("강수", ["precipitation_mm"]),
]
# 기상코드·뇌우지수(CAPE)·해류·수온은 뺐다.
# 자료원이 ECMWF+NOAA 로 바뀌면서 더 이상 받지 않아 값이 항상 비기 때문이다.

TAB_NAMES = [name for name, _ in NUMERIC_TABS] + [
    "기상특보", "운항 판단", "과거 실황", "예보 이력",
]

# 표는 항상 '밝은 데이터 시트' 모양으로 고정한다.
# 색깔(초록/노랑/빨강/회색)이 판단의 핵심이라, 브라우저가 다크 모드라고
# 색이 뒤집히면 안 된다. 그래서 글자색·배경색을 전부 직접 지정한다.
# (지정하지 않으면 다크 모드에서 흰 배경에 흰 글자가 되어 안 보인다.)
TABLE_CSS = """
<style>
.wx-wrap {
  overflow-x: auto; border: 1px solid #d0d0d0; border-radius: 6px;
  background: #ffffff; padding: 0; margin-bottom: 4px;
}
table.wx {
  border-collapse: collapse; font-size: 12px; white-space: nowrap;
  background: #ffffff; color: #202020;
}
table.wx th, table.wx td {
  border: 1px solid #dcdcdc; padding: 3px 6px; text-align: center;
  color: #202020;
}
table.wx thead th {
  position: sticky; top: 0; background: #eceff1 !important; z-index: 2;
  color: #263238 !important; font-weight: 700; font-size: 11px; line-height: 1.25;
}
table.wx td.loc, table.wx th.loc {
  position: sticky; left: 0; background: #f4f6f7 !important; z-index: 3;
  color: #202020 !important;
  text-align: left; font-weight: 700; min-width: 190px; max-width: 190px;
  white-space: normal;
}
table.wx thead th.loc { z-index: 4; background: #eceff1 !important; }
table.wx td { cursor: help; }
.wx-legend { font-size: 12px; margin: 6px 0 10px 0; }
.wx-legend span {
  display: inline-block; padding: 3px 12px; margin: 0 6px 4px 0;
  border-radius: 3px; border: 1px solid #c8c8c8; color: #202020;
}
</style>
"""

LEGEND_HTML = (
    '<div class="wx-legend">'
    '<span style="background:#e6f4ea;color:#1a7f37">초록 · 정상</span>'
    '<span style="background:#fff4d6;color:#8a6100">노랑 · 주의</span>'
    '<span style="background:#fde7e7;color:#c62828">빨강 · 불가</span>'
    '<span style="background:#f0f0f0;color:#5f5f5f">회색 · 데이터 없음</span>'
    '</div>'
)


# ---------------------------------------------------------------------------
# 데이터 읽기 (streamlit 캐시로 같은 조건은 다시 읽지 않는다)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def load_forecast_data(location_ids: tuple[str, ...], collected_at: str | None,
                       valid_from: str, valid_to: str, _stamp: str):
    with session() as conn:
        rows = fetch_forecast(conn, location_ids, collected_at, valid_from, valid_to)
        return [dict(r) for r in rows]


@st.cache_data(ttl=60, show_spinner=False)
def load_warning_data(_stamp: str):
    with session() as conn:
        collected, rows = fetch_latest_warnings(conn)
        return collected, [dict(r) for r in rows]


@st.cache_data(ttl=60, show_spinner=False)
def load_observation_data(location_ids: tuple[str, ...], start: str, end: str,
                          _stamp: str):
    with session() as conn:
        rows = fetch_observations(conn, location_ids, start, end)
        return [dict(r) for r in rows]


@st.cache_data(ttl=60, show_spinner=False)
def load_archive_data(location_ids: tuple[str, ...], archive_date: str, _stamp: str):
    with session() as conn:
        rows = fetch_archive(conn, location_ids, archive_date)
        return [archive_row_to_cell(dict(r)) for r in rows]


@st.cache_data(ttl=30, show_spinner=False)
def load_meta():
    with session() as conn:
        return {
            "latest": latest_collected_at(conn),
            "snapshots": list_collected_at(conn, 300),
            "archives": list_archive_dates(conn, 400),
            "runs": [dict(r) for r in fetch_run_log(conn, 15)],
            "counts": storage_summary(conn),
        }


# ---------------------------------------------------------------------------
# 표 만들기
# ---------------------------------------------------------------------------

def time_columns(rows: list[dict], step_hours: int) -> list[str]:
    """표의 열(시각) 목록을 만든다. step_hours 가 3 이면 3시간 간격."""
    times = sorted({r["valid_time"] for r in rows})
    if step_hours <= 1:
        return times
    picked = []
    for value in times:
        hour = int(value[11:13])
        if hour % step_hours == 0:
            picked.append(value)
    return picked


def header_labels(times: list[str]) -> list[str]:
    """열 머리글. 날짜가 바뀌는 칸에만 날짜를 같이 보여 준다."""
    labels = []
    previous_day = None
    for value in times:
        day = value[5:10].replace("-", "/")
        hour = value[11:16]
        if day != previous_day:
            labels.append("{}<br>{}".format(day, hour))
            previous_day = day
        else:
            labels.append("<br>{}".format(hour))
    return labels


def render_table(
    config,
    location_ids: list[str],
    rows: list[dict],
    times: list[str],
    columns: list[str],
    warning_rows: list[dict],
    collected_label: str | None,
    data_kind: str,
    judgement_mode: bool = False,
    stored_judgement: bool = False,
) -> str:
    """HTML 표 한 덩어리를 문자열로 만든다.

    judgement_mode 가 True 면 셀에 숫자 대신 가능/조건/불가 를 쓴다.

    stored_judgement 가 True 면(아카이브 자료) 색은 지금 한계값으로 다시 계산하고,
    저장될 당시 판정은 툴팁에 따로 적어 준다. 한계값을 나중에 고쳐도
    과거를 새 기준으로 볼 수 있고, 동시에 그때 뭐라고 떴는지도 남는다.
    """
    indexed: dict[tuple[str, str], dict] = {
        (r["location_id"], r["valid_time"]): r for r in rows
    }

    parts = ['<div class="wx-wrap"><table class="wx"><thead><tr>']
    parts.append('<th class="loc">위치</th>')
    for label in header_labels(times):
        parts.append("<th>{}</th>".format(label))
    parts.append("</tr></thead><tbody>")

    for order, location_id in enumerate(location_ids, start=1):
        location = config.locations[location_id]
        location_warnings = judge.warnings_for_location(config, warning_rows, location_id)
        parts.append("<tr>")
        # 앞의 번호는 지도의 동그라미 번호와 같다.
        parts.append('<td class="loc">{}. {}</td>'.format(
            order, html.escape(location.name)))

        for valid_time in times:
            record = indexed.get((location_id, valid_time))
            if record is None:
                parts.append(
                    '<td style="background:{};color:{}" title="{}">-</td>'.format(
                        judge.STATUS_BACKGROUNDS[judge.NO_DATA],
                        judge.STATUS_COLORS[judge.NO_DATA],
                        html.escape("{} / {}\n데이터 없음".format(
                            location.name, valid_time.replace("T", " "))),
                    )
                )
                continue

            hits = judge.active_warnings_at(config, location_warnings, valid_time)
            verdict = judge.judge_cell(config, record, hits)

            # 아카이브 행에는 저장될 당시의 판정과 특보 요약이 들어 있다.
            extra_lines: list[str] = []
            if stored_judgement:
                saved = record.get("judgement")
                if saved:
                    extra_lines.append("저장 당시 판정: {}".format(
                        judge.JUDGEMENT_LABELS_KO.get(saved, saved)))
                summary = record.get("warning_summary")
                extra_lines.append(
                    "저장 당시 특보: {}".format(summary if summary else "없음"))

            if judgement_mode:
                status = verdict.status
                text = judge.JUDGEMENT_LABELS_KO[status]
                tooltip = judge.build_tooltip(
                    verdict, location.name, valid_time,
                    record.get("collected_at") or collected_label, data_kind,
                    extra_lines=extra_lines,
                )
            else:
                # 이 탭에서 보여 줄 열들만 골라 상태를 따로 계산한다.
                shown_status = judge.NORMAL
                pieces = []
                any_value = False
                for column in columns:
                    value = record.get(column)
                    if value is not None:
                        any_value = True
                    pieces.append(judge.format_value(column, value))
                    metric = judge.evaluate_metric(column, value, config.thresholds)
                    if metric.status == judge.NO_DATA:
                        continue
                    if judge.SEVERITY[metric.status] > judge.SEVERITY[shown_status]:
                        shown_status = metric.status
                status = shown_status if any_value else judge.NO_DATA
                text = " / ".join(p for p in pieces if p) if any_value else "-"
                tooltip = judge.build_tooltip(
                    verdict, location.name, valid_time,
                    record.get("collected_at") or collected_label, data_kind,
                    tab_status=status,
                    tab_label="·".join(COLUMN_LABELS.get(c, c) for c in columns),
                    extra_lines=extra_lines,
                )

            parts.append(
                '<td style="background:{};color:{}" title="{}">{}</td>'.format(
                    judge.STATUS_BACKGROUNDS[status],
                    judge.STATUS_COLORS[status],
                    html.escape(tooltip),
                    html.escape(text),
                )
            )
        parts.append("</tr>")

    parts.append("</tbody></table></div>")
    return "".join(parts)


def render_route_map(
    config,
    route_id: str,
    location_ids: list[str],
    forecast_rows: list[dict],
    times: list[str],
    warning_rows: list[dict],
    collected_label: str | None,
) -> None:
    """표 위에 항로 지도를 그린다.

    탭이 여러 개지만 지금 어느 탭을 보고 있는지는 프로그램이 알 수 없다.
    (Streamlit 은 탭을 한 번에 다 그린다.)
    그래서 지도에는 따로 항목 고르는 칸을 둔다.
    """
    with st.expander("지도로 보기 (OpenStreetMap)", expanded=True):
        if not times:
            st.info("표시할 예보가 없습니다.")
            return

        left, right = st.columns([3, 2])
        with left:
            picked_time = st.select_slider(
                "지도에 표시할 시각",
                options=times,
                value=times[0],
                format_func=lambda t: "{} {}".format(
                    t[5:10].replace("-", "/"), t[11:16]),
            )
        with right:
            metric = st.segmented_control(
                "표시 항목",
                options=[key for key, _ in map_view.MAP_METRICS],
                default=map_view.MAP_METRICS[0][0],
                format_func=lambda k: dict(map_view.MAP_METRICS)[k],
            )
        if metric is None:
            metric = map_view.MAP_METRICS[0][0]

        rows_by_key = {
            (r["location_id"], r["valid_time"]): r for r in forecast_rows
        }
        points = map_view.build_map_points(
            config, location_ids, rows_by_key, warning_rows,
            picked_time, metric, collected_label, "예보",
        )
        html = map_view.render_map_html(
            points, config.routes[route_id].name, picked_time,
            dict(map_view.MAP_METRICS)[metric],
            main_count=len(config.routes[route_id].main_ids),
        )
        st.iframe(html, height=460)
        st.caption(
            "선은 항해 순서, 짧은 막대는 바람이 불어 가는 방향입니다. "
            "동그라미를 누르면 판정 근거가 전부 나옵니다. "
            "지도 타일은 OpenStreetMap 에서 받아 옵니다(인터넷 필요)."
        )


# ---------------------------------------------------------------------------
# 화면 본체
# ---------------------------------------------------------------------------

def main() -> None:
    st.markdown(TABLE_CSS, unsafe_allow_html=True)

    try:
        config = load_config()
    except ConfigError as exc:
        st.error("설정 파일에 문제가 있습니다.\n\n{}".format(exc))
        st.stop()
        return

    meta = load_meta()

    if not meta["latest"]:
        st.title("한·중 바지선 운항 기상 조회")
        st.warning(
            "아직 수집된 데이터가 없습니다.\n\n"
            "폴더에 있는 **1_수집하기.bat** 을 먼저 한 번 더블클릭해서 실행하세요.\n"
            "1~2분 뒤 이 화면을 새로고침(F5)하면 표가 나타납니다."
        )
        st.caption("데이터베이스 파일 위치: {}".format(DB_PATH))
        st.stop()
        return

    # ---------------- 사이드바 ----------------
    with st.sidebar:
        st.header("보기 설정")

        route_id = st.selectbox(
            "항로",
            options=list(config.routes.keys()),
            format_func=lambda rid: "{} · {}".format(
                config.routes[rid].label, config.routes[rid].name),
        )

        resolution_label = st.radio(
            "시간 간격",
            options=["3시간", "1시간"],
            index=0 if config.default_display_resolution.startswith("3") else 1,
            horizontal=True,
        )
        step_hours = 3 if resolution_label == "3시간" else 1

        days_ahead = st.slider("몇 일치를 볼지", min_value=1,
                               max_value=config.forecast_days, value=7)

        st.divider()
        counts = meta["counts"]
        st.caption("최근 예보 수집: {}".format(
            (meta["latest"] or "").replace("T", " ")))
        st.caption("원본 스냅샷: {}개 ({:,}행)".format(
            len(meta["snapshots"]), counts.get("forecast_snapshot", 0)))
        st.caption("아카이브: {}일치 ({:,}행)".format(
            len(meta["archives"]), counts.get("forecast_archive", 0)))
        st.caption("과거 실황: {:,}행".format(counts.get("observation", 0)))
        st.caption("특보 기록: {:,}건".format(counts.get("kma_warning", 0)))
        st.caption("DB 파일 크기: {} MB".format(database_size_mb()))

        if st.button("데이터 새로고침", width="stretch"):
            st.cache_data.clear()
            st.rerun()

        with st.expander("최근 수집 기록"):
            for run in meta["runs"]:
                mark = {"ok": "OK", "partial": "일부", "error": "실패",
                        "skipped": "건너뜀"}.get(run["status"], run["status"])
                st.text("{} [{}] {} {}행".format(
                    run["started_at"].replace("T", " "), run["kind"], mark,
                    run["rows_written"]))
                if run.get("message"):
                    st.caption(run["message"])

    locations = config.route_locations(route_id)
    location_ids = [loc.id for loc in locations]

    now = datetime.now(config.timezone).replace(minute=0, second=0, microsecond=0)
    valid_from = now.strftime("%Y-%m-%dT%H:%M")
    valid_to = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M")

    warning_collected, warning_rows = load_warning_data(meta["latest"] or "")

    st.title("한·중 바지선 운항 기상 조회")
    st.caption(
        "{} · 기준 {} KST · 표시 간격 {} · 시간대 Asia/Seoul".format(
            config.routes[route_id].name, now.strftime("%Y-%m-%d %H:%M"),
            resolution_label)
    )
    st.markdown(LEGEND_HTML, unsafe_allow_html=True)

    # 표에 쓸 예보를 먼저 읽는다. 지도와 표가 같은 자료를 쓴다.
    forecast_rows = load_forecast_data(
        tuple(location_ids), None, valid_from, valid_to, meta["latest"] or ""
    )
    times = time_columns(forecast_rows, step_hours)

    # ---------------- 지도 (표 위) ----------------
    render_route_map(config, route_id, location_ids, forecast_rows, times,
                     warning_rows, meta["latest"])

    tabs = st.tabs(TAB_NAMES)

    for index, (tab_name, columns) in enumerate(NUMERIC_TABS):
        with tabs[index]:
            unit_text = " · ".join(
                "{} ({})".format(COLUMN_LABELS[c], VALUE_COLUMNS[c]) for c in columns
            )
            st.caption("표시 항목: {}".format(unit_text))
            if not forecast_rows:
                st.info("이 기간에 저장된 예보가 없습니다.")
            else:
                st.markdown(
                    render_table(config, location_ids, forecast_rows, times, columns,
                                 warning_rows, meta["latest"], "예보"),
                    unsafe_allow_html=True,
                )
            if any(c in ("wave_height_m", "wave_direction_deg", "wave_period_s",
                         "current_speed_kn", "current_direction_deg") for c in columns):
                st.caption(
                    "참고: 해양(파고·파주기) 예보는 모델 한계로 약 9~10일까지만 "
                    "값이 있습니다. 그 이후 칸은 회색(데이터 없음)으로 표시됩니다."
                )

    # ---------------- 기상특보 탭 ----------------
    with tabs[len(NUMERIC_TABS)]:
        st.caption("출처: 기상청 API 허브 특보현황 (wrn_now_data_new.php)")
        if warning_collected:
            st.caption("수집 시각: {} KST".format(warning_collected.replace("T", " ")))
        if not warning_rows:
            st.info("수집된 특보 정보가 없습니다. 1_수집하기.bat 을 실행해 보세요.")
        else:
            st.markdown("#### 이 항로에 적용되는 특보")
            found_any = False
            for location in locations:
                applicable = judge.warnings_for_location(config, warning_rows, location.id)
                st.markdown("**{}**".format(location.name))
                if not location.kma_zones:
                    if location.country == "CN":
                        st.caption(
                            "중국 관할 해역·항만입니다. 기상청 특보는 이 지점을 다루지 "
                            "않습니다. 중국 측 특보는 별도 공급자가 필요합니다."
                        )
                    else:
                        st.caption(
                            "기상청 특보구역이 지정되지 않은 지점입니다. "
                            "이 칸은 숫자 값만으로 판단합니다."
                        )
                    continue
                if not applicable:
                    st.caption("현재 발효 중인 특보 없음 (구역: {})".format(
                        ", ".join(location.kma_zones)))
                    continue
                found_any = True
                st.dataframe(
                    [
                        {
                            "구역": "{} {}".format(row["reg_id"], row["reg_ko"]),
                            "특보": row["wrn"],
                            "수준": row["lvl"],
                            "명령": row["cmd"],
                            "발표": format_kma_time(row["tm_fc"]),
                            "발효": format_kma_time(row["tm_ef"]),
                            "해제예고": row["ed_tm"],
                        }
                        for row in applicable
                    ],
                    width="stretch",
                    hide_index=True,
                )
            if not found_any:
                st.success("현재 이 항로의 한국 측 지점에 발효 중인 특보가 없습니다.")

            with st.expander("전국 특보 현황 전체 보기 ({}건)".format(len(warning_rows))):
                st.dataframe(
                    [
                        {
                            "상위구역": row["reg_up_ko"],
                            "구역코드": row["reg_id"],
                            "구역": row["reg_ko"],
                            "특보": row["wrn"],
                            "수준": row["lvl"],
                            "명령": row["cmd"],
                            "발표": format_kma_time(row["tm_fc"]),
                            "발효": format_kma_time(row["tm_ef"]),
                            "해제예고": row["ed_tm"],
                        }
                        for row in warning_rows
                    ],
                    width="stretch",
                    hide_index=True,
                )

    # ---------------- 운항 판단 탭 ----------------
    with tabs[len(NUMERIC_TABS) + 1]:
        st.caption(
            "가능 / 조건 / 불가. 마우스를 셀에 올리면 판정 근거가 나옵니다."
        )
        st.warning(
            "이 판정은 화면 경보용입니다. 자동 출항 승인 기준이 아닙니다. "
            "실제 피항·출항 판단은 담당자가 하십시오.",
            icon="⚠️",
        )
        if not forecast_rows:
            st.info("이 기간에 저장된 예보가 없습니다.")
        else:
            st.markdown(
                render_table(config, location_ids, forecast_rows, times, [],
                             warning_rows, meta["latest"], "예보",
                             judgement_mode=True),
                unsafe_allow_html=True,
            )
        with st.expander("현재 적용 중인 한계값 보기"):
            st.json(config.thresholds)

    # ---------------- 과거 실황 탭 ----------------
    with tabs[len(NUMERIC_TABS) + 2]:
        st.caption(
            "수집 시점마다 저장해 둔 현재값입니다. 예보와 섞지 않습니다."
        )
        past_days = st.slider("며칠 전까지 볼지", 1, 30, 3, key="past_days")
        start = (now - timedelta(days=past_days)).strftime("%Y-%m-%dT%H:%M")
        # 현재값은 정시가 아니라 수집한 순간(예: 01:27)에 저장된다.
        # now 는 정시로 내림한 값이라 그대로 끝 시각으로 쓰면 방금 수집분이 빠진다.
        end = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        observations = load_observation_data(
            tuple(location_ids), start, end, meta["latest"] or ""
        )
        if not observations:
            st.info(
                "저장된 현재값이 아직 없습니다. 수집을 여러 번 돌리면 여기에 쌓입니다."
            )
        else:
            kinds = {row.get("data_kind") for row in observations}
            if kinds == {"model_current"}:
                st.info(
                    "표시된 값은 관측값이 아니라 **모델 기반 현재값**입니다. "
                    "기상청 부이·등표 관측값 연동은 아직 붙이지 않았습니다.",
                    icon="ℹ️",
                )
            renamed = [
                {**row, "valid_time": row["observed_at"]} for row in observations
            ]
            # 현재값은 정시가 아닌 시각(01:27 등)에 저장되므로
            # 3시간 간격 걸러내기를 적용하면 전부 사라진다. 저장된 시각을 모두 쓴다.
            observation_times = time_columns(renamed, 1)
            st.markdown(
                render_table(config, location_ids, renamed, observation_times,
                             ["wind_speed_ms", "wind_gust_ms", "wave_height_m"],
                             warning_rows, None, "현재값(모델)"),
                unsafe_allow_html=True,
            )
            st.caption("표시 항목: 평균 풍속 (m/s) / 순간 풍속 (m/s) / 유의파고 (m)")

    # ---------------- 예보 이력 탭 ----------------
    with tabs[len(NUMERIC_TABS) + 3]:
        st.caption(
            "특정 수집 시점에 받았던 예보를 그대로 다시 봅니다. "
            "나중에 실제 날씨와 비교해 예보가 맞았는지 확인할 때 씁니다."
        )

        # 목록은 두 종류가 섞여 있다.
        #   원본   = 최근 7일. 전체 9항목, 3~6시간 간격, 10일 앞까지.
        #   아카이브 = 그 이전 1년. 5항목 + 판정, 3시간 간격, 7일 앞까지.
        options: list[tuple[str, str, str]] = []
        for value in meta["snapshots"]:
            options.append(("raw", value, "원본 · {} KST".format(value.replace("T", " "))))
        for day, collected in meta["archives"]:
            options.append(("archive", day, "아카이브 · {} (09시 근방 수집분)".format(day)))

        if not options:
            st.info("저장된 스냅샷이 없습니다.")
        else:
            st.caption(
                "원본 {}개 · 아카이브 {}일치. 아카이브는 용량을 줄이려고 "
                "풍속·풍향·순간풍속·시정·유의파고와 운항 판단만 남긴 것입니다.".format(
                    len(meta["snapshots"]), len(meta["archives"]))
            )
            picked = st.selectbox(
                "수집 시점",
                options=list(range(len(options))),
                format_func=lambda i: options[i][2],
            )
            kind, key, _ = options[picked]

            if kind == "raw":
                available = [c for c in VALUE_COLUMNS
                             if not c.endswith("_direction_deg")]
                history_rows = load_forecast_data(
                    tuple(location_ids), key, None, None, meta["latest"] or "")
                data_kind = "예보 스냅샷(원본)"
                collected_label = key
            else:
                available = [c for c in ARCHIVE_VALUE_COLUMNS
                             if not c.endswith("_direction_deg")]
                history_rows = load_archive_data(
                    tuple(location_ids), key, meta["latest"] or "")
                data_kind = "예보 스냅샷(아카이브)"
                collected_label = history_rows[0]["collected_at"] if history_rows else key
                st.info(
                    "아카이브 스냅샷입니다. 3시간 간격, 7일 앞까지만 있고 "
                    "강수·파주기는 저장돼 있지 않습니다.",
                    icon="ℹ️",
                )

            history_column = st.selectbox(
                "볼 항목",
                options=[*available, "__judgement__"],
                format_func=lambda c: ("운항 판단" if c == "__judgement__"
                                       else "{} ({})".format(COLUMN_LABELS[c],
                                                             VALUE_COLUMNS[c])),
                key="history_column_{}".format(kind),
            )

            if not history_rows:
                st.info("이 시점에 저장된 예보가 없습니다.")
            else:
                history_times = time_columns(history_rows, 1)
                st.markdown(
                    render_table(
                        config, location_ids, history_rows, history_times,
                        [] if history_column == "__judgement__" else [history_column],
                        warning_rows, collected_label, data_kind,
                        judgement_mode=(history_column == "__judgement__"),
                        stored_judgement=(kind == "archive"),
                    ),
                    unsafe_allow_html=True,
                )

    st.divider()
    st.caption(
        "데이터 출처: ECMWF IFS+WAM (CC BY 4.0), NOAA GFS, MET Norway (CC BY 4.0), 기상청 API 허브. "
        "모두 상업적 사용이 허용된 자료입니다."
    )


main()
