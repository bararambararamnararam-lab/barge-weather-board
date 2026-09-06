"""정해진 주기마다 자동으로 수집을 돌리는 프로그램.

`3_자동수집_시작.bat` 을 더블클릭하면 이 프로그램이 실행되고,
창을 닫기 전까지 계속 켜져 있으면서 알아서 데이터를 모은다.

주기는 config/weather_config.yaml 의 collection 항목에서 정한다.
  forecast_interval_minutes: 180   -> 예보는 3시간마다
  warning_interval_minutes: 30     -> 특보는 30분마다

프로그램을 켜면 먼저 한 번 바로 수집하고, 그 다음부터 주기를 지킨다.
멈추려면 창을 닫거나 Ctrl+C 를 누른다.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from .collect import collect_forecast, collect_warning
from .config import Config, load_config


def _now(config: Config) -> datetime:
    return datetime.now(config.timezone)


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def run(config: Config | None = None) -> int:
    cfg = config or load_config()
    collection = cfg.collection
    forecast_minutes = int(collection.get("forecast_interval_minutes", 180))
    warning_minutes = int(collection.get("warning_interval_minutes", 30))

    print("=" * 68)
    print(" 자동 수집을 시작합니다.")
    print(" 예보 주기 : {}분마다".format(forecast_minutes))
    print(" 특보 주기 : {}분마다".format(warning_minutes))
    print(" 멈추려면 이 창을 닫거나 Ctrl+C 를 누르세요.")
    print("=" * 68)

    next_forecast = _now(cfg)
    next_warning = _now(cfg)

    try:
        while True:
            current = _now(cfg)

            if current >= next_forecast:
                collect_forecast(cfg)
                next_forecast = _now(cfg) + timedelta(minutes=forecast_minutes)
                print("[예보] 다음 수집 예정: {}".format(_fmt(next_forecast)))

            if current >= next_warning:
                collect_warning(cfg)
                next_warning = _now(cfg) + timedelta(minutes=warning_minutes)
                print("[특보] 다음 수집 예정: {}".format(_fmt(next_warning)))

            # 다음 할 일까지 남은 시간만큼 쉰다. 최대 60초씩 끊어서 잔다
            # (그래야 Ctrl+C 를 눌렀을 때 바로 반응한다).
            upcoming = min(next_forecast, next_warning)
            remaining = (upcoming - _now(cfg)).total_seconds()
            time.sleep(max(1.0, min(60.0, remaining)))

    except KeyboardInterrupt:
        print("\n자동 수집을 멈췄습니다.")
        return 0


def main() -> int:
    try:
        config = load_config()
    except Exception as exc:
        print("설정 파일을 읽지 못했습니다.\n{}".format(exc))
        return 1
    return run(config)


if __name__ == "__main__":
    sys.exit(main())
