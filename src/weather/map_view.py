"""표 위에 띄우는 OpenStreetMap 지도.

왜 Leaflet 인가
---------------
Streamlit 에 지도 명령(st.map, st.pydeck_chart)이 있지만 둘 다 Carto 타일을 쓴다.
OpenStreetMap 타일을 pydeck 의 TileLayer 로 붙여 봤으나 화면에 아예 뜨지 않았다.
그래서 OSM 공식 뷰어인 Leaflet 을 st.iframe 안에 직접 띄운다.

지도에 그리는 것
---------------
  - 항로 선 : 지점을 항해 순서대로 이은 선
  - 지점 표시: 선택한 시각의 상태에 따라 초록/노랑/빨강/회색 원
  - 라벨    : 지점 이름과 선택한 항목의 값
  - 화살표  : 바람이 불어 가는 방향 (풍향)
  - 클릭    : 그 칸의 판정 근거 전체가 뜬다 (표 툴팁과 같은 내용)

인터넷이 필요하다. 타일과 Leaflet 을 인터넷에서 받아 오기 때문이다.
(예보 자체도 인터넷으로 받으므로 새로 생기는 제약은 아니다.)
"""

from __future__ import annotations

import json
from typing import Any

from . import judge
from .config import Config
from .db import COLUMN_LABELS, VALUE_COLUMNS

# 지도 라벨에 고를 수 있는 항목. 왼쪽이 내부 이름, 오른쪽이 화면 표시.
MAP_METRICS: list[tuple[str, str]] = [
    ("__judgement__", "운항 판단"),
    ("wind_speed_ms", "평균 풍속"),
    ("wind_gust_ms", "순간 풍속"),
    ("wave_height_m", "유의파고"),
    ("visibility_km", "시정"),
]

LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"
LEAFLET_JS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"


def build_map_points(
    config: Config,
    location_ids: list[str],
    rows_by_key: dict[tuple[str, str], dict[str, Any]],
    warning_rows: list[dict[str, Any]],
    valid_time: str,
    metric: str,
    collected_label: str | None,
    data_kind: str,
) -> list[dict[str, Any]]:
    """지도에 찍을 지점 목록을 만든다.

    rows_by_key 는 (위치ID, 유효시각) -> 예보 한 줄.
    """
    points: list[dict[str, Any]] = []

    for order, location_id in enumerate(location_ids, start=1):
        location = config.locations[location_id]
        record = rows_by_key.get((location_id, valid_time))

        if record is None:
            points.append({
                "order": order,
                "name": location.name,
                "lat": location.latitude,
                "lon": location.longitude,
                "color": judge.STATUS_COLORS[judge.NO_DATA],
                "label": "데이터 없음",
                "detail": "{}\n{} 시점의 예보가 없습니다.".format(
                    location.name, valid_time.replace("T", " ")),
                "wind_dir": None,
                "wind_speed": None,
            })
            continue

        location_warnings = judge.warnings_for_location(
            config, warning_rows, location_id)
        hits = judge.active_warnings_at(config, location_warnings, valid_time)
        verdict = judge.judge_cell(config, record, hits)

        if metric == "__judgement__":
            status = verdict.status
            label = judge.JUDGEMENT_LABELS_KO[status]
            tooltip = judge.build_tooltip(
                verdict, location.name, valid_time, collected_label, data_kind)
        else:
            value = record.get(metric)
            result = judge.evaluate_metric(metric, value, config.thresholds)
            status = result.status
            label = "{} {}".format(
                judge.format_value(metric, value), VALUE_COLUMNS.get(metric, ""))
            if value is None:
                label = "데이터 없음"
            tooltip = judge.build_tooltip(
                verdict, location.name, valid_time, collected_label, data_kind,
                tab_status=status, tab_label=COLUMN_LABELS.get(metric, metric))

        points.append({
            "order": order,
            "name": location.name,
            "lat": location.latitude,
            "lon": location.longitude,
            "color": judge.STATUS_COLORS[status],
            "label": label,
            "detail": tooltip,
            "wind_dir": record.get("wind_direction_deg"),
            "wind_speed": record.get("wind_speed_ms"),
        })

    return points


def render_map_html(points: list[dict[str, Any]], route_name: str,
                    valid_time: str, metric_label: str,
                    height: int = 460,
                    main_count: int | None = None) -> str:
    """Leaflet 지도 HTML 한 덩어리를 만든다.

    데이터는 전부 JSON 으로 넣는다. 문자열을 HTML 에 직접 이어 붙이지 않아
    지점 이름에 따옴표나 꺾쇠가 들어 있어도 깨지지 않는다.
    """
    # </script> 가 데이터 안에 있으면 스크립트가 잘리므로 '<' 를 이스케이프한다.
    payload = json.dumps(points, ensure_ascii=False).replace("<", "\\u003c")
    # 앞에서부터 몇 개가 '한 줄로 이어지는 본선'인지.
    # 나머지는 본선 마지막 지점에서 갈라지는 도착지다.
    main_n = len(points) if main_count is None else int(main_count)
    # 지도 안 배지는 짧게. 항로 이름은 지도 위쪽 화면에 이미 나와 있다.
    header = json.dumps(
        "{} · {}".format(valid_time.replace("T", " "), metric_label),
        ensure_ascii=False,
    ).replace("<", "\\u003c")

    return """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="{css}"/>
<script src="{js}"></script>
<style>
  html, body {{ height: 100%; margin: 0; background: #ffffff; }}
  #map {{ height: 100%; width: 100%; background: #dfe8ef; }}
  /* 시각 배지. white-space:nowrap 이 없으면 글자가 세로로 쏟아진다. */
  .wx-head {{
    background: rgba(255,255,255,0.93); border: 1px solid #c0c0c0;
    border-radius: 4px; padding: 3px 9px; font: 600 12px/1.3 sans-serif;
    color: #202020; white-space: nowrap;
  }}
  /* 라벨은 짧게 유지한다. 길면 지점끼리 겹쳐 못 읽는다.
     전체 이름과 판정 근거는 동그라미를 눌렀을 때 나온다. */
  .wx-pin {{
    font: 700 11px/1.2 sans-serif; white-space: nowrap;
    padding: 1px 5px; border-radius: 3px; border: 1px solid #b0b0b0;
    background: rgba(255,255,255,0.94); color: #202020;
    box-shadow: none;
  }}
  .wx-pin::before {{ display: none; }}
  .wx-pop {{
    font: 12px/1.45 monospace; white-space: pre; color: #202020;
    max-height: 300px; overflow: auto;
  }}
  .leaflet-popup-content {{ margin: 8px 10px; width: auto !important; }}
  .wx-fail {{ font: 13px sans-serif; color: #444; padding: 16px; }}
</style></head>
<body>
<div id="map"></div>
<script>
(function () {{
  var pts = {payload};
  var head = {header};
  var mainN = {main_n};

  if (typeof L === "undefined") {{
    document.getElementById("map").innerHTML =
      '<div class="wx-fail">지도를 불러오지 못했습니다. 인터넷 연결을 확인하세요.<br>' +
      '지도가 없어도 아래 표는 정상으로 동작합니다.</div>';
    return;
  }}

  var map = L.map("map", {{ scrollWheelZoom: false }});
  L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    maxZoom: 18, attribution: '&copy; OpenStreetMap 기여자'
  }}).addTo(map);

  var caption = L.control({{ position: "bottomleft" }});
  caption.onAdd = function () {{
    var d = L.DomUtil.create("div", "wx-head");
    d.textContent = head;
    L.DomEvent.disableClickPropagation(d);
    return d;
  }};
  caption.addTo(map);

  // 팝업 내용을 DOM 요소로 만들어 넘긴다.
  // textContent 로 넣으므로 지점 이름에 무슨 글자가 있어도 HTML 로 해석되지 않는다.
  function makePopup(text) {{
    var d = document.createElement("div");
    d.className = "wx-pop";
    d.textContent = text;
    return d;
  }}

  /* 본선은 한 줄로 잇고, 도착지는 본선 마지막 지점에서 따로 뻗는다.
     전부 한 줄로 이으면 도착지끼리 없는 항로가 그려진다. */
  var line = pts.map(function (p) {{ return [p.lat, p.lon]; }});
  var LINE = {{ color: "#3f6fd0", weight: 2, opacity: 0.85, dashArray: "6 5" }};
  L.polyline(line.slice(0, mainN), LINE).addTo(map);
  if (mainN > 0) {{
    var branch = line[mainN - 1];
    for (var bi = mainN; bi < line.length; bi++) {{
      L.polyline([branch, line[bi]], LINE).addTo(map);
    }}
  }}

  pts.forEach(function (p) {{
    // 풍향 화살표: 바람이 불어 가는 쪽으로 짧은 선을 긋는다.
    // 기상 풍향은 '불어오는 방향'이므로 180도를 더해 진행 방향으로 바꾼다.
    if (p.wind_dir !== null && p.wind_speed) {{
      var rad = (p.wind_dir + 180) * Math.PI / 180;
      var len = 0.10 + Math.min(p.wind_speed, 20) * 0.012;
      var dLat = Math.cos(rad) * len;
      var dLon = Math.sin(rad) * len / Math.cos(p.lat * Math.PI / 180);
      L.polyline([[p.lat, p.lon], [p.lat + dLat, p.lon + dLon]],
                 {{ color: p.color, weight: 3, opacity: 0.9 }}).addTo(map);
    }}

    // 라벨은 '번호 값' 만. 지점이 몰려 있어도 겹침이 적다.
    // 라벨을 지점마다 위/아래로 엇갈리게 붙여 겹침을 한 번 더 줄인다.
    var up = (p.order % 2 === 1);
    L.circleMarker([p.lat, p.lon], {{
      radius: 8, color: "#ffffff", weight: 2,
      fillColor: p.color, fillOpacity: 1
    }}).addTo(map)
      .bindTooltip(p.order + " " + p.label, {{
        permanent: true,
        direction: up ? "top" : "bottom",
        offset: up ? [0, -9] : [0, 9],
        className: "wx-pin",
        opacity: 1
      }})
      // 팝업 내용을 '바인딩할 때' 넣어야 한다.
      // 열린 뒤에 채우면 Leaflet 이 빈 상태(폭 0)로 크기를 잡아
      // 글자가 한 줄에 한 자씩 세로로 쏟아진다.
      .bindPopup(makePopup(p.detail), {{ maxWidth: 520, minWidth: 240 }});
  }});

  if (line.length === 1) {{
    map.setView(line[0], 9);
  }} else {{
    map.fitBounds(L.latLngBounds(line), {{ padding: [45, 45] }});
  }}
}})();
</script>
</body></html>""".format(css=LEAFLET_CSS, js=LEAFLET_JS,
                         payload=payload, header=header, main_n=main_n)
