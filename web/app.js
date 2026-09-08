/* ==========================================================================
   바지선 운항 기상 - 화면 동작
   --------------------------------------------------------------------------
   서버가 따로 없다. web/data/*.json 세 개를 읽어서 브라우저가 전부 그린다.
   그래서 이 폴더(web/)를 통째로 어디에 올려도 그대로 돌아간다.

   판정(가능/조건/불가)은 파이썬이 미리 계산해 넣어 준 값을 그대로 쓴다.
   ('st' 배열의 글자 하나: n=가능 c=조건 u=불가 x=데이터없음)
   여기서 다시 계산하지 않으므로 표와 지도와 폰 화면의 색이 어긋날 일이 없다.
   항목 하나짜리 색(예: 풍속 칸 색)만 한계값과 비교해 여기서 정한다.
   ========================================================================== */

(function () {
  "use strict";

  var META = null, FC = null, WARN = null, GRID = null, HIST = null;
  var gridLayer = null, gridCells = [], gridOn = true;
  var state = {
    route: null,
    way: "out",          /* out=가는 길, back=오는 길 */
    timeIndex: 0,
    view: "summary",
    metric: "judge",
    gridMetric: "judge",
    location: null
  };
  var map = null, mapLayers = [];
  var mapFitted = false;   /* 항로를 바꿀 때만 화면을 다시 맞춘다 */
  var mapDrawing = false;  /* 그리는 중 또 그리지 않게 하는 빗장 */
  var mapWaits = 0;        /* 지도 칸 크기가 잡히기를 기다린 횟수 */
  /* 이 항로를 보기에 알맞은 배율 범위. 여기를 벗어나면 뭔가 잘못된 것이다. */
  var MAP_ZOOM_MIN = 4, MAP_ZOOM_MAX = 12;

  var $ = function (id) { return document.getElementById(id); };

  // ---------------------------------------------------------------- 유틸
  function fmtTime(iso) {
    if (!iso) return "—";
    return iso.slice(5, 10).replace("-", "/") + " " + iso.slice(11, 16);
  }
  function fmtDay(iso) {
    var d = new Date(iso + ":00");
    var names = ["일", "월", "화", "수", "목", "금", "토"];
    return iso.slice(5, 10).replace("-", "월 ") + "일 (" + names[d.getDay()] + ")";
  }
  /* 16방위를 한글로. N/S/E/W 대신 북/남/동/서 를 쓴다. */
  function compass(deg) {
    if (deg === null || deg === undefined) return "";
    var n = ["북","북북동","북동","동북동","동","동남동","남동","남남동",
             "남","남남서","남서","서남서","서","서북서","북서","북북서"];
    return n[Math.round((deg % 360) / 22.5) % 16];
  }
  function num(v, digits) {
    if (v === null || v === undefined) return "—";
    return Number(v).toFixed(digits === undefined ? 1 : digits);
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  /* 항목 하나의 색을 한계값과 비교해 정한다.
     값이 클수록 나쁜 항목과 작을수록 나쁜 항목(시정)을 나눠 본다. */
  function metricStatus(key, value) {
    if (value === null || value === undefined) return "x";
    var t = META.thresholds[key];
    if (!t || t.auto === false) return "n";
    if (t.unavailable_at !== null && t.unavailable_at !== undefined
        && value >= t.unavailable_at) return "u";
    if (t.caution_at !== null && t.caution_at !== undefined
        && value >= t.caution_at) return "c";
    if (t.unavailable_below !== null && t.unavailable_below !== undefined
        && value < t.unavailable_below) return "u";
    if (t.caution_below !== null && t.caution_below !== undefined
        && value < t.caution_below) return "c";
    return "n";
  }

  function routeObj() {
    for (var i = 0; i < META.routes.length; i++) {
      if (META.routes[i].id === state.route) return META.routes[i];
    }
    return META.routes[0];
  }
  function metricObj(key) {
    for (var i = 0; i < META.metrics.length; i++) {
      if (META.metrics[i].key === key) return META.metrics[i];
    }
    return META.metrics[0];
  }
  function series(locId) { return FC.series[locId] || null; }

  /* 그 지점의 판정. 가는 길과 오는 길이 다르다.

     파도를 어느 쪽에서 맞느냐가 방향에 따라 정반대가 되기 때문이다.
     (여수 남방은 갈 때 등파인 시각이 올 때는 맞파가 된다)
     파이썬이 두 벌을 미리 계산해 보내 준다. */
  function stOf(locId, idx) {
    var s = series(locId);
    if (!s) return "x";
    var arr = (state.way === "back" && s.st_back) ? s.st_back : s.st;
    return arr ? arr[idx] : "x";
  }

  /* 짐을 싣고 내릴 수 있는 상태인지. 부두 네 곳에만 값이 있다.
     기준은 순간풍속(돌풍) 10 / 12 m/s 다. 크레인에 매달린 화물이
     순간적인 돌풍에 흔들리기 때문에 평균 풍속이 아니라 돌풍을 본다.
     ★ 색(운항 판단)과 섞지 않는다. 색은 "거기까지 갈 수 있나" 이고
       이건 "가서 짐을 싣고 내릴 수 있나" 라 다른 이야기다. */
  function berthOf(locId, idx) {
    var s = series(locId);
    if (!s || !s.berth) return null;
    return s.berth[idx] || null;
  }

  /* 선하역 기준이 보는 값과 화면 이름의 짝.
     설정(berthing_thresholds)에 적힌 이름을 시리즈 이름으로 옮긴다. */
  var BERTH_KEY = {
    wind_speed_ms: "wind",
    wind_gust_ms: "gust",
    wave_height_m: "wave"
  };

  /* 설정에 적힌 선하역 기준만 골라 준다.
     applies_to(목록)와 basis(글)는 기준이 아니므로 건너뛴다. */
  function berthRules() {
    var rule = META.berthing || {};
    var out = [];
    Object.keys(rule).forEach(function (col) {
      var lim = rule[col];
      var key = BERTH_KEY[col];
      if (!key || !lim || typeof lim !== "object" || lim.caution_at === undefined) return;
      out.push({ col: col, key: key, lim: lim });
    });
    return out;
  }

  /* 왜 그 표시가 떴는지. "돌풍 16.2m/s" 처럼 실제 값을 돌려준다.

     기준값을 글에 박아 두면 설정을 고칠 때마다 화면 글이 어긋난다.
     실제로 한 번 어긋났었다. 그래서 설정에서 읽어 만든다. */
  function berthReason(locId, idx) {
    var s = series(locId);
    if (!s) return "";
    var best = null;
    berthRules().forEach(function (r) {
      var arr = s[r.key];
      if (!arr) return;
      var v = arr[idx];
      if (v === null || v === undefined) return;
      var lvl = 0;
      if (r.lim.unavailable_at !== null && r.lim.unavailable_at !== undefined
          && v >= r.lim.unavailable_at) lvl = 2;
      else if (r.lim.caution_at !== null && r.lim.caution_at !== undefined
          && v >= r.lim.caution_at) lvl = 1;
      if (!lvl) return;
      /* 더 나쁜 것, 같은 등급이면 기준을 더 많이 넘은 것 */
      var over = v / (lvl === 2 ? r.lim.unavailable_at : r.lim.caution_at);
      if (!best || lvl > best.lvl || (lvl === best.lvl && over > best.over)) {
        best = { lvl: lvl, over: over, key: r.key, v: v };
      }
    });
    if (!best) return "";
    /* 자료에 따라 소수 둘째 자리까지 오는 값이 있어 글이 길어진다.
       한 자리로 맞추되 정수는 소수점을 안 붙인다. */
    var shown = Math.round(best.v * 10) / 10;
    return (META.labels[best.key] || best.key) + " "
      + shown + (META.units[best.key] || "");
  }

  /* 파도를 어느 쪽에서 맞는지. 소요 시간 계산에 쓴다. */
  function waveSideAt(locId, idx) {
    var route = routeObj();
    var course = (route.courses || {})[locId];
    var s = series(locId);
    if (course === undefined || !s || !s.vdir) return "head";
    var wd = s.vdir[idx];
    if (wd === null || wd === undefined) return "head";
    if (state.way === "back") course = (course + 180) % 360;
    var rel = Math.abs(((wd - course + 180) % 360 + 360) % 360 - 180);
    return rel < 60 ? "head" : (rel < 120 ? "beam" : "following");
  }

  /* 그 시각에 실제로 걸려 있는 특보만 고른다.

     특보에는 발효 시각(from)과 해제 예정 시각(until)이 들어 있다.
     이걸 안 보면 이미 풀린 특보가 열흘 뒤 예보에까지 붙는다.
     (실제로 7일에 풀릴 강풍주의보가 16일까지 따라다녔다)

     until 이 없는 특보(해제 예고를 읽지 못한 것)는 계속 유효로 본다.
     판정 쪽에서는 48시간 안전장치를 두지만, 화면 글자는 보수적으로 남긴다. */
  function warningsAt(locId, idx) {
    var all = FC.warnings_by_location[locId] || [];
    var t = FC.times[idx];
    if (!t) return all;
    return all.filter(function (w) {
      if (w.from && t < w.from) return false;
      if (w.until && t >= w.until) return false;
      return true;
    });
  }

  /* 지점·시각의 값들을 줄 단위로 만든다.

     각 줄을 {head, value, tail} 세 토막으로 돌려준다.
     value 가 '지금 값' 이다. 지도 팝업에서는 이 토막만 굵게 그린다.
     같은 줄에 불가 기준이 같이 적혀 있어서, 굵게 하지 않으면 어느 쪽이
     지금 값인지 헷갈린다. */
  function detailParts(locId, idx) {
    var s = series(locId), loc = META.locations[locId];
    var out = [];
    if (!s) { out.push({ head: loc ? loc.name : locId }); return out; }

    out.push({ head: loc.name + " / " + fmtTime(FC.times[idx]) });
    out.push({ head: "판정: ", value: META.status_labels[stOf(locId, idx)] });

    /* 자료원이 ECMWF+NOAA 로 바뀌면서 뇌우·해류·수온은 받지 않는다. */
    var order = ["wind", "gust", "wave", "vis", "prec", "vper"];
    for (var i = 0; i < order.length; i++) {
      var k = order[i], v = s[k] ? s[k][idx] : null;
      if (v === null || v === undefined) {
        out.push({ head: META.labels[k] + ": ", value: "데이터 없음" });
        continue;
      }
      var unit = META.units[k] || "";
      var tail = "";
      var t = META.thresholds[k];
      if (t && t.auto !== false) {
        /* "불가 기준" 대신 "불가". 앞에 / 가 있어 뜻이 통하고,
           팝업 한 줄이 폰 화면에 들어가려면 글자를 아껴야 한다. */
        if (t.unavailable_at !== null && t.unavailable_at !== undefined) {
          tail += " / 불가 " + t.unavailable_at + unit;
        } else if (t.unavailable_below !== null && t.unavailable_below !== undefined) {
          tail += " / 불가 " + t.unavailable_below + unit + " 미만";
        }
        tail += " (" + META.metric_status_labels[metricStatus(k, v)] + ")";
      }
      out.push({ head: META.labels[k] + ": ", value: v + unit, tail: tail });
    }

    if (s.wdir && s.wdir[idx] !== null) {
      out.push({ head: "풍향: ",
                 value: compass(s.wdir[idx]) + " (" + s.wdir[idx] + "°)",
                 tail: " · 불어오는 쪽" });
    }
    if (s.code && s.code[idx] !== null) {
      out.push({ head: "날씨: ",
                 value: META.wmo[s.code[idx]] || ("코드 " + s.code[idx]) });
    }

    var ws = warningsAt(locId, idx);
    if (ws.length) {
      out.push({ head: "---- 기상특보 ----" });
      ws.forEach(function (w) {
        out.push({ head: "", value: w.wrn + " " + w.lvl,
                   tail: " (" + w.cmd + ") [" + w.reg_ko + "]"
                         + (w.ed_tm ? " / 해제예고 " + w.ed_tm : "") });
      });
    }

    out.push({ head: "데이터 기준: 예보" });
    out.push({ head: "예보 수집 시각: " + fmtTime(META.forecast_collected_at) + " KST" });
    /* 자료원이 바뀌면 이 줄도 같이 바뀌도록 meta.json 에서 가져다 쓴다.
       예전에는 "Open-Meteo" 를 글자로 박아 둬서, 자료원을 ECMWF 로 바꾼
       뒤에도 지도 팝업에는 옛 출처가 그대로 나왔다. */
    out.push({ head: "출처: " + (META.sources || "ECMWF · NOAA · MET Norway · 기상청") });
    return out;
  }

  /* 글자 한 덩어리. 표 셀에 마우스를 올리면 뜨는 툴팁에 쓴다.
     (브라우저 기본 툴팁이라 굵게 같은 꾸밈을 넣을 수 없다) */
  function detailText(locId, idx) {
    return detailParts(locId, idx).map(function (p) {
      return (p.head || "") + (p.value || "") + (p.tail || "");
    }).join("\n");
  }

  /* 지도 팝업.

     ★ 짧게 유지한다. 예전에는 상세 화면과 똑같이 12줄을 넣었더니
       팝업이 지도를 통째로 덮어 닫기 버튼조차 가렸다.
       지도에서는 "이 지점이 지금 어떤지" 만 보면 된다.
       불가 기준·수집 시각·출처는 '자세히' 를 눌러 지점 화면에서 본다. */
  function popupNode(locId, idx) {
    var s = series(locId), loc = META.locations[locId];
    var box = el("div", "wx-pop");
    if (!loc) return box;

    box.appendChild(el("div", "wx-name", loc.name));
    box.appendChild(el("div", "wx-when", fmtTime(FC.times[idx]) + " 기준"));

    if (!s) {
      box.appendChild(el("div", "wx-none", "자료 없음"));
      return box;
    }

    var st = stOf(locId, idx);
    box.appendChild(el("div", "wx-verdict s-" + st, META.status_labels[st]));

    /* 값 넷만. 기준은 빼고 값 자체에 색으로 상태를 담는다. */
    var rows = el("div", "wx-rows");
    [["wind", "풍속"], ["gust", "돌풍"], ["wave", "파고"], ["vis", "시정"]]
      .forEach(function (p) {
        var k = p[0], v = s[k] ? s[k][idx] : null;
        rows.appendChild(el("span", "wx-k", p[1]));
        if (v === null || v === undefined) {
          rows.appendChild(el("span", "wx-v", "—"));
          return;
        }
        var vv = el("span", "wx-v s-" + metricStatus(k, v));
        vv.textContent = v + (META.units[k] || "");
        if (k === "wind" && s.wdir && s.wdir[idx] !== null) {
          vv.textContent += " " + compass(s.wdir[idx]);
        }
        rows.appendChild(vv);
      });
    box.appendChild(rows);

    var bth = berthOf(locId, idx);
    if (bth && bth !== "n") {
      box.appendChild(el("div", "wx-berth s-" + bth,
        (bth === "u" ? "선하역 불가" : "선하역 주의")
        + (berthReason(locId, idx) ? " (" + berthReason(locId, idx) + ")" : "")));
    }

    var ws = warningsAt(locId, idx);
    if (ws.length) {
      var w = ws[0];
      for (var i = 0; i < ws.length; i++) {
        if (ws[i].lvl === "경보") { w = ws[i]; break; }
      }
      box.appendChild(el("div", "wx-warn s-" + (w.lvl === "경보" ? "u" : "c"),
        "특보 " + w.wrn + w.lvl + (ws.length > 1 ? " 외 " + (ws.length - 1) : "")));
    }

    var more = el("button", "wx-more", "자세히 보기 ›");
    more.type = "button";
    more.onclick = function () {
      if (map) map.closePopup();
      state.location = locId;
      show("detail");
    };
    box.appendChild(more);
    return box;
  }

  /* ---------------------------------------------------------------- 주소 QR
     회의실에서 주소를 불러 주는 대신 화면을 보여 준다.
     QR 그림(qr.svg)은 미리 만들어 둔 것이라 그리는 코드가 필요 없다. */

  var QR_URL = "https://tinyurl.com/barge-weather";

  function showQr(on) {
    var m = $("qrModal");
    if (!m) return;
    m.hidden = !on;
    if (on) {
      var c = $("qrClose");
      if (c) c.focus();
    } else {
      var b = $("qrBtn");
      if (b) b.focus();
    }
  }

  function copyUrl() {
    var btn = $("qrCopy");
    function done(ok) {
      if (!btn) return;
      btn.textContent = ok ? "복사했습니다" : "복사 실패";
      setTimeout(function () { btn.textContent = "주소 복사"; }, 1600);
    }
    /* 옛 브라우저나 http 로 열었을 때는 clipboard 가 없다. 그때는
       숨긴 칸에 넣고 execCommand 로 복사한다. */
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(QR_URL).then(function () { done(true); },
                                                 function () { done(false); });
      return;
    }
    try {
      var ta = document.createElement("textarea");
      ta.value = QR_URL;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      done(document.execCommand("copy"));
      document.body.removeChild(ta);
    } catch (e) { done(false); }
  }

  /* ---------------------------------------------------------------- 밝기
     시스템 설정만 따르던 것을 손으로도 고를 수 있게 한다.
     고른 값은 그 기기에만 기억된다(localStorage). */

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "light"
      ? "light" : "dark";
  }

  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    try { localStorage.setItem("theme", mode); } catch (e) { /* 사생활 보호 모드 */ }

    var btn = $("themeBtn");
    if (btn) {
      /* 지금 상태가 아니라 '누르면 어떻게 되는지' 를 보여 준다. */
      btn.textContent = (mode === "dark") ? "☀" : "☾";
      btn.title = (mode === "dark") ? "밝은 화면으로" : "어두운 화면으로";
      btn.setAttribute("aria-label", btn.title);
    }
    /* 폰 위쪽 상태 막대 색도 맞춘다. */
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", mode === "dark" ? "#0f172a" : "#f4f6f8");

    /* 지도는 색을 스스로 다시 칠하지 않으므로 다시 그려 준다. */
    if (map && state.view === "map") renderMap();
  }

  // ---------------------------------------------------------------- 자료 읽기
  function load() {
    var bust = "?t=" + Date.now();
    return Promise.all([
      fetch("data/meta.json" + bust).then(function (r) { return r.json(); }),
      fetch("data/forecast.json" + bust).then(function (r) { return r.json(); }),
      fetch("data/warnings.json" + bust).then(function (r) { return r.json(); })
    ]).then(function (all) {
      META = all[0]; FC = all[1]; WARN = all[2];
      TIME_MS = null;      /* 시각이 바뀌었으니 다시 만든다 */
      if (!state.route) state.route = META.routes[0].id;
      state.timeIndex = nowIndex();
      /* 격자와 과거 기록은 없을 수도 있다(설정에서 껐거나 아직 안 받았을 때).
         없어도 나머지 화면은 그대로 동작해야 하므로 실패를 조용히 넘긴다. */
      var g1 = fetch("data/grid.json" + bust)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (g) { GRID = g; gridCells = []; gridLayer = null; })
        .catch(function () { GRID = null; });
      var g2 = fetch("data/history.json" + bust)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (h) { HIST = h; })
        .catch(function () { HIST = null; });
      return Promise.all([g1, g2]);
    });
  }

  /* 지금 시각에 가장 가까운 칸을 찾는다. */
  function nowIndex() {
    var now = new Date();
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    var key = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-"
            + pad(now.getDate()) + "T" + pad(now.getHours()) + ":00";
    for (var i = 0; i < FC.times.length; i++) {
      if (FC.times[i] >= key) return i;
    }
    return 0;
  }

  // ---------------------------------------------------------------- 그리기
  function renderChips() {
    var box = $("routeChips");
    box.innerHTML = "";
    META.routes.forEach(function (r) {
      var b = el("button", "chip" + (r.id === state.route ? " on" : ""),
                 r.short || r.id);
      b.type = "button";
      b.title = r.name;
      b.onclick = function () {
        state.route = r.id; state.location = null;
        mapFitted = false;      /* 새 항로에 맞춰 화면을 다시 잡는다 */
        renderAll();
      };
      box.appendChild(b);
    });
    $("appTitle").textContent = routeObj().name;
  }

  /* 방향 단추. 가는 길과 오는 길은 파도를 맞는 쪽이 정반대라
     판정도 소요 시간도 달라진다. */
  function renderWayChips() {
    var box = $("wayChips");
    if (!box) return;
    box.innerHTML = "";
    var route = routeObj();
    var main = route.main || route.locations || [];
    var startName = main.length && META.locations[main[0]]
      ? tailName(main[0]) : "출발지";
    var endName = (route.short || "도착지");

    [["out", startName + " → " + endName],
     ["back", endName + " → " + startName]].forEach(function (p) {
      var b = el("button", "chip way" + (state.way === p[0] ? " on" : ""), p[1]);
      b.type = "button";
      b.onclick = function () {
        state.way = p[0];
        mapFitted = false;
        renderAll();
      };
      box.appendChild(b);
    });
  }

  function renderTimeBar() {
    var r = $("timeRange");
    r.max = String(FC.times.length - 1);
    r.value = String(state.timeIndex);
    $("timeLabel").textContent =
      fmtDay(FC.times[state.timeIndex]) + " " + FC.times[state.timeIndex].slice(11, 16)
      + (state.timeIndex === nowIndex() ? " · 지금" : "");
  }

  function renderSummary() {
    var route = routeObj(), idx = state.timeIndex;
    var counts = { n: 0, c: 0, u: 0, x: 0 };
    var worst = null, worstRank = -1;
    var rank = { n: 0, c: 1, u: 2, x: -1 };

    var list = $("locList");
    list.innerHTML = "";

    route.locations.forEach(function (locId, i) {
      var s = series(locId), loc = META.locations[locId];
      var st = stOf(locId, idx);
      counts[st]++;
      if (rank[st] > worstRank) { worstRank = rank[st]; worst = loc.name; }

      var card = el("button", "card b-" + st);
      card.type = "button";

      /* 1줄: 번호 · 이름 · 특보 · 판정
         2줄: 풍속 · 돌풍 · 파고 · 풍향
         폰에서 카드 하나가 딱 두 줄에 들어가게 맞춘 구조다. */
      var top = el("div", "card-top");
      top.appendChild(el("div", "card-num", String(i + 1)));
      top.appendChild(el("div", "card-name", loc.name));

      var ws = warningsAt(locId, idx);
      if (ws.length) {
        // 가장 센 특보 하나만 짧게 보여 주고, 더 있으면 +N 으로 붙인다.
        var worstW = ws[0];
        for (var wi = 0; wi < ws.length; wi++) {
          if (ws[wi].lvl === "경보") { worstW = ws[wi]; break; }
        }
        var wtext = worstW.wrn + worstW.lvl;
        if (ws.length > 1) wtext += "+" + (ws.length - 1);
        top.appendChild(el("div",
          "card-warn " + (worstW.lvl === "경보" ? "s-u" : "s-c"), wtext));
      }

      /* 부두면 짐을 싣고 내릴 수 있는지 따로 붙인다.
         고현항은 하역, 도착지는 선적이라 묶어서 '선하역' 이라 부른다.
         색(운항 판단)과 섞지 않는다. 색은 "거기까지 갈 수 있나" 이고
         이건 "가서 짐을 싣고 내릴 수 있나" 라 다른 이야기다. */
      var bth = berthOf(locId, idx);
      if (bth && bth !== "n") {
        top.appendChild(el("div", "card-berth s-" + bth,
          bth === "u" ? "선하역불가" : "선하역주의"));
      }

      top.appendChild(el("div", "card-badge s-" + st, META.status_labels[st]));
      card.appendChild(top);

      var vals = el("div", "card-vals");
      [["wind", "풍속"], ["gust", "돌풍"], ["wave", "파고"]].forEach(function (p) {
        var v = s ? s[p[0]][idx] : null;
        var span = el("span");
        span.appendChild(document.createTextNode(p[1] + " "));
        span.appendChild(el("b", null, v === null ? "—" : num(v)));
        span.appendChild(document.createTextNode(META.units[p[0]] || ""));
        vals.appendChild(span);
      });
      if (s && s.wdir[idx] !== null) {
        var d = el("span");
        d.appendChild(document.createTextNode("풍향 "));
        d.appendChild(el("b", null, compass(s.wdir[idx])));
        vals.appendChild(d);
      }
      card.appendChild(vals);

      card.onclick = function () { state.location = locId; show("detail"); };
      list.appendChild(card);
    });

    var banner = $("summaryBanner");
    banner.innerHTML = "";
    /* 기준 시각은 아래 시간 막대에 이미 크게 나오므로 여기서는 뺀다.
       (두 군데 있으면 가운데 화면만 좁아진다) */
    [["u", "불가"], ["c", "조건"], ["n", "가능"], ["x", "자료없음"]].forEach(function (p) {
      if (!counts[p[0]]) return;
      banner.appendChild(el("div", "pill s-" + p[0], p[1] + " " + counts[p[0]] + "곳"));
    });

    /* 신호등 줄.
       "불가 1곳 조건 3곳" 같은 숫자만으로는 '어디가' 나쁜지 알 수 없다.
       항해 순서대로 칸을 늘어놓아 몇 번째 지점이 막히는지 바로 보이게 한다.
       칸 번호는 카드 목록·지도의 번호와 같고, 누르면 그 지점으로 들어간다. */
    var signals = el("div", "signals");
    route.locations.forEach(function (locId, i) {
      var st = stOf(locId, idx);
      var b = el("button", "signal s-" + st, String(i + 1));
      b.type = "button";
      b.title = META.locations[locId].name + " — " + META.status_labels[st];
      b.onclick = function () { state.location = locId; show("detail"); };
      signals.appendChild(b);
    });
    banner.appendChild(signals);

    if (worstRank >= 1 && worst) {
      banner.appendChild(el("div", "lead", "가장 나쁜 곳: " + worst));
    }

    var note = $("stalenessNote");
    note.textContent = "예보 수집 " + fmtTime(META.forecast_collected_at)
      + " · 특보 수집 " + fmtTime(META.warning_collected_at)
      + " · 자료 생성 " + fmtTime(META.generated_at);

    /* 출처 표기. ECMWF·MET Norway 가 CC BY 4.0 이라 표기가 의무다. */
    var src = $("sourceNote");
    if (src) {
      src.textContent = "자료 출처: ECMWF (CC BY 4.0) · NOAA GFS · "
        + "MET Norway (CC BY 4.0) · 기상청 API 허브 · 지도 OpenStreetMap"
        + " / 원자료를 가공해 운항 판단을 계산한 화면입니다";
    }
  }

  function renderMetricChips(boxId, current, onPick) {
    var box = $(boxId);
    box.innerHTML = "";
    META.metrics.forEach(function (m) {
      var b = el("button", "chip" + (m.key === current ? " on" : ""), m.label);
      b.type = "button";
      b.onclick = function () { onPick(m.key); };
      box.appendChild(b);
    });
  }

  /* ======================================================================
     소요 시간(ETA) 계산

     "지금 이 지점에 있는 배가 목적지까지 몇 시간 걸릴까" 를 낸다.

     핵심은 '시간을 흘려보내며' 계산한다는 점이다.
     배가 3번 지점을 지날 때는 출발로부터 몇 시간 뒤이므로 그 시각의 예보를
     봐야 한다. 지금 기상만 보고 끝까지 계산하지 않는다.

     한 시간씩 전진시키면서 그 구간의 운항 판단을 보고
       가능(초록)   -> 기준 속력 그대로
       조건부(노랑) -> 기준 속력 x caution_factor
       불가(빨강)   -> 그 자리에서 대기 (설정에 따라 통과도 가능)
     ====================================================================== */

  var TIME_MS = null;          /* FC.times 를 밀리초로 바꿔 둔 것 (계산용) */

  function timeMs() {
    if (!TIME_MS) {
      TIME_MS = FC.times.map(function (t) {
        return new Date(t + ":00").getTime();
      });
    }
    return TIME_MS;
  }

  /* 출발 시각에서 h 시간 뒤에 해당하는 예보 칸 번호.
     예보가 3~6시간 간격이라 딱 맞는 칸이 없으므로 그 시각 이하 중 가장 가까운 칸. */
  function slotAfter(startIdx, h) {
    var ms = timeMs();
    var target = ms[startIdx] + h * 3600000;
    var best = startIdx;
    for (var i = startIdx; i < ms.length; i++) {
      if (ms[i] <= target) best = i; else break;
    }
    return best;
  }

  var RANK = { x: -1, n: 0, c: 1, u: 2 };

  /* 한 구간의 상태. 양 끝 지점 중 나쁜 쪽을 쓴다(보수적).
     길 꺾는 점은 기상이 없으므로 값이 있는 쪽만 본다. */
  function legStatus(leg, idx) {
    var worst = null;
    [leg.from, leg.to].forEach(function (id) {
      if (!series(id)) return;
      var st = stOf(id, idx);          /* 고른 방향의 판정을 쓴다 */
      if (!st || st === "x") return;
      if (worst === null || RANK[st] > RANK[worst]) worst = st;
    });
    return worst || "n";     /* 양쪽 다 자료가 없으면 막지 않는다 */
  }

  /* 구간들을 순서대로 항해했을 때 걸리는 시간.
     돌려주는 값: {hours, wait, beyond} 또는 못 가면 null
       hours  : 총 소요 시간
       wait   : 그중 기상 때문에 멈춰 있던 시간
       beyond : 예보 기간을 넘어선 채로 계산했는지 */
  /* 그 구간에서 파도 방향 때문에 속도가 얼마나 달라지는지. */
  function dirSpeedFactor(leg, idx) {
    var wd = (META.wave_direction || {});
    var ks = wd.speed_k;
    if (!ks) return 1;

    /* 구간의 두 끝 중 기상 자료가 있는 쪽을 쓴다.
       (길 꺾는 점은 기상을 안 받는다) */
    var lid = series(leg.to) ? leg.to : (series(leg.from) ? leg.from : null);
    if (!lid) return 1;
    var s = series(lid);
    var hs = (s.wave && s.wave[idx] !== null && s.wave[idx] !== undefined)
      ? s.wave[idx] : 0;
    if (!hs) return 1;

    var k = ks[waveSideAt(lid, idx)];
    if (k === undefined) return 1;
    var f = 1 - k * (hs / 2.0);
    /* 터무니없는 값이 되지 않게 막아 둔다. */
    return Math.max(0.5, Math.min(1.25, f));
  }

  function runLegs(legs, startIdx, speedKn) {
    var v = META.voyage || {};
    var cautionF = (v.caution_factor === undefined) ? 0.75 : v.caution_factor;
    var waitMode = v.wait_when_unavailable !== false;
    var maxWait = v.max_wait_hours || 72;

    var hours = 0, wait = 0, beyond = false;
    var lastIdx = FC.times.length - 1;

    for (var i = 0; i < legs.length; i++) {
      var left = legs[i].nm;
      var guard = 0;
      while (left > 0.0001) {
        var idx = slotAfter(startIdx, hours);
        if (idx >= lastIdx) beyond = true;
        var st = legStatus(legs[i], idx);
        var f = (st === "n") ? 1 : ((st === "c") ? cautionF : 0);

        if (f <= 0) {
          if (waitMode) {
            hours += 1; wait += 1;
            if (wait > maxWait) return null;   /* 예보 기간 안에는 못 간다 */
            continue;
          }
          f = cautionF;      /* 기상을 무시하고 통과하는 설정 */
        }
        /* 파도를 어느 쪽에서 맞느냐에 따라 속도가 달라진다.

           ★ 위험한 순서와 느린 순서가 다르다.
             횡파는 롤링이 심해 가장 위험하지만 속도는 별로 안 준다.
             맞파는 가장 느리지만 횡파보다 안전하다.
             그래서 안전 계수(파고 한계)와 속도 계수를 따로 둔다.

           속도계수 = 1 - k x (파고 / 2.0)
             맞파 k=0.10   횡파 k=0.02   등파 k=-0.05(빨라짐)

           한국선급 부가저항표와 풍압저항으로 푼 값에 맞춘 것이다.
           파고 2 m 에서 맞파 -10%, 등파 +5% 가 나온다. */
        left -= speedKn * f * dirSpeedFactor(legs[i], idx);
        hours += 1;
        if (++guard > 1000) return null;
      }
    }
    return { hours: hours, wait: wait, beyond: beyond };
  }

  /* 항로를 본선과 도착지 가지로 나눈다. */
  function routeShape(route) {
    var dests = route.dests || [];
    var trunk = [], branch = [];
    (route.legs || []).forEach(function (l) {
      if (dests.indexOf(l.to) >= 0) branch.push(l); else trunk.push(l);
    });
    var nodes = trunk.length ? [trunk[0].from] : [];
    trunk.forEach(function (l) { nodes.push(l.to); });
    return { trunk: trunk, branch: branch, nodes: nodes };
  }

  /* 이 지점에서 출발하면 어디까지 얼마나 걸리는지.

     ★ 위쪽 방향 단추가 고른 쪽 하나만 계산한다.
       예전에는 앞뒤 양쪽을 다 보여 줬는데 두 가지가 잘못됐다.
         1) 위에서 방향을 골라 놓고 아래서 양쪽을 다 보여 주면
            단추를 왜 눌렀는지 알 수 없다.
         2) 더 나쁜 건, 판정(stOf)과 속도(dirSpeedFactor)가 고른 방향의
            파향을 쓰기 때문에, 반대쪽 줄은 틀린 방향의 파향으로 계산된
            값이었다. 가는 길을 골라 놓고 본 '고현항 向' 숫자는
            돌아가는 배의 숫자가 아니었다.
       한쪽만 내보내면 두 문제가 같이 없어진다. */
  function etaFor(locId, startIdx) {
    var route = routeObj();
    if (!route.legs || !route.legs.length) return null;
    var shape = routeShape(route);
    var vessels = (META.voyage && META.voyage.vessels) || [];
    if (!vessels.length) return null;

    var goingOut = (state.way !== "back");
    var pos = shape.nodes.indexOf(locId);
    var onBranch = (route.dests || []).indexOf(locId) >= 0;
    var targets = [];

    if (onBranch) {
      /* 도착지에 있는 배. 가는 길이라면 여기가 종점이라 갈 곳이 없다. */
      if (!goingOut) {
        var myLeg = null;
        shape.branch.forEach(function (l) { if (l.to === locId) myLeg = l; });
        if (myLeg) {
          var back = [{ from: myLeg.to, to: myLeg.from, nm: myLeg.nm }];
          var upto = shape.trunk.slice().reverse().map(function (l) {
            return { from: l.to, to: l.from, nm: l.nm };
          });
          targets.push({ id: shape.nodes[0], legs: back.concat(upto) });
        }
      }
    } else if (pos >= 0) {
      if (goingOut) {
        /* 앞으로 각 도착지까지 */
        var ahead = shape.trunk.slice(pos);
        shape.branch.forEach(function (b) {
          targets.push({ id: b.to, legs: ahead.concat([b]) });
        });
      } else if (pos > 0) {
        /* 뒤로 출발지까지 */
        targets.push({
          id: shape.nodes[0],
          legs: shape.trunk.slice(0, pos).reverse().map(function (l) {
            return { from: l.to, to: l.from, nm: l.nm };
          })
        });
      }
    }

    var packed = targets.map(function (t) {
      var runs = vessels.map(function (ves) {
        return { vessel: ves, result: runLegs(t.legs, startIdx, ves.speed_kn) };
      });
      var nm = 0;
      t.legs.forEach(function (l) { nm += l.nm; });
      return { id: t.id, nm: nm, runs: runs };
    });
    return { targets: packed, goingOut: goingOut };
  }

  /* 지명에서 뒤쪽 낱말만. "거제 고현항" -> "고현항"
     방향 표시 칸이 좁아서 앞의 행정구역 이름은 뗀다. */
  function tailName(locId) {
    var loc = META.locations[locId];
    var n = loc ? loc.name : locId;
    var parts = String(n).split(" ");
    return parts[parts.length - 1];
  }

  /* 도착 예정 시각을 사람이 읽는 글로. */
  function arriveText(startIdx, hours) {
    var d = new Date(timeMs()[startIdx] + hours * 3600000);
    var days = ["일", "월", "화", "수", "목", "금", "토"];
    return (d.getMonth() + 1) + "/" + d.getDate()
      + "(" + days[d.getDay()] + ") "
      + ("0" + d.getHours()).slice(-2) + "시";
  }

  function renderEta(locId) {
    var box = $("detailEta");
    if (!box) return;
    box.innerHTML = "";
    var eta = etaFor(locId, state.timeIndex);
    if (!eta) { box.hidden = true; return; }
    box.hidden = false;

    /* 방향을 '앞으로/돌아가기' 대신 그쪽 끝 지명으로 적는다.
       뱃사람 말로 "영성 향", "고현항 향" 이 훨씬 바로 읽힌다. */
    var route0 = routeObj();
    var shape0 = routeShape(route0);
    var startId = shape0.nodes.length ? shape0.nodes[0] : null;
    var dirLabel = eta.goingOut
      ? ((route0.short || "도착지") + " 向")
      : ((startId ? tailName(startId) : "출발지") + " 向");

    /* 이 방향에서 더 갈 곳이 없는 지점(가는 길의 도착지, 오는 길의 고현항).
       그냥 숨기면 왜 사라졌는지 알 수 없어서 한 줄을 남긴다. */
    if (!eta.targets.length) {
      box.appendChild(el("div", "eta-title",
        "이 방향(" + dirLabel + ")에서는 여기가 종점입니다"));
      box.appendChild(el("p", "eta-note",
        "위쪽 방향 단추를 반대로 바꾸면 소요 시간이 나옵니다."));
      return;
    }

    box.appendChild(el("div", "eta-title",
      fmtTime(FC.times[state.timeIndex]) + " 에 이 지점에서 출발하면"));

    [[dirLabel, eta.targets]].forEach(function (pair) {
      var row = el("div", "eta-row");
      row.appendChild(el("div", "eta-dir", pair[0]));
      var list = el("div", "eta-targets");

      pair[1].forEach(function (t) {
        var line = el("div", "eta-item");
        var name = META.locations[t.id] ? META.locations[t.id].name : t.id;
        line.appendChild(el("span", "eta-name",
          name + " (" + t.nm.toFixed(0) + "해리)"));

        /* 선종마다 한 줄. 폰 폭에 들어가도록 글을 짧게 쓴다.
           "89시간 (3일 17시간) · 9/10(목) 17시 도착" -> "3일 17h · 9/10(목) 17시" */
        t.runs.forEach(function (r) {
          var sp = el("span", "eta-vessel");
          sp.appendChild(el("span", "eta-ship", r.vessel.short));

          var val = el("span");
          if (!r.result) {
            val.appendChild(el("b", "eta-none", "예보 기간 내 불가"));
          } else {
            var h = r.result.hours;
            var txt = h >= 24
              ? (Math.floor(h / 24) + "일 " + (h % 24) + "시간")
              : (h + "시간");
            val.appendChild(el("b", null, txt));
            val.appendChild(document.createTextNode(
              " · " + arriveText(state.timeIndex, h)));
            if (r.result.wait > 0) {
              val.appendChild(document.createTextNode(" · "));
              val.appendChild(el("b", "eta-wait", "대기 " + r.result.wait + "시간"));
            }
          }
          sp.appendChild(val);
          line.appendChild(sp);
        });
        list.appendChild(line);
      });
      row.appendChild(list);
      box.appendChild(row);
    });

    var speeds = ((META.voyage && META.voyage.vessels) || []).map(function (v) {
      return v.short + " " + v.speed_kn + "노트";
    }).join(" / ");
    box.appendChild(el("p", "eta-note",
      "기상이 좋을 때 " + speeds + " 기준입니다. "
      + "조건부(노랑) 구간은 느려지고, 불가(빨강) 구간은 기상이 나아질 때까지 "
      + "기다리는 것으로 계산합니다. 지나가는 시각의 예보를 그때그때 반영합니다."));
  }

  /* ======================================================================
     지난 기록

     자료는 DB 에 계속 쌓이고 있는데 화면에서 볼 길이 없었다.
     탭을 늘리는 대신 이미 있는 자리에 접어서 넣는다.
       지점 상세 : 그 지점의 과거 실황 + 예보 이력
       특보 탭   : 특보가 언제 떴다 풀렸나
     ====================================================================== */

  function histDot(ch) {
    var e = el("span", "hist-dot s-" + (ch === "-" ? "x" : ch));
    e.textContent = ch === "-" ? "" : "";
    return e;
  }

  function renderDetailHistory(locId) {
    var box = $("detailHistBody");
    var wrap = $("detailHistory");
    if (!box || !wrap) return;
    box.innerHTML = "";

    if (!HIST) { wrap.hidden = true; return; }
    wrap.hidden = false;

    /* ---- 실제로 있었던 날씨 ---- */
    box.appendChild(el("div", "hist-title",
      "실제로 있었던 날씨 (최근 " + HIST.days + "일)"));
    var obs = (HIST.obs || {})[locId] || [];
    if (!obs.length) {
      box.appendChild(el("div", "hist-none",
        "아직 쌓인 기록이 없습니다. 수집이 몇 번 돌면 채워집니다."));
    } else {
      var g = el("div", "hist-obs");
      obs.slice().reverse().forEach(function (r) {
        g.appendChild(el("span", "hist-when", fmtTime(r[0])));
        g.appendChild(el("span", "hist-dot s-" + r[1],
          META.status_labels[r[1]] ? META.status_labels[r[1]].charAt(0) : ""));
        var parts = [];
        if (r[2] !== null) parts.push("풍속 " + r[2]);
        if (r[3] !== null) parts.push("돌풍 " + r[3]);
        if (r[4] !== null) parts.push("파고 " + r[4]);
        g.appendChild(el("span", "hist-val", parts.join(" · ") || "—"));
      });
      box.appendChild(g);
    }

    /* ---- 예보가 어떻게 바뀌었나 ---- */
    var runs = HIST.runs || [];
    var fc = (HIST.fc || {})[locId] || {};
    var keys = Object.keys(fc).sort();
    if (runs.length > 1 && keys.length) {
      box.appendChild(el("div", "hist-title",
        "예보가 어떻게 바뀌었나 (수집 " + runs.length + "번)"));
      var t = el("div", "hist-fc");
      keys.slice(0, 16).forEach(function (k) {
        t.appendChild(el("span", "hist-when", fmtTime(k)));
        var marks = el("span", "hist-marks");
        fc[k].split("").forEach(function (ch) {
          marks.appendChild(el("i", "hist-mark s-" + (ch === "-" ? "x" : ch)));
        });
        t.appendChild(marks);
      });
      box.appendChild(t);
      box.appendChild(el("div", "hist-legend",
        "왼쪽이 오래된 예보, 오른쪽이 가장 최근 예보입니다. "
        + "색이 오른쪽으로 갈수록 나빠지면 상황이 악화되는 중입니다. "
        + "(수집 " + fmtTime(runs[0]) + " ~ " + fmtTime(runs[runs.length - 1]) + ")"));
    }
  }

  function renderWarnHistory() {
    var box = $("warnHistBody");
    var wrap = $("warnHistory");
    if (!box || !wrap) return;
    box.innerHTML = "";

    var rows = (HIST && HIST.warn) || [];
    if (!rows.length) { wrap.hidden = true; return; }
    wrap.hidden = false;

    var g = el("div", "hist-warn");
    rows.slice(0, 60).forEach(function (w) {
      var row = el("div", "hist-warn-row");
      var what = el("span", "hist-warn-what",
        w.reg + " · " + w.wrn + w.lvl + (w.cmd ? " (" + w.cmd + ")" : ""));
      row.appendChild(what);
      row.appendChild(el("span", "hist-warn-when",
        fmtTime(w.from) + (w.to && w.to !== w.from ? " ~ " + fmtTime(w.to) : "")));
      g.appendChild(row);
    });
    box.appendChild(g);
    box.appendChild(el("div", "hist-legend",
      "최근 " + HIST.days + "일간 기상청에서 받은 특보입니다. "
      + "시각은 우리 프로그램이 그 특보를 처음 본 때와 마지막으로 본 때입니다."));
  }

  function renderDetail() {
    var locId = state.location || routeObj().locations[0];
    state.location = locId;
    var loc = META.locations[locId], s = series(locId);
    var m = metricObj(state.metric);

    $("detailName").textContent = loc.name;
    $("detailSub").textContent =
      loc.lat.toFixed(4) + ", " + loc.lon.toFixed(4)
      + (loc.zones.length ? " · 특보구역 " + loc.zones.join(", ") : "");

    renderEta(locId);
    renderDetailHistory(locId);

    renderMetricChips("metricChips", state.metric, function (k) {
      state.metric = k; renderDetail();
    });

    /* 상세 화면 위 상자는 지금 보고 있는 시각 기준으로 보여 준다. */
    var ws = warningsAt(locId, state.timeIndex);
    var wbox = $("detailWarn");
    if (ws.length) {
      wbox.hidden = false;
      wbox.textContent = "발효 중인 특보: " + ws.map(function (w) {
        return w.wrn + " " + w.lvl + " (" + w.reg_ko + ")";
      }).join(" · ");
    } else {
      wbox.hidden = true;
    }

    var list = $("detailList");
    list.innerHTML = "";
    if (!s) { list.appendChild(el("p", "foot-note", "자료가 없습니다.")); return; }

    var nowIdx = nowIndex(), lastDay = "";
    for (var i = 0; i < FC.times.length; i++) {
      var t = FC.times[i], day = t.slice(0, 10);
      if (day !== lastDay) {
        list.appendChild(el("div", "day-sep", fmtDay(t)));
        lastDay = day;
      }
      var row = el("div", "trow" + (i === nowIdx ? " now-row" : ""));
      row.appendChild(el("div", "thour", t.slice(11, 16)));

      var st, main, sub = "", factors = null;
      if (m.kind === "judge") {
        st = stOf(locId, i);
        main = META.status_labels[st];
        /* 판정 근거를 전부 보여 준다.
           예전에는 풍속·파고 둘만 적었는데, 그 둘이 멀쩡한데 '조건' 이
           뜨면 왜 그런지 알 길이 없었다. 판정은 네 항목(풍속·돌풍·파고·시정)과
           기상특보를 함께 보고 내린다. 항목마다 제 상태 색을 입혀서
           어느 것이 걸렸는지 한눈에 보이게 한다. */
        factors = el("span", "tfactors");
        [["wind", "풍속"], ["gust", "돌풍"], ["wave", "파고"], ["vis", "시정"]]
          .forEach(function (p) {
            var k = p[0], v = s[k] ? s[k][i] : null;
            var chip = el("span", "tfac s-" + (v === null ? "x" : metricStatus(k, v)));
            chip.textContent = p[1] + " " + (v === null ? "—" : num(v));
            factors.appendChild(chip);
          });
        var bthRow = berthOf(locId, i);
        if (bthRow && bthRow !== "n") {
          factors.appendChild(el("span", "tfac s-" + bthRow,
            bthRow === "u" ? "선하역불가" : "선하역주의"));
        }
        var wsRow = warningsAt(locId, i);
        if (wsRow.length) {
          var worstW = wsRow[0];
          for (var wi = 0; wi < wsRow.length; wi++) {
            if (wsRow[wi].lvl === "경보") { worstW = wsRow[wi]; break; }
          }
          var wchip = el("span", "tfac s-" + (worstW.lvl === "경보" ? "u" : "c"));
          wchip.textContent = "특보 " + worstW.wrn + worstW.lvl
            + (wsRow.length > 1 ? "+" + (wsRow.length - 1) : "");
          factors.appendChild(wchip);
        }
      } else {
        var v = s[m.key] ? s[m.key][i] : null;
        st = metricStatus(m.key, v);
        main = (v === null ? "데이터 없음" : num(v) + " " + m.unit);
        if (m.dir && s[m.dir] && s[m.dir][i] !== null) {
          sub = compass(s[m.dir][i]) + " " + s[m.dir][i] + "°";
        }
      }
      var dot = el("div", "tdot");
      dot.style.background = "currentColor";
      var wrap = el("div", "tdot s-" + st);
      wrap.style.background = getComputedStyle(document.documentElement)
        .getPropertyValue(st === "n" ? "--ok-ink" : st === "c" ? "--warn-ink"
                          : st === "u" ? "--bad-ink" : "--none-ink").trim();
      row.appendChild(wrap);

      var val = el("div", "tval", main);
      if (factors) val.appendChild(factors);
      else if (sub) val.appendChild(el("span", "tsub", "  " + sub));
      row.appendChild(val);
      row.title = detailText(locId, i);
      list.appendChild(row);
    }
  }

  function renderWarn() {
    renderWarnHistory();
    var route = routeObj();
    var box = $("warnRoute");
    box.innerHTML = "";
    box.appendChild(el("p", "foot-note",
      "출처: 기상청 API 허브 특보현황 · 수집 " + fmtTime(WARN.collected_at)));

    route.locations.forEach(function (locId) {
      var loc = META.locations[locId];
      var ws = warningsAt(locId, state.timeIndex);
      var card = el("div", "wcard");
      card.appendChild(el("h3", null, loc.name));
      if (!loc.zones.length) {
        card.appendChild(el("div", "none", loc.country === "CN"
          ? "중국 관할 해역·항만입니다. 기상청 특보는 이 지점을 다루지 않습니다."
          : "기상청 특보구역이 지정되지 않은 지점입니다. 숫자 값만으로 판단합니다."));
      } else if (!ws.length) {
        card.appendChild(el("div", "none",
          "발효 중인 특보 없음 (구역 " + loc.zones.join(", ") + ")"));
      } else {
        ws.forEach(function (w) {
          var cls = w.lvl === "경보" ? "s-u" : "s-c";
          card.appendChild(el("span", "wtag " + cls,
            w.wrn + " " + w.lvl + " · " + w.reg_ko
            + (w.ed_tm ? " · 해제예고 " + w.ed_tm : "")));
        });
      }
      box.appendChild(card);
    });

    var all = $("warnAll");
    all.innerHTML = "";
    var table = el("table", "wtable");
    var head = el("tr");
    ["구역", "특보", "수준", "명령", "발효", "해제예고"].forEach(function (h) {
      head.appendChild(el("th", null, h));
    });
    table.appendChild(head);
    (WARN.rows || []).forEach(function (w) {
      var tr = el("tr");
      [w.reg_ko, w.wrn, w.lvl, w.cmd,
       w.tm_ef ? w.tm_ef.slice(4, 6) + "/" + w.tm_ef.slice(6, 8) + " "
                 + w.tm_ef.slice(8, 10) + ":" + w.tm_ef.slice(10, 12) : "",
       w.ed_tm].forEach(function (c) { tr.appendChild(el("td", null, c || "")); });
      table.appendChild(tr);
    });
    all.appendChild(table);
  }

  function renderGrid() {
    renderMetricChips("gridChips", state.gridMetric, function (k) {
      state.gridMetric = k; renderGrid();
    });
    var route = routeObj(), m = metricObj(state.gridMetric);
    var step = 3;  // 표는 3시간 간격으로 보여 준다
    var cols = [];
    for (var i = 0; i < FC.times.length; i++) {
      if (Number(FC.times[i].slice(11, 13)) % step === 0) cols.push(i);
    }

    var table = el("table", "grid");
    var thead = el("thead"), hr = el("tr");
    hr.appendChild(el("th", "gloc", "위치"));
    var lastDay = "";
    cols.forEach(function (i) {
      var t = FC.times[i], day = t.slice(5, 10).replace("-", "/");
      var th = el("th");
      /* 시각은 '09:00' 대신 '9시' 로 쓴다. 열이 좁아지고 읽기도 쉽다. */
      var hh = Number(t.slice(11, 13));
      th.innerHTML = (day !== lastDay ? day : "") + "<br>" + hh + "시";
      lastDay = day;
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    route.locations.forEach(function (locId, r) {
      var s = series(locId), loc = META.locations[locId];
      var tr = el("tr");
      tr.appendChild(el("td", "gloc", (r + 1) + ". " + loc.name));
      cols.forEach(function (i) {
        var st, text;
        if (m.kind === "judge") {
          st = stOf(locId, i);
          text = META.status_labels[st];
        } else {
          var v = s && s[m.key] ? s[m.key][i] : null;
          st = metricStatus(m.key, v);
          text = v === null ? "—" : num(v);
          if (m.dir && s && s[m.dir] && s[m.dir][i] !== null) {
            text += " " + compass(s[m.dir][i]);
          }
        }
        var td = el("td", "s-" + st, text);
        td.title = detailText(locId, i);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    var wrap = $("gridWrap");
    wrap.innerHTML = "";
    wrap.appendChild(table);

    renderGridLegend();
  }

  /* 표 아래에 색이 무슨 뜻인지, 어떤 숫자로 갈리는지 적어 준다.
     한계값은 meta.json 에서 그대로 가져오므로 설정을 고치면 같이 바뀐다. */
  function renderGridLegend() {
    var box = $("gridLegend");
    if (!box) return;
    box.innerHTML = "";

    var row = el("div", "legend-colors");
    [["n", "가능"], ["c", "조건부"], ["u", "불가"], ["x", "자료 없음"]]
      .forEach(function (p) {
        row.appendChild(el("span", "legend-chip s-" + p[0], p[1]));
      });
    box.appendChild(row);

    /* 항목마다 한 줄씩. 예전에는 넷을 '/' 로 이어 붙여 한 문단으로
       넣었더니 폰에서 아무 데서나 줄이 끊겨 읽기 어려웠다. */
    box.appendChild(el("div", "legend-head", "운항 판단 기준"));

    var order = ["wind", "gust", "wave", "vis"];
    var table = el("div", "legend-rules");
    order.forEach(function (k) {
      var t = META.thresholds[k];
      if (!t || t.auto === false) return;
      var unit = META.units[k] || "";
      var parts = [];
      if (t.caution_at !== null && t.caution_at !== undefined) {
        parts.push(["c", "조건부 " + t.caution_at + unit + " 이상"]);
      }
      if (t.unavailable_at !== null && t.unavailable_at !== undefined) {
        parts.push(["u", "불가 " + t.unavailable_at + unit + " 이상"]);
      }
      if (t.caution_below !== null && t.caution_below !== undefined) {
        parts.push(["c", "조건부 " + t.caution_below + unit + " 미만"]);
      }
      if (t.unavailable_below !== null && t.unavailable_below !== undefined) {
        parts.push(["u", "불가 " + t.unavailable_below + unit + " 미만"]);
      }
      if (!parts.length) return;

      table.appendChild(el("div", "legend-key", META.labels[k] || k));
      var val = el("div", "legend-val");
      parts.forEach(function (p, i) {
        if (i) val.appendChild(document.createTextNode(" · "));
        val.appendChild(el("span", "legend-" + p[0], p[1]));
      });
      table.appendChild(val);
    });
    box.appendChild(table);

    /* 파향 계수는 짧게 한 줄만. 파고 기준이 방향에 따라 달라지는 것을
       모르면 "왜 같은 파고인데 색이 다르지" 하고 헷갈린다. */
    var wd = (META.wave_direction || {}).safety_factor;
    var wave = META.thresholds.wave;
    if (wd && wave && wave.unavailable_at) {
      var u = wave.unavailable_at;
      box.appendChild(el("p", "legend-note",
        "파고 기준은 파도를 맞는 쪽에 따라 달라집니다 — "
        + "맞파 " + (u * (wd.head || 1)).toFixed(2) + "m · "
        + "옆파 " + (u * (wd.beam || 1)).toFixed(2) + "m · "
        + "등파 " + (u * (wd.following || 1)).toFixed(2) + "m 에서 불가. "
        + "옆에서 맞으면 흔들림이 커 가장 엄격합니다."));
    }

    /* 선하역은 색과 뜻이 달라서 따로 설명한다. 안 그러면 초록인데
       '선하역불가' 가 붙은 걸 보고 헷갈린다. */
    var brs = berthRules();
    if (brs.length) {
      /* 보는 값이 여럿이면 "평균 풍속·순간 풍속" 처럼 이어 붙인다.
         기준값이 다 같으면 한 번만 적어 글이 짧아진다. */
      var names = brs.map(function (r) { return META.labels[r.key] || r.key; });
      var same = brs.every(function (r) {
        return r.lim.caution_at === brs[0].lim.caution_at
            && r.lim.unavailable_at === brs[0].lim.unavailable_at;
      });
      var txt = "선하역(짐 싣고 내리기)은 따로 봅니다 — ";
      if (same) {
        txt += names.join("·") + " 어느 쪽이든 "
          + brs[0].lim.caution_at + "m/s 이상 주의 · "
          + brs[0].lim.unavailable_at + "m/s 이상 불가";
      } else {
        txt += brs.map(function (r, i) {
          return names[i] + " " + r.lim.caution_at + "/"
            + r.lim.unavailable_at + (META.units[r.key] || "");
        }).join(" · ");
      }
      txt += ". 고현항·영성법인·영성가야·CSME 에만 표시하며, "
        + "색(운항 판단)과는 별개입니다. 갈 수는 있어도 "
        + "짐을 못 싣는 때가 있습니다.";
      box.appendChild(el("p", "legend-note", txt));
    }
  }

  // ---------------------------------------------------------------- 지도
  var STATUS_HEX = { n: "#1a7f37", c: "#b58100", u: "#c62828", x: "#9e9e9e" };
  /* 면을 칠할 때 쓰는 색. 점(동그라미)보다 연하게 해서 그 위의 항로와
     지점 표시가 묻히지 않게 한다. */
  var AREA_HEX = { n: "#2fa84f", c: "#e8b53a", u: "#e05545", x: "#9e9e9e" };
  /* 지점 동그라미 반지름(픽셀). 풍향 쐐기가 이 밖에서 시작한다. */
  var DOT_R = 7;
  /* 쐐기 밑변을 동그라미 지름의 몇 배로 할지.
     1.0 이면 지름과 같고, 그건 너무 뭉툭했다. */
  var WEDGE_W = 2 / 3;

  /* 격자에서 '지금 지도에 보이는 시각'에 해당하는 칸 번호를 찾는다.
     격자는 3시간 간격이고 지점 예보는 1시간 간격이라 칸 수가 다르다.
     그래서 시각 글자를 직접 맞춰 본다. */
  function gridSlot() {
    if (!GRID || !GRID.times.length) return -1;
    var want = FC.times[state.timeIndex];
    var best = -1;
    for (var i = 0; i < GRID.times.length; i++) {
      if (GRID.times[i] <= want) best = i; else break;
    }
    return best;
  }

  /* 격자 칸을 사각형으로 그린다.
     사각형은 한 번만 만들고, 시각이 바뀌면 색만 갈아 끼운다.
     매번 다시 만들면 칸이 900개쯤 되어 눈에 띄게 느려진다.

     grid.json 의 각 칸은 {lat, lon, st, wind, gust, wave} 객체다.
     st 는 이 칸의 시각별 판정을 한 글자씩 이어붙인 문자열이고
     (n=가능 c=조건 u=불가 x=데이터없음), GRID.times 와 같은 순서다. */
  function buildGridLayer() {
    if (!GRID || gridLayer) return;
    var half = GRID.cell_deg / 2;
    /* 칸이 900개가 넘는다. SVG 로 그리면 폰에서 버벅인다.
       캔버스 렌더러를 쓰면 수천 개도 부드럽다. */
    var canvas = L.canvas({ padding: 0.3 });
    gridLayer = L.layerGroup();
    gridCells = [];
    GRID.cells.forEach(function (c) {
      var rect = L.rectangle(
        [[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]],
        { stroke: false, fillOpacity: 0.4, fillColor: "#9e9e9e",
          interactive: false, renderer: canvas }
      );
      gridLayer.addLayer(rect);
      gridCells.push({ rect: rect, cell: c });
    });
  }

  function paintGrid() {
    if (!GRID || !gridLayer) return;
    var slot = gridSlot();
    gridCells.forEach(function (g) {
      var ch = (slot >= 0 && slot < g.cell.st.length) ? g.cell.st[slot] : "x";
      g.rect.setStyle({ fillColor: AREA_HEX[ch] || AREA_HEX.x });
    });
  }

  /* 지도 그리기는 바깥 껍데기와 알맹이로 나눠 둔다.
     알맹이에서 오류가 나면 지도 자리에 이유를 적어 준다.
     안 그러면 지도가 그냥 비어 보여서 무엇이 잘못됐는지 알 수가 없다. */
  function renderMap() {
    try {
      mapDrawing = true;
      renderMapInner();
    } catch (err) {
      var box = $("map");
      if (box) {
        box.innerHTML = "";
        var p = el("p", "foot-note err");
        p.style.padding = "16px";
        p.textContent = "지도를 그리는 중 문제가 생겼습니다: " + (err && err.message)
          + " — 목록·표·특보 탭은 정상 동작합니다.";
        box.appendChild(p);
      }
      if (window.console) console.error("renderMap 실패", err);
    } finally {
      mapDrawing = false;
    }
  }

  function renderMapInner() {
    /* 지도 칸이 아직 화면에 안 나타났으면(다른 탭에 있었거나 방금 전환)
       크기가 0x0 이다. 그 상태로 그리면 배율 계산이 망가진다.
       크기가 잡힐 때까지 잠깐 기다렸다 다시 시도한다. */
    var box0 = $("map");
    if (box0 && (!box0.offsetWidth || !box0.offsetHeight)) {
      /* 혹시 끝내 크기가 안 잡히더라도 무한히 되풀이하지 않게 횟수를 센다. */
      if (mapWaits < 12) {
        mapWaits += 1;
        setTimeout(function () { if (state.view === "map") renderMap(); }, 80);
      }
      return;
    }
    mapWaits = 0;

    if (typeof L === "undefined") {
      $("map").innerHTML =
        '<p class="foot-note err" style="padding:16px">'
        + '지도를 불러오지 못했습니다. 인터넷 연결을 확인하세요.'
        + ' 지도가 없어도 목록과 표는 정상 동작합니다.</p>';
      return;
    }
    if (!map) {
      map = L.map("map", { scrollWheelZoom: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18, attribution: "&copy; OpenStreetMap 기여자"
      }).addTo(map);
      /* 도형을 얹기 전에 화면 범위를 먼저 정해야 한다.
         범위가 없는 지도에 도형을 넣으면 Leaflet 이
         "Cannot read properties of undefined (reading 'min')" 로 죽는다.
         아래에서 fitBounds 로 항로에 맞게 다시 잡는다. */
      map.setView([35.0, 126.0], 6);
      /* 배율이 바뀌면 풍향 막대를 다시 그린다.
         막대를 픽셀로 계산하므로 배율이 달라지면 좌표가 어긋난다. */
      /* 배율이 바뀌면 풍향 막대를 다시 그린다(막대를 픽셀로 계산하므로).
         단, 그리는 중에 일어난 배율 변경은 무시한다. 안 그러면
         그리기 -> 배율변경 -> 다시 그리기 가 서로 물려 값이 튄다. */
      map.on("zoomend", function () {
        if (state.view === "map" && !mapDrawing) renderMap();
      });
    }
    mapLayers.forEach(function (l) { map.removeLayer(l); });
    mapLayers = [];

    // 격자를 맨 아래에 깔고, 그 위에 항로와 지점을 그린다.
    buildGridLayer();
    if (gridLayer) {
      if (gridOn && !map.hasLayer(gridLayer)) gridLayer.addTo(map);
      if (!gridOn && map.hasLayer(gridLayer)) map.removeLayer(gridLayer);
      paintGrid();
    }

    var route = routeObj(), idx = state.timeIndex;
    var LINE = { color: "#3f6fd0", weight: 2, opacity: .85, dashArray: "6 5" };
    var at = function (locId) {
      var loc = META.locations[locId];
      return [loc.lat, loc.lon];
    };

    /* 본선은 한 줄로 잇고, 도착지는 본선 마지막 지점에서 따로 뻗는다.
       (영성 묘박지에서 영성법인·영성가야로 갈라지는 모양)
       전부 한 줄로 이으면 영성법인 → 영성가야 사이에 없는 항로가 그려진다. */
    var main = (route.main && route.main.length) ? route.main : route.locations;
    var dests = route.dests || [];
    /* 선은 route.path 로 그린다. 길을 꺾는 점까지 들어 있어서
       고현항에서 거제도를 돌아 나가는 실제 항로대로 그려진다.
       (지점만 이으면 거제도 육지를 뚫는 직선이 된다) */
    var line = (route.path && route.path.length) ? route.path : main.map(at);
    mapLayers.push(L.polyline(line, LINE).addTo(map));

    var branchAt = main.length ? at(main[main.length - 1]) : null;
    if (branchAt) {
      dests.forEach(function (d) {
        mapLayers.push(L.polyline([branchAt, at(d)], LINE).addTo(map));
      });
    }
    // 지도 범위는 모든 지점을 담도록 잡는다.
    var allPts = route.locations.map(at);

    route.locations.forEach(function (locId, i) {
      var loc = META.locations[locId], s = series(locId);
      var st = stOf(locId, idx);
      var color = STATUS_HEX[st];
      var label, m = metricObj(state.metric);
      if (m.kind === "judge" || !s) {
        label = META.status_labels[st];
      } else {
        var v = s[m.key] ? s[m.key][idx] : null;
        label = v === null ? "—" : num(v) + " " + m.unit;
      }

      /* 바람을 쐐기(뾰족한 삼각형)로 그린다.

         ★ 방향을 헷갈리지 않게 하려고 쐐기로 만들었다.
           동그라미 쪽이 넓고(지름과 같은 너비) 반대쪽으로 갈수록 좁아져
           뾰족해진다. 그 뾰족한 끝이 '바람이 불어 가는 쪽' 이다.
           그냥 막대면 어느 쪽으로 부는지 알 수가 없다.

         ※ 표와 카드에 적히는 풍향 글자(북·남동 …)는 기상 관례대로
           '불어오는 방향' 이다. 지도의 쐐기와는 정반대를 가리킨다.
           그래서 아래 +180 으로 뒤집는다.

         길이를 위도·경도(도)로 잡으면 지도를 축소했을 때 쐐기가 짧아져
         동그라미에 묻힌다. 그래서 화면 픽셀로 계산한다. */
      if (s && s.wdir[idx] !== null && s.wind[idx]) {
        var rad = (s.wdir[idx] + 180) * Math.PI / 180;   /* 불어 가는 쪽으로 뒤집기 */
        var dx = Math.sin(rad), dy = -Math.cos(rad);     /* 화면 좌표: y 는 아래가 + */
        var nx = -dy, ny = dx;                           /* 쐐기 밑변 방향(직각) */
        var gap = DOT_R;                                 /* 동그라미 가장자리에서 시작 */
        var len = 16 + Math.min(s.wind[idx], 20) * 1.6;  /* 바람이 셀수록 길게 */
        var c = map.latLngToLayerPoint([loc.lat, loc.lon]);

        var bx = c.x + dx * gap, by = c.y + dy * gap;            /* 밑변 가운데 */
        var tx = c.x + dx * (gap + len), ty = c.y + dy * (gap + len);  /* 뾰족한 끝 */
        var half = DOT_R * WEDGE_W;                              /* 밑변 절반 */

        var arrow = L.polygon([
          map.layerPointToLatLng(L.point(bx + nx * half, by + ny * half)),
          map.layerPointToLatLng(L.point(bx - nx * half, by - ny * half)),
          map.layerPointToLatLng(L.point(tx, ty))
        ], { color: color, weight: 1, opacity: .95,
             fillColor: color, fillOpacity: .85, interactive: false }).addTo(map);
        mapLayers.push(arrow);
      }

      var pop = popupNode(locId, idx);

      var up = (i % 2 === 0);
      var mk = L.circleMarker([loc.lat, loc.lon], {
        radius: DOT_R, color: "#ffffff", weight: 2,
        fillColor: color, fillOpacity: 1
      }).addTo(map)
        .bindTooltip((i + 1) + " " + label, {
          permanent: true, direction: up ? "top" : "bottom",
          offset: up ? [0, -9] : [0, 9], className: "wx-pin", opacity: 1
        })
        .bindPopup(pop, { maxWidth: 300, minWidth: 180, autoPanPadding: [12, 12] });
      mapLayers.push(mk);
    });

    /* 화면 맞추기는 항로를 처음 그릴 때만 한다.
       배율을 바꿀 때마다 다시 맞추면 사용자가 확대한 것이 도로 풀린다. */
    function fitAll() {
      if (allPts.length === 1) map.setView(allPts[0], 9);
      else map.fitBounds(L.latLngBounds(allPts), { padding: [40, 40] });
    }

    if (!mapFitted) {
      fitAll();
      mapFitted = true;
    }

    /* 크기를 다시 재고, 그 뒤에 배율이 멀쩡한지 확인한다.
       다른 탭에 있는 동안 지도 칸이 0x0 이었다면 배율이 엉뚱한 값으로
       남아 있을 수 있다(실제로 '말도 안 되게 확대' 되는 일이 있었다). */
    setTimeout(function () {
      if (!map) return;
      mapDrawing = true;
      map.invalidateSize();
      var z = map.getZoom();
      if (!isFinite(z) || z < MAP_ZOOM_MIN || z > MAP_ZOOM_MAX) {
        fitAll();
      }
      mapDrawing = false;
    }, 60);
  }

  // ---------------------------------------------------------------- 화면 전환
  function show(view) {
    state.view = view;
    ["summary", "detail", "map", "warn", "grid"].forEach(function (v) {
      var node = $("view" + v.charAt(0).toUpperCase() + v.slice(1));
      if (node) node.hidden = (v !== view);
    });
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (b) {
      b.classList.toggle("on", b.dataset.view === view
        || (view === "detail" && b.dataset.view === "summary"));
    });
    if (view === "map") renderMap();
    if (view === "detail") renderDetail();
    if (view === "grid") renderGrid();
    if (view === "warn") renderWarn();
    window.scrollTo(0, 0);
  }

  /* 위쪽 고정 막대의 실제 높이를 재서 CSS 에 넣는다.
     높이를 CSS 에 숫자로 박아 두면 항로 이름 길이나 글꼴 크기에 따라
     내용 첫 줄이 막대에 가려진다. 그래서 잰 값을 쓴다. */
  function syncTopHeight() {
    var h = document.querySelector(".top").offsetHeight;
    document.documentElement.style.setProperty("--top-h", h + "px");
    /* 아래 시간 막대 높이도 실제로 재서 넣는다.
       글꼴 크기나 화면 폭에 따라 달라지므로 숫자를 박아 두면 어긋난다. */
    var tb = document.getElementById("timebar");
    if (tb) {
      var th = tb.offsetHeight;
      document.documentElement.style.setProperty(
        "--timebar-h", (th > 0 ? th : 56) + "px");
    }
  }

  function updateGridNote() {
    var note = $("gridNote");
    if (!note) return;
    var base = "선은 항해 순서입니다. 지점마다 붙은 쐐기는 바람인데, "
             + "넓은 쪽이 지점이고 뾰족한 끝이 바람이 불어 가는 쪽입니다. "
             + "길수록 센 바람입니다. "
             + "(표와 카드에 적힌 풍향 글자는 기상 관례대로 '불어오는 방향' 이라 "
             + "쐐기와는 정반대를 가리킵니다.) "
             + "동그라미를 누르면 판정 근거가 나옵니다. 지도는 OpenStreetMap 입니다.";
    if (GRID) {
      base += " 바다에 칠한 색은 약 " + Math.round(GRID.step_deg * 111) + " km 격자마다 "
            + "운항 판단입니다. " + GRID.note;
    } else {
      base += " (해역 색칠 자료가 아직 없습니다. 1_수집하기.bat 을 실행하세요.)";
      var btn = $("gridToggle");
      if (btn) { btn.disabled = true; btn.textContent = "해역 색칠 없음"; }
    }
    note.textContent = base;
  }

  function renderAll() {
    renderChips();
    renderWayChips();
    renderTimeBar();
    renderSummary();   /* 신호등은 머리말에 있어 어느 탭에서나 보인다 */
    updateGridNote();
    syncTopHeight();
    if (state.view === "map") renderMap();
    if (state.view === "detail") renderDetail();
    if (state.view === "grid") renderGrid();
    if (state.view === "warn") renderWarn();
  }

  // ---------------------------------------------------------------- 시작
  /* 시각이 바뀌면 다시 그려야 하는 것들.
     네 군데(막대 끌기·‹·›·지금)에서 똑같이 불러야 하는데 예전에는
     각자 조금씩 다르게 적어 둬서, 상세 화면의 소요 시간이 시각을 옮겨도
     그대로였다. 한 곳으로 모은다.

     표(格子)는 여기 없다. 표는 시각 하나가 아니라 전체 기간을
     한꺼번에 보여 주므로 다시 그릴 것이 없다. */
  function refreshForTime() {
    renderTimeBar();
    renderSummary();                                   /* 신호등 */
    if (state.view === "map") renderMap();
    if (state.view === "detail") renderDetail();       /* 소요 시간 포함 */
  }

  function wire() {
    $("timeRange").addEventListener("input", function (e) {
      state.timeIndex = Number(e.target.value);
      refreshForTime();
    });
    $("timePrev").onclick = function () {
      state.timeIndex = Math.max(0, state.timeIndex - 1);
      refreshForTime();
    };
    $("timeNext").onclick = function () {
      state.timeIndex = Math.min(FC.times.length - 1, state.timeIndex + 1);
      refreshForTime();
    };
    $("timeNow").onclick = function () {
      state.timeIndex = nowIndex();
      refreshForTime();
    };
    $("detailBack").onclick = function () { show("summary"); };
    $("gridToggle").onclick = function () {
      gridOn = !gridOn;
      this.textContent = gridOn ? "해역 색칠 켜짐" : "해역 색칠 꺼짐";
      this.classList.toggle("on", gridOn);
      if (map && gridLayer) {
        if (gridOn) { gridLayer.addTo(map); paintGrid(); }
        else map.removeLayer(gridLayer);
      }
    };
    $("qrBtn").onclick = function () { showQr(true); };
    $("qrClose").onclick = function () { showQr(false); };
    $("qrBack").onclick = function () { showQr(false); };
    $("qrCopy").onclick = copyUrl;
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") showQr(false);
    });
    $("themeBtn").onclick = function () {
      applyTheme(currentTheme() === "dark" ? "light" : "dark");
    };
    $("reloadBtn").onclick = function () {
      $("loading").classList.remove("done");
      $("loading").textContent = "불러오는 중…";
      load().then(function () { renderAll(); $("loading").classList.add("done"); });
    };
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (b) {
      b.onclick = function () { show(b.dataset.view); };
    });
    window.addEventListener("resize", syncTopHeight);
    window.addEventListener("orientationchange", function () {
      setTimeout(syncTopHeight, 200);
    });
  }

  load().then(function () {
    wire();
    applyTheme(currentTheme());   /* 단추 아이콘을 지금 상태에 맞춘다 */
    renderAll();
    show("summary");
    $("loading").classList.add("done");
  }).catch(function (err) {
    $("loading").innerHTML =
      '<div style="padding:24px;text-align:center">'
      + '<p class="err">자료를 불러오지 못했습니다.</p>'
      + '<p class="foot-note">1_수집하기.bat 을 실행한 뒤 다시 열어 주세요.<br>'
      + String(err).replace(/</g, "&lt;") + "</p></div>";
  });
})();
