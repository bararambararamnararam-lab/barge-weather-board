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

  var META = null, FC = null, WARN = null, GRID = null;
  var gridLayer = null, gridCells = [], gridOn = true;
  var state = {
    route: null,
    timeIndex: 0,
    view: "summary",
    metric: "judge",
    gridMetric: "judge",
    location: null
  };
  var map = null, mapLayers = [];

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

  /* 어떤 지점·시각의 값들을 사람이 읽을 수 있는 여러 줄 글로 만든다.
     지도 팝업과 상세 화면에서 같이 쓴다. */
  function detailText(locId, idx) {
    var s = series(locId), loc = META.locations[locId];
    if (!s) return loc.name;
    var lines = [];
    lines.push(loc.name + " / " + fmtTime(FC.times[idx]));
    lines.push("판정: " + META.status_labels[s.st[idx]]);
    /* 자료원이 ECMWF+NOAA 로 바뀌면서 뇌우(cape)·해류(cur)·수온(sst)은
       더 이상 받지 않는다. 목록에서 뺀다. */
    var order = ["wind", "gust", "wave", "vis", "prec", "vper"];
    for (var i = 0; i < order.length; i++) {
      var k = order[i], v = s[k] ? s[k][idx] : null;
      if (v === null || v === undefined) {
        lines.push(META.labels[k] + ": 데이터 없음");
        continue;
      }
      var line = META.labels[k] + ": " + v + " " + (META.units[k] || "");
      var t = META.thresholds[k];
      if (t && t.auto !== false) {
        if (t.unavailable_at !== null && t.unavailable_at !== undefined) {
          line += " / 불가 기준 " + t.unavailable_at + " " + META.units[k];
        } else if (t.unavailable_below !== null && t.unavailable_below !== undefined) {
          line += " / 불가 기준 " + t.unavailable_below + " " + META.units[k] + " 미만";
        }
        line += " (" + META.metric_status_labels[metricStatus(k, v)] + ")";
      }
      lines.push(line);
    }
    if (s.wdir && s.wdir[idx] !== null) {
      lines.push("풍향: " + compass(s.wdir[idx]) + " (" + s.wdir[idx] + "°)");
    }
    if (s.code && s.code[idx] !== null) {
      lines.push("날씨: " + (META.wmo[s.code[idx]] || ("코드 " + s.code[idx])));
    }
    var ws = FC.warnings_by_location[locId] || [];
    if (ws.length) {
      lines.push("---- 기상특보 ----");
      ws.forEach(function (w) {
        lines.push(w.wrn + " " + w.lvl + " (" + w.cmd + ") [" + w.reg_ko + "]"
                   + (w.ed_tm ? " / 해제예고 " + w.ed_tm : ""));
      });
    }
    lines.push("데이터 기준: 예보");
    lines.push("예보 수집 시각: " + fmtTime(META.forecast_collected_at) + " KST");
    lines.push("출처: Open-Meteo Forecast + Marine, 기상청 특보현황");
    return lines.join("\n");
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
      // 격자는 없을 수도 있다(설정에서 껐거나 아직 안 받았을 때).
      // 없어도 나머지 화면은 그대로 동작해야 하므로 실패를 조용히 넘긴다.
      return fetch("data/grid.json" + bust)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (g) { GRID = g; gridCells = []; gridLayer = null; })
        .catch(function () { GRID = null; });
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
      b.onclick = function () { state.route = r.id; state.location = null; renderAll(); };
      box.appendChild(b);
    });
    $("appTitle").textContent = routeObj().name;
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
      var st = s ? s.st[idx] : "x";
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

      var ws = FC.warnings_by_location[locId] || [];
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
    banner.appendChild(el("div", "lead",
      fmtDay(FC.times[idx]) + " " + FC.times[idx].slice(11, 16) + " 기준"));
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
      var s = series(locId);
      var st = s ? s.st[idx] : "x";
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
      var ss = series(id);
      if (!ss || !ss.st) return;
      var st = ss.st[idx];
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
        left -= speedKn * f;   /* 한 시간 전진 */
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

  /* 어떤 지점에서 앞(도착지들)과 뒤(출발지)로 각각 얼마나 걸리는지. */
  function etaFor(locId, startIdx) {
    var route = routeObj();
    if (!route.legs || !route.legs.length) return null;
    var shape = routeShape(route);
    var vessels = (META.voyage && META.voyage.vessels) || [];
    if (!vessels.length) return null;

    var pos = shape.nodes.indexOf(locId);
    var onBranch = (route.dests || []).indexOf(locId) >= 0;
    var forward = [], backward = [];

    if (onBranch) {
      /* 도착지에 이미 있는 배. 앞으로 갈 곳은 없고 돌아가는 길만 있다. */
      var myLeg = null;
      shape.branch.forEach(function (l) { if (l.to === locId) myLeg = l; });
      if (myLeg) {
        var back = [{ from: myLeg.to, to: myLeg.from, nm: myLeg.nm }];
        var upto = shape.trunk.slice().reverse().map(function (l) {
          return { from: l.to, to: l.from, nm: l.nm };
        });
        backward.push({ id: shape.nodes[0], legs: back.concat(upto) });
      }
    } else if (pos >= 0) {
      /* 본선 위의 배. 앞으로는 각 도착지까지, 뒤로는 출발지까지. */
      var ahead = shape.trunk.slice(pos);
      shape.branch.forEach(function (b) {
        forward.push({ id: b.to, legs: ahead.concat([b]) });
      });
      if (pos > 0) {
        backward.push({
          id: shape.nodes[0],
          legs: shape.trunk.slice(0, pos).reverse().map(function (l) {
            return { from: l.to, to: l.from, nm: l.nm };
          })
        });
      }
    }

    function pack(list) {
      return list.map(function (t) {
        var runs = vessels.map(function (ves) {
          return { vessel: ves, result: runLegs(t.legs, startIdx, ves.speed_kn) };
        });
        var nm = 0;
        t.legs.forEach(function (l) { nm += l.nm; });
        return { id: t.id, nm: nm, runs: runs };
      });
    }
    return { forward: pack(forward), backward: pack(backward) };
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
    if (!eta || (!eta.forward.length && !eta.backward.length)) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.appendChild(el("div", "eta-title",
      fmtTime(FC.times[state.timeIndex]) + " 에 이 지점에서 출발하면"));

    [["앞으로", eta.forward], ["돌아가기", eta.backward]].forEach(function (pair) {
      if (!pair[1].length) return;
      var row = el("div", "eta-row");
      row.appendChild(el("div", "eta-dir", pair[0]));
      var list = el("div", "eta-targets");

      pair[1].forEach(function (t) {
        var line = el("div", "eta-item");
        var name = META.locations[t.id] ? META.locations[t.id].name : t.id;
        line.appendChild(el("span", "eta-name",
          name + " (" + t.nm.toFixed(0) + "해리)"));

        t.runs.forEach(function (r) {
          var sp = el("span", "eta-vessel");
          sp.appendChild(document.createTextNode(r.vessel.short + " "));
          if (!r.result) {
            sp.appendChild(el("b", "eta-none", "예보 기간 내 불가"));
          } else {
            var h = r.result.hours;
            var txt = h + "시간";
            if (h >= 24) txt += " (" + Math.floor(h / 24) + "일 " + (h % 24) + "시간)";
            sp.appendChild(el("b", null, txt));
            sp.appendChild(document.createTextNode(
              " · " + arriveText(state.timeIndex, h) + " 도착"));
            if (r.result.wait > 0) {
              sp.appendChild(document.createTextNode(" · "));
              sp.appendChild(el("b", "eta-wait", "대기 " + r.result.wait + "시간"));
            }
          }
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

    renderMetricChips("metricChips", state.metric, function (k) {
      state.metric = k; renderDetail();
    });

    var ws = FC.warnings_by_location[locId] || [];
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

      var st, main, sub = "";
      if (m.kind === "judge") {
        st = s.st[i];
        main = META.status_labels[st];
        sub = "풍속 " + num(s.wind[i]) + " · 파고 " + num(s.wave[i]);
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
      if (sub) {
        var small = el("span", "tsub", "  " + sub);
        val.appendChild(small);
      }
      row.appendChild(val);
      row.title = detailText(locId, i);
      list.appendChild(row);
    }
  }

  function renderWarn() {
    var route = routeObj();
    var box = $("warnRoute");
    box.innerHTML = "";
    box.appendChild(el("p", "foot-note",
      "출처: 기상청 API 허브 특보현황 · 수집 " + fmtTime(WARN.collected_at)));

    route.locations.forEach(function (locId) {
      var loc = META.locations[locId];
      var ws = FC.warnings_by_location[locId] || [];
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
      th.innerHTML = (day !== lastDay ? day : "") + "<br>" + t.slice(11, 16);
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
          st = s ? s.st[i] : "x";
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
  }

  // ---------------------------------------------------------------- 지도
  var STATUS_HEX = { n: "#1a7f37", c: "#b58100", u: "#c62828", x: "#9e9e9e" };
  /* 면을 칠할 때 쓰는 색. 점(동그라미)보다 연하게 해서 그 위의 항로와
     지점 표시가 묻히지 않게 한다. */
  var AREA_HEX = { n: "#2fa84f", c: "#e8b53a", u: "#e05545", x: "#9e9e9e" };

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
    }
  }

  function renderMapInner() {
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
      var st = s ? s.st[idx] : "x";
      var color = STATUS_HEX[st];
      var label, m = metricObj(state.metric);
      if (m.kind === "judge" || !s) {
        label = META.status_labels[st];
      } else {
        var v = s[m.key] ? s[m.key][idx] : null;
        label = v === null ? "—" : num(v) + " " + m.unit;
      }

      if (s && s.wdir[idx] !== null && s.wind[idx]) {
        var rad = (s.wdir[idx] + 180) * Math.PI / 180;
        var len = 0.10 + Math.min(s.wind[idx], 20) * 0.012;
        var dLat = Math.cos(rad) * len;
        var dLon = Math.sin(rad) * len / Math.cos(loc.lat * Math.PI / 180);
        var arrow = L.polyline([[loc.lat, loc.lon], [loc.lat + dLat, loc.lon + dLon]],
                               { color: color, weight: 3, opacity: .9 }).addTo(map);
        mapLayers.push(arrow);
      }

      var pop = document.createElement("div");
      pop.className = "wx-pop";
      pop.textContent = detailText(locId, idx);

      var up = (i % 2 === 0);
      var mk = L.circleMarker([loc.lat, loc.lon], {
        radius: 8, color: "#ffffff", weight: 2, fillColor: color, fillOpacity: 1
      }).addTo(map)
        .bindTooltip((i + 1) + " " + label, {
          permanent: true, direction: up ? "top" : "bottom",
          offset: up ? [0, -9] : [0, 9], className: "wx-pin", opacity: 1
        })
        .bindPopup(pop, { maxWidth: 520, minWidth: 220 });
      mapLayers.push(mk);
    });

    if (allPts.length === 1) map.setView(allPts[0], 9);
    else map.fitBounds(L.latLngBounds(allPts), { padding: [40, 40] });
    setTimeout(function () { map.invalidateSize(); }, 60);
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
  }

  function updateGridNote() {
    var note = $("gridNote");
    if (!note) return;
    var base = "선은 항해 순서, 짧은 막대는 바람이 불어 가는 방향입니다. "
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
    renderTimeBar();
    renderSummary();
    updateGridNote();
    syncTopHeight();
    if (state.view === "map") renderMap();
    if (state.view === "detail") renderDetail();
    if (state.view === "grid") renderGrid();
    if (state.view === "warn") renderWarn();
  }

  // ---------------------------------------------------------------- 시작
  function wire() {
    $("timeRange").addEventListener("input", function (e) {
      state.timeIndex = Number(e.target.value);
      renderTimeBar(); renderSummary();
      if (state.view === "map") renderMap();
      if (state.view === "grid") renderGrid();
    });
    $("timePrev").onclick = function () {
      state.timeIndex = Math.max(0, state.timeIndex - 1);
      renderTimeBar(); renderSummary();
      if (state.view === "map") renderMap();
    };
    $("timeNext").onclick = function () {
      state.timeIndex = Math.min(FC.times.length - 1, state.timeIndex + 1);
      renderTimeBar(); renderSummary();
      if (state.view === "map") renderMap();
    };
    $("timeNow").onclick = function () {
      state.timeIndex = nowIndex();
      renderTimeBar(); renderSummary();
      if (state.view === "map") renderMap();
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
