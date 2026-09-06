"""데이터 저장소(SQLite).

SQLite 는 '파일 하나가 곧 데이터베이스'인 방식이다.
서버를 설치할 필요가 없고, data/weather.db 파일 하나만 백업하면 전부 백업된다.

나중에 Supabase(Postgres)로 옮길 것을 염두에 두고,
Postgres 에서도 그대로 통하는 문법만 사용했다.

────────────────────────────────────────────────────────────────────────
표(테이블) 구성 — 용량을 줄이려고 3단계로 나눠 놨다
────────────────────────────────────────────────────────────────────────

1) forecast_snapshot  = 원본 스냅샷 (최근 7일)
   3시간마다 수집한 예보를 전체 9항목, 3~6시간 간격, 10일 앞까지 그대로 보관.
   화면에 지금 보이는 표는 항상 여기서 나온다. 7일 지나면 지운다.

2) forecast_archive   = 아카이브 스냅샷 (1년)
   하루에 한 번, 한국시간 09시에 가장 가까운 스냅샷 하나만 골라
   7항목 · 3시간 간격 · 7일 앞까지로 줄여서 1년 보관한다.
   '그때 예보가 어땠나'를 오래 보기 위한 것.

3) observation        = 과거 실황 (2년)
   수집 시점의 현재값. 예보가 아니다. 전체 항목 그대로 2년 보관.

4) kma_warning        = 기상청 특보 (2년)
   같은 특보를 30분마다 다시 쌓지 않는다. 내용이 같으면 한 행으로 두고
   '마지막으로 본 시각'만 갱신한다. (이렇게 안 하면 2년에 168만 행이 된다.)

5) collection_run     = 수집 실행 기록
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import DB_PATH

# ---------------------------------------------------------------------------
# 열 정의
# ---------------------------------------------------------------------------

# 원본 스냅샷 / 실황이 갖는 측정값 열과 단위.
VALUE_COLUMNS: dict[str, str] = {
    "wind_speed_ms": "m/s",
    "wind_direction_deg": "°",
    "wind_gust_ms": "m/s",
    "visibility_km": "km",
    "precipitation_mm": "mm",
    "weather_code": "WMO 코드",
    "cape_jkg": "J/kg",
    "wave_height_m": "m",
    "wave_direction_deg": "°",
    "wave_period_s": "s",
    "current_speed_kn": "kn",
    "current_direction_deg": "°",
    "sea_surface_temp_c": "°C",
}

# 1년 보관 아카이브에 남기는 항목.
# 풍속·풍향·유의파고 + (판정을 다시 계산할 수 있게) 순간풍속·시정.
ARCHIVE_VALUE_COLUMNS: tuple[str, ...] = (
    "wind_speed_ms",
    "wind_direction_deg",
    "wind_gust_ms",
    "visibility_km",
    "wave_height_m",
)

# 화면에 보여줄 한글 이름
COLUMN_LABELS: dict[str, str] = {
    "wind_speed_ms": "평균 풍속",
    "wind_direction_deg": "풍향",
    "wind_gust_ms": "순간 풍속",
    "visibility_km": "시정",
    "precipitation_mm": "시간 강수량",
    "weather_code": "기상 코드",
    "cape_jkg": "뇌우 지수(CAPE)",
    "wave_height_m": "유의파고",
    "wave_direction_deg": "파향",
    "wave_period_s": "파주기",
    "current_speed_kn": "해류 속도",
    "current_direction_deg": "해류 방향",
    "sea_surface_temp_c": "수온",
}

_INT_COLUMNS = {"weather_code"}

_VALUE_COLS_SQL = ",\n    ".join(
    "{} {}".format(name, "INTEGER" if name in _INT_COLUMNS else "REAL")
    for name in VALUE_COLUMNS
)

_ARCHIVE_COLS_SQL = ",\n    ".join(
    "{} REAL".format(name) for name in ARCHIVE_VALUE_COLUMNS
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_snapshot (
    collected_at    TEXT NOT NULL,
    location_id     TEXT NOT NULL,
    valid_time      TEXT NOT NULL,
    {value_cols},
    source_forecast TEXT,
    source_marine   TEXT,
    PRIMARY KEY (collected_at, location_id, valid_time)
);

CREATE INDEX IF NOT EXISTS ix_forecast_lookup
    ON forecast_snapshot (location_id, valid_time, collected_at);

CREATE INDEX IF NOT EXISTS ix_forecast_collected
    ON forecast_snapshot (collected_at);

CREATE TABLE IF NOT EXISTS forecast_archive (
    archive_date  TEXT NOT NULL,
    location_id   TEXT NOT NULL,
    valid_time    TEXT NOT NULL,
    collected_at  TEXT NOT NULL,
    {archive_cols},
    judgement       TEXT,
    warning_summary TEXT,
    PRIMARY KEY (archive_date, location_id, valid_time)
);

CREATE INDEX IF NOT EXISTS ix_archive_lookup
    ON forecast_archive (location_id, valid_time);

CREATE TABLE IF NOT EXISTS observation (
    observed_at   TEXT NOT NULL,
    location_id   TEXT NOT NULL,
    {value_cols},
    data_kind     TEXT NOT NULL,
    source        TEXT,
    PRIMARY KEY (observed_at, location_id)
);

CREATE INDEX IF NOT EXISTS ix_observation_lookup
    ON observation (location_id, observed_at);

CREATE TABLE IF NOT EXISTS kma_warning (
    reg_id     TEXT NOT NULL,
    wrn        TEXT NOT NULL,
    tm_fc      TEXT NOT NULL,
    tm_ef      TEXT NOT NULL,
    lvl        TEXT NOT NULL,
    cmd        TEXT NOT NULL,
    ed_tm      TEXT NOT NULL,
    reg_up     TEXT,
    reg_up_ko  TEXT,
    reg_ko     TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    base_time  TEXT,
    PRIMARY KEY (reg_id, wrn, tm_fc, tm_ef, lvl, cmd, ed_tm)
);

CREATE INDEX IF NOT EXISTS ix_warning_last_seen
    ON kma_warning (last_seen);

CREATE INDEX IF NOT EXISTS ix_warning_reg
    ON kma_warning (reg_id, last_seen);

CREATE TABLE IF NOT EXISTS collection_run (
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    rows_written INTEGER DEFAULT 0,
    message      TEXT,
    PRIMARY KEY (started_at, kind)
);
""".format(value_cols=_VALUE_COLS_SQL, archive_cols=_ARCHIVE_COLS_SQL)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """데이터베이스 파일을 열고(없으면 만들고) 연결을 돌려준다."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # 수집과 화면이 동시에 파일을 써도 서로 막히지 않게 하는 설정.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def session(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """with 문으로 쓰는 연결. 블록이 끝나면 자동으로 저장하고 닫는다."""
    conn = connect(db_path)
    try:
        init_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """테이블이 없으면 만들고, 옛 구조가 남아 있으면 새 구조로 옮긴다."""
    _migrate_warning_table(conn)
    conn.executescript(SCHEMA)


def _migrate_warning_table(conn: sqlite3.Connection) -> None:
    """옛 kma_warning(수집할 때마다 전부 새로 쌓던 구조)을 중복 제거 구조로 바꾼다.

    옛 구조에는 collected_at 열이 있었고 수집 1회마다 48행씩 늘어났다.
    새 구조는 같은 특보를 한 행으로 두고 first_seen / last_seen 만 갱신한다.
    """
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "kma_warning" not in tables:
        return

    columns = {row[1] for row in conn.execute("PRAGMA table_info(kma_warning)")}
    if "collected_at" not in columns:
        return  # 이미 새 구조

    conn.executescript("""
    CREATE TABLE kma_warning_new (
        reg_id     TEXT NOT NULL,
        wrn        TEXT NOT NULL,
        tm_fc      TEXT NOT NULL,
        tm_ef      TEXT NOT NULL,
        lvl        TEXT NOT NULL,
        cmd        TEXT NOT NULL,
        ed_tm      TEXT NOT NULL,
        reg_up     TEXT,
        reg_up_ko  TEXT,
        reg_ko     TEXT,
        first_seen TEXT NOT NULL,
        last_seen  TEXT NOT NULL,
        base_time  TEXT,
        PRIMARY KEY (reg_id, wrn, tm_fc, tm_ef, lvl, cmd, ed_tm)
    );

    INSERT INTO kma_warning_new
        (reg_id, wrn, tm_fc, tm_ef, lvl, cmd, ed_tm,
         reg_up, reg_up_ko, reg_ko, first_seen, last_seen, base_time)
    SELECT reg_id, wrn, tm_fc, tm_ef,
           COALESCE(lvl, ''), COALESCE(cmd, ''), COALESCE(ed_tm, ''),
           MAX(reg_up), MAX(reg_up_ko), MAX(reg_ko),
           MIN(collected_at), MAX(collected_at), MAX(base_time)
    FROM kma_warning
    GROUP BY reg_id, wrn, tm_fc, tm_ef,
             COALESCE(lvl, ''), COALESCE(cmd, ''), COALESCE(ed_tm, '');

    DROP TABLE kma_warning;
    ALTER TABLE kma_warning_new RENAME TO kma_warning;
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# 쓰기
# ---------------------------------------------------------------------------

def insert_forecast_rows(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> int:
    """원본 예보 스냅샷을 저장한다.

    같은 (수집시각, 위치, 유효시각) 이 이미 있으면 무시한다(INSERT OR IGNORE).
    이렇게 해서 한 번 저장한 스냅샷은 절대 바뀌지 않는다는 PRD 규칙을 지킨다.
    """
    if not rows:
        return 0
    columns = ["collected_at", "location_id", "valid_time",
               *VALUE_COLUMNS, "source_forecast", "source_marine"]
    placeholders = ", ".join("?" for _ in columns)
    sql = (
        "INSERT OR IGNORE INTO forecast_snapshot ({}) VALUES ({})"
        .format(", ".join(columns), placeholders)
    )
    payload = [tuple(row.get(col) for col in columns) for row in rows]
    cur = conn.executemany(sql, payload)
    return max(cur.rowcount or 0, 0)


def insert_archive_rows(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> int:
    """아카이브 스냅샷(하루 1개, 1년 보관)을 저장한다."""
    if not rows:
        return 0
    columns = ["archive_date", "location_id", "valid_time", "collected_at",
               *ARCHIVE_VALUE_COLUMNS, "judgement", "warning_summary"]
    placeholders = ", ".join("?" for _ in columns)
    sql = (
        "INSERT OR IGNORE INTO forecast_archive ({}) VALUES ({})"
        .format(", ".join(columns), placeholders)
    )
    payload = [tuple(row.get(col) for col in columns) for row in rows]
    cur = conn.executemany(sql, payload)
    return max(cur.rowcount or 0, 0)


def insert_observation_rows(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]]) -> int:
    """수집 시점의 현재값을 저장한다."""
    if not rows:
        return 0
    columns = ["observed_at", "location_id", *VALUE_COLUMNS, "data_kind", "source"]
    placeholders = ", ".join("?" for _ in columns)
    sql = (
        "INSERT OR IGNORE INTO observation ({}) VALUES ({})"
        .format(", ".join(columns), placeholders)
    )
    payload = [tuple(row.get(col) for col in columns) for row in rows]
    cur = conn.executemany(sql, payload)
    return max(cur.rowcount or 0, 0)


def upsert_warning_rows(conn: sqlite3.Connection, rows: Sequence[dict[str, Any]],
                        seen_at: str) -> tuple[int, int]:
    """기상청 특보를 저장한다. 같은 내용이면 last_seen 만 갱신한다.

    돌려주는 값: (새로 생긴 행 수, 갱신만 된 행 수)
    """
    if not rows:
        return 0, 0

    sql = """
    INSERT INTO kma_warning
        (reg_id, wrn, tm_fc, tm_ef, lvl, cmd, ed_tm,
         reg_up, reg_up_ko, reg_ko, first_seen, last_seen, base_time)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (reg_id, wrn, tm_fc, tm_ef, lvl, cmd, ed_tm)
    DO UPDATE SET last_seen = excluded.last_seen,
                  base_time = excluded.base_time
    """
    before = conn.execute("SELECT COUNT(*) FROM kma_warning").fetchone()[0]
    payload = [
        (
            row.get("reg_id", ""), row.get("wrn", ""),
            row.get("tm_fc", ""), row.get("tm_ef", ""),
            row.get("lvl", "") or "", row.get("cmd", "") or "",
            row.get("ed_tm", "") or "",
            row.get("reg_up"), row.get("reg_up_ko"), row.get("reg_ko"),
            seen_at, seen_at, row.get("base_time"),
        )
        for row in rows
    ]
    conn.executemany(sql, payload)
    after = conn.execute("SELECT COUNT(*) FROM kma_warning").fetchone()[0]
    inserted = after - before
    return inserted, len(rows) - inserted


def log_run(
    conn: sqlite3.Connection,
    started_at: str,
    kind: str,
    status: str,
    rows_written: int = 0,
    message: str | None = None,
    finished_at: str | None = None,
) -> None:
    """수집 실행 기록을 남긴다."""
    conn.execute(
        "INSERT OR REPLACE INTO collection_run "
        "(started_at, finished_at, kind, status, rows_written, message) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (started_at, finished_at, kind, status, rows_written, message),
    )


# ---------------------------------------------------------------------------
# 읽기 — 원본 스냅샷
# ---------------------------------------------------------------------------

def latest_collected_at(conn: sqlite3.Connection) -> str | None:
    """가장 최근 예보 수집 시각."""
    row = conn.execute("SELECT MAX(collected_at) AS v FROM forecast_snapshot").fetchone()
    return row["v"] if row else None


def list_collected_at(conn: sqlite3.Connection, limit: int = 200) -> list[str]:
    """원본 스냅샷 수집 시각 목록(최신순)."""
    rows = conn.execute(
        "SELECT DISTINCT collected_at FROM forecast_snapshot "
        "ORDER BY collected_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["collected_at"] for r in rows]


def fetch_forecast(
    conn: sqlite3.Connection,
    location_ids: Iterable[str],
    collected_at: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> list[sqlite3.Row]:
    """원본 예보를 읽어 온다.

    collected_at 이 None 이면 유효시각마다 가장 최근에 수집된 예보를 고른다.
    (PRD 5.1: 동일 유효 시각의 기본 표시는 가장 최근 수집분)
    collected_at 에 값을 주면 그 시점의 스냅샷만 그대로 보여 준다.
    """
    ids = list(location_ids)
    if not ids:
        return []
    id_marks = ", ".join("?" for _ in ids)
    params: list[Any] = list(ids)

    time_filter = ""
    if valid_from:
        time_filter += " AND valid_time >= ?"
        params.append(valid_from)
    if valid_to:
        time_filter += " AND valid_time <= ?"
        params.append(valid_to)

    if collected_at:
        params.append(collected_at)
        sql = (
            "SELECT * FROM forecast_snapshot "
            "WHERE location_id IN ({}){} AND collected_at = ? "
            "ORDER BY location_id, valid_time".format(id_marks, time_filter)
        )
        return conn.execute(sql, params).fetchall()

    sql = """
        SELECT f.* FROM forecast_snapshot AS f
        JOIN (
            SELECT location_id, valid_time, MAX(collected_at) AS collected_at
            FROM forecast_snapshot
            WHERE location_id IN ({ids}){time_filter}
            GROUP BY location_id, valid_time
        ) AS newest
          ON f.location_id  = newest.location_id
         AND f.valid_time   = newest.valid_time
         AND f.collected_at = newest.collected_at
        ORDER BY f.location_id, f.valid_time
    """.format(ids=id_marks, time_filter=time_filter)
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# 읽기 — 아카이브
# ---------------------------------------------------------------------------

def list_archive_dates(conn: sqlite3.Connection, limit: int = 400) -> list[tuple[str, str]]:
    """아카이브에 들어 있는 날짜 목록(최신순). (archive_date, collected_at)"""
    rows = conn.execute(
        "SELECT archive_date, MIN(collected_at) AS collected_at "
        "FROM forecast_archive GROUP BY archive_date "
        "ORDER BY archive_date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(r["archive_date"], r["collected_at"]) for r in rows]


def fetch_archive(
    conn: sqlite3.Connection,
    location_ids: Iterable[str],
    archive_date: str,
) -> list[sqlite3.Row]:
    """특정 날짜의 아카이브 스냅샷을 읽어 온다."""
    ids = list(location_ids)
    if not ids:
        return []
    id_marks = ", ".join("?" for _ in ids)
    sql = (
        "SELECT * FROM forecast_archive "
        "WHERE location_id IN ({}) AND archive_date = ? "
        "ORDER BY location_id, valid_time".format(id_marks)
    )
    return conn.execute(sql, [*ids, archive_date]).fetchall()


def archived_dates_set(conn: sqlite3.Connection) -> set[str]:
    """이미 아카이브한 날짜 집합. 같은 날을 두 번 만들지 않으려고 쓴다."""
    return {
        row[0] for row in conn.execute(
            "SELECT DISTINCT archive_date FROM forecast_archive")
    }


# ---------------------------------------------------------------------------
# 읽기 — 실황 / 특보 / 실행기록
# ---------------------------------------------------------------------------

def fetch_observations(
    conn: sqlite3.Connection,
    location_ids: Iterable[str],
    observed_from: str | None = None,
    observed_to: str | None = None,
) -> list[sqlite3.Row]:
    """저장된 현재값(과거 실황)을 읽어 온다."""
    ids = list(location_ids)
    if not ids:
        return []
    id_marks = ", ".join("?" for _ in ids)
    params: list[Any] = list(ids)
    where = "location_id IN ({})".format(id_marks)
    if observed_from:
        where += " AND observed_at >= ?"
        params.append(observed_from)
    if observed_to:
        where += " AND observed_at <= ?"
        params.append(observed_to)
    sql = "SELECT * FROM observation WHERE {} ORDER BY location_id, observed_at".format(where)
    return conn.execute(sql, params).fetchall()


def fetch_latest_warnings(conn: sqlite3.Connection) -> tuple[str | None, list[sqlite3.Row]]:
    """가장 최근 수집에서 살아 있던 특보 전부. (마지막 수집 시각, 행 목록)

    last_seen 이 가장 최근 값과 같은 행 = 마지막 수집 때도 여전히 떠 있던 특보.
    """
    row = conn.execute("SELECT MAX(last_seen) AS v FROM kma_warning").fetchone()
    latest = row["v"] if row else None
    if not latest:
        return None, []
    rows = conn.execute(
        "SELECT * FROM kma_warning WHERE last_seen = ? ORDER BY reg_id, wrn",
        (latest,),
    ).fetchall()
    return latest, rows


def fetch_warnings_seen_at(conn: sqlite3.Connection, moment: str) -> list[sqlite3.Row]:
    """특정 시각에 발효 중이던 특보를 돌려준다(아카이브 만들 때 사용).

    first_seen <= moment <= last_seen 인 행을 고른다.
    """
    return conn.execute(
        "SELECT * FROM kma_warning WHERE first_seen <= ? AND last_seen >= ? "
        "ORDER BY reg_id, wrn",
        (moment, moment),
    ).fetchall()


def fetch_run_log(conn: sqlite3.Connection, limit: int = 30) -> list[sqlite3.Row]:
    """최근 수집 실행 기록."""
    return conn.execute(
        "SELECT * FROM collection_run ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()


def storage_summary(conn: sqlite3.Connection) -> dict[str, int]:
    """각 표에 몇 행이 있는지. 사이드바에 보여 준다."""
    out: dict[str, int] = {}
    for table in ("forecast_snapshot", "forecast_archive", "observation",
                  "kma_warning", "collection_run"):
        try:
            out[table] = conn.execute(
                "SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
        except sqlite3.Error:
            out[table] = 0
    return out


# ---------------------------------------------------------------------------
# 보존 기간 정리
# ---------------------------------------------------------------------------

def apply_retention(
    conn: sqlite3.Connection,
    now: datetime,
    raw_days: int | None,
    archive_days: int | None,
    observation_days: int | None,
    warning_days: int | None,
) -> dict[str, int]:
    """보존 기간이 지난 데이터를 지운다.

    None 을 주면 그 종류는 지우지 않는다(영구 보관).
    기준은 수집한 시각이다. 예보의 유효시각이 아니다.

    ★ 원본 스냅샷을 지우기 전에 아카이브가 먼저 만들어져 있어야 한다.
      collect.py 가 '아카이브 만들기 → 보존기간 정리' 순서로 부른다.
    """
    deleted = {"forecast": 0, "archive": 0, "observation": 0, "warning": 0}

    if raw_days is not None:
        cutoff = (now - timedelta(days=int(raw_days))).strftime("%Y-%m-%dT%H:%M")
        cur = conn.execute("DELETE FROM forecast_snapshot WHERE collected_at < ?", (cutoff,))
        deleted["forecast"] = max(cur.rowcount or 0, 0)

    if archive_days is not None:
        cutoff = (now - timedelta(days=int(archive_days))).strftime("%Y-%m-%d")
        cur = conn.execute("DELETE FROM forecast_archive WHERE archive_date < ?", (cutoff,))
        deleted["archive"] = max(cur.rowcount or 0, 0)

    if observation_days is not None:
        cutoff = (now - timedelta(days=int(observation_days))).strftime("%Y-%m-%dT%H:%M")
        cur = conn.execute("DELETE FROM observation WHERE observed_at < ?", (cutoff,))
        deleted["observation"] = max(cur.rowcount or 0, 0)

    if warning_days is not None:
        cutoff = (now - timedelta(days=int(warning_days))).strftime("%Y-%m-%dT%H:%M")
        cur = conn.execute("DELETE FROM kma_warning WHERE last_seen < ?", (cutoff,))
        deleted["warning"] = max(cur.rowcount or 0, 0)

    return deleted


def vacuum(conn: sqlite3.Connection) -> None:
    """지운 자리를 실제로 파일에서 회수한다.

    SQLite 는 DELETE 만으로는 파일 크기가 줄지 않는다.
    수집할 때마다 하면 느리므로 collect.py 가 가끔씩만 부른다.
    """
    conn.commit()
    conn.execute("VACUUM")


def database_size_mb(db_path: Path | str | None = None) -> float:
    """데이터베이스 파일 크기(MB). 화면에 보여 주기 위한 용도."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        return 0.0
    return round(path.stat().st_size / (1024 * 1024), 2)
