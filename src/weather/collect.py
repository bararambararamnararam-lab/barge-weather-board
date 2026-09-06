"""실제로 데이터를 받아 데이터베이스에 넣는 부분.

이 파일은 명령창에서 직접 실행할 수 있다.
    python -m weather.collect all       (기본값: 예보 + 특보 둘 다)
    python -m weather.collect forecast  (예보만)
    python -m weather.collect warning   (특보만)

.bat 파일들이 결국 이 명령을 대신 실행해 준다.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from typing import Any

from . import grid as grid_module
from .archive import build_pending_archives
from .config import DB_PATH, Config, load_api_key, load_config
from .db import (
    apply_retention,
    database_size_mb,
    insert_forecast_rows,
    insert_observation_rows,
    log_run,
    session,
    upsert_warning_rows,
    vacuum,
)
from .sources import combined, kma


def _now(config: Config) -> datetime:
    """설정된 시간대(기본 Asia/Seoul)의 현재 시각."""
    return datetime.now(config.timezone)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M")


def export_web_files(config: Config, verbose: bool = True) -> None:
    """웹 화면이 읽을 JSON 을 다시 만든다.

    ★ 반드시 예보와 특보를 '둘 다' 저장한 뒤에 불러야 한다.
      예전에는 예보 수집이 끝나자마자 여기를 불렀는데, 특보는 그 다음에
      수집되기 때문에 warnings.json 이 늘 비어 있었다.
      (실제로 배포된 화면에서 특보가 하나도 안 뜨는 문제가 났다.)

    여기서 실패해도 수집 자체는 성공으로 둔다. 자료는 이미 DB 에 들어갔고,
    JSON 은 5_웹자료_다시만들기.bat 으로 언제든 다시 만들 수 있다.
    """
    try:
        from .export_web import export as export_web
        export_web(config, verbose=verbose)
    except Exception as exc:
        if verbose:
            print("[웹] JSON 내보내기 실패(수집은 정상): {}".format(exc))


def collect_forecast(config: Config, verbose: bool = True,
                     export: bool = True) -> dict[str, Any]:
    """예보와 현재값을 수집해 저장한다."""
    started = _now(config)
    result: dict[str, Any] = {
        "kind": "forecast",
        "started_at": _stamp(started),
        "status": "ok",
        "forecast_rows": 0,
        "observation_rows": 0,
        "warnings": [],
    }

    if verbose:
        print("[예보] 수집 시작 {} KST".format(_stamp(started)))
        print("[예보] 대상 지점 {}곳, {}일치".format(
            len(config.locations), config.forecast_days))

    try:
        # 해역 색칠용 바다 칸 목록. ECMWF 를 받을 때 같이 뽑으므로
        # 격자 때문에 추가로 내려받는 자료는 없다.
        grid_cells: list[tuple[float, float]] = []
        grid_opts = grid_module.settings(config)
        if grid_opts["enabled"]:
            try:
                grid_cells = grid_module.load_mask(
                    grid_module.box_of(config, grid_opts),
                    float(grid_opts["mask_step_deg"]), verbose=verbose)
            except Exception as exc:
                if verbose:
                    print("[격자] 바다 마스크 준비 실패(격자 없이 진행): {}".format(exc))

        rows, source_warnings, grid_raw = combined.collect_forecast(
            config, grid_cells=grid_cells, verbose=verbose)
        observations = combined.build_observation_rows(config, rows, started)
        result["warnings"] = source_warnings

        with session() as conn:
            written = insert_forecast_rows(conn, rows)
            observed = insert_observation_rows(conn, observations)
            result["forecast_rows"] = written
            result["observation_rows"] = observed

            # 순서가 중요하다.
            # 1) 지난 날짜를 아카이브로 줄여 놓고
            # 2) 그다음에 보존기간 지난 원본을 지운다.
            # 순서가 바뀌면 아카이브를 만들기도 전에 원본이 사라진다.
            archived = build_pending_archives(
                conn, config, started.replace(tzinfo=None), verbose=verbose)
            result["archive"] = archived

            deleted = apply_retention(
                conn,
                started.replace(tzinfo=None),
                config.retention.get("raw_snapshot_days"),
                config.retention.get("archive_days"),
                config.retention.get("observation_days"),
                config.retention.get("warning_days"),
            )
            result["deleted"] = deleted

            status = "partial" if source_warnings else "ok"
            result["status"] = status
            log_run(
                conn,
                started_at=_stamp(started),
                kind="forecast",
                status=status,
                rows_written=written,
                message="; ".join(source_warnings) if source_warnings else None,
                finished_at=_stamp(_now(config)),
            )

        if verbose:
            print("[예보] 저장 완료: 예보 {}행, 현재값 {}행".format(
                result["forecast_rows"], result["observation_rows"]))
            if archived.get("rows"):
                print("[예보] 아카이브 생성: {}일치 {}행".format(
                    archived["days"], archived["rows"]))
            d = result.get("deleted") or {}
            if any(d.values()):
                print("[예보] 보존기간 초과분 삭제: 원본 {}행, 아카이브 {}행, "
                      "실황 {}행, 특보 {}행".format(
                          d["forecast"], d["archive"], d["observation"], d["warning"]))
            for message in source_warnings:
                print("[예보] 경고: {}".format(message))

        # 웹 화면용 JSON.
        # 예보만 따로 수집할 때는 여기서 만든다.
        # 예보+특보를 같이 수집할 때(collect_all)는 특보까지 끝난 뒤에
        # 한 번만 만들도록 export=False 로 넘어온다.
        if export:
            export_web_files(config, verbose=verbose)

        # 지도 격자(해역 색칠).
        # 새 ECMWF 사이클을 받았을 때만 다시 만든다.
        # 사이클이 그대로면 이미 있는 grid.json 이 유효하므로 건드리지 않는다.
        try:
            from .export_web import write_grid
            if grid_raw and grid_cells:
                write_grid(grid_module.build(config, grid_raw, grid_cells,
                                             verbose=verbose), verbose=verbose)
            elif not grid_opts["enabled"]:
                write_grid(None, verbose=verbose)
            elif verbose:
                print("[격자] 새 사이클이 아니라 기존 grid.json 을 그대로 둡니다")
        except Exception as exc:
            if verbose:
                print("[격자] 만들기 실패(나머지는 정상): {}".format(exc))

        _maybe_vacuum(config, verbose)

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        if verbose:
            print("[예보] 실패: {}".format(exc))
            traceback.print_exc()
        try:
            with session() as conn:
                log_run(conn, _stamp(started), "forecast", "error", 0, str(exc),
                        _stamp(_now(config)))
        except Exception:
            pass

    return result


def _maybe_vacuum(config: Config, verbose: bool = True) -> None:
    """가끔 한 번씩 지운 자리를 파일에서 실제로 회수한다.

    SQLite 는 DELETE 만으로 파일이 줄지 않아서, 안 하면 지워도 용량이 그대로다.
    매번 하면 느리므로 설정한 횟수마다 한 번만 한다.
    """
    every = int(config.retention.get("vacuum_every_n_runs") or 0)
    if every <= 0:
        return
    with session() as conn:
        runs = conn.execute(
            "SELECT COUNT(*) FROM collection_run WHERE kind = 'forecast'").fetchone()[0]
        if runs % every != 0:
            return
        before = database_size_mb()
        vacuum(conn)
    after = database_size_mb()
    if verbose:
        print("[정리] 파일 공간 회수: {} MB -> {} MB".format(before, after))


def collect_warning(config: Config, verbose: bool = True,
                    export: bool = True) -> dict[str, Any]:
    """기상청 특보 현황을 수집해 저장한다."""
    started = _now(config)
    result: dict[str, Any] = {
        "kind": "warning",
        "started_at": _stamp(started),
        "status": "ok",
        "warning_rows": 0,
    }

    api_key = load_api_key()
    if not api_key:
        message = (".env 파일에 KMA_HUB_API_KEY 가 없어 특보 수집을 건너뜁니다.")
        result["status"] = "skipped"
        result["error"] = message
        if verbose:
            print("[특보] {}".format(message))
        if export:
            export_web_files(config, verbose=verbose)
        return result

    if verbose:
        print("[특보] 수집 시작 {} KST".format(_stamp(started)))

    try:
        collection = config.collection
        rows, base_time = kma.build_warning_rows(
            api_key,
            started,
            timeout=int(collection.get("request_timeout_seconds", 30)),
            retries=int(collection.get("retry_count", 3)),
            wait=int(collection.get("retry_wait_seconds", 5)),
        )
        with session() as conn:
            new_rows, refreshed = upsert_warning_rows(conn, rows, _stamp(started))
            result["warning_rows"] = new_rows
            result["warning_refreshed"] = refreshed
            result["base_time"] = base_time
            log_run(conn, _stamp(started), "warning", "ok", new_rows,
                    "기준시각 {} (새 {}건, 유지 {}건)".format(base_time, new_rows, refreshed),
                    _stamp(_now(config)))

        if verbose:
            print("[특보] 조회 {}건 = 새 특보 {}건 + 계속 발효 중 {}건 "
                  "(기상청 기준시각 {})".format(
                      len(rows), new_rows, refreshed, kma.format_kma_time(base_time)))

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        if verbose:
            print("[특보] 실패: {}".format(exc))
        try:
            with session() as conn:
                log_run(conn, _stamp(started), "warning", "error", 0, str(exc),
                        _stamp(_now(config)))
        except Exception:
            pass

    # 특보까지 DB 에 들어간 뒤에 화면용 JSON 을 만든다.
    if export:
        export_web_files(config, verbose=verbose)

    return result


def collect_all(config: Config | None = None, verbose: bool = True) -> dict[str, Any]:
    """예보와 특보를 모두 수집한다."""
    cfg = config or load_config()
    # 화면용 JSON 은 둘 다 끝난 뒤에 한 번만 만든다(export=False).
    # 예보 직후에 만들면 그 시점엔 특보가 아직 DB 에 없어서
    # warnings.json 이 빈 채로 배포된다.
    forecast_result = collect_forecast(cfg, verbose, export=False)
    warning_result = collect_warning(cfg, verbose, export=False)
    export_web_files(cfg, verbose=verbose)
    if verbose:
        print("[완료] 데이터베이스 파일: {} ({} MB)".format(DB_PATH, database_size_mb()))
    return {"forecast": forecast_result, "warning": warning_result}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    target = args[0].lower() if args else "all"

    try:
        config = load_config()
    except Exception as exc:
        print("설정 파일을 읽지 못했습니다.\n{}".format(exc))
        return 1

    if target == "forecast":
        result = collect_forecast(config)
        return 0 if result["status"] in ("ok", "partial") else 1
    if target == "warning":
        result = collect_warning(config)
        return 0 if result["status"] in ("ok", "skipped") else 1
    if target == "all":
        results = collect_all(config)
        bad = [r for r in results.values() if r["status"] == "error"]
        return 1 if bad else 0

    print("사용법: python -m weather.collect [all|forecast|warning]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
