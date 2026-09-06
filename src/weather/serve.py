"""web/ 폴더를 브라우저가 볼 수 있게 띄워 주는 아주 작은 서버.

왜 필요한가
-----------
web/index.html 을 파일로 그냥 열면(file:// 주소) 브라우저가 보안 규칙 때문에
data/*.json 을 읽지 못한다. 그래서 아주 작은 웹 서버로 띄운다.
파이썬에 기본으로 들어 있는 기능만 쓴다. 따로 설치할 것이 없다.

같은 Wi-Fi 에 있는 폰에서도 볼 수 있게 0.0.0.0 으로 연다.
(0.0.0.0 은 '이 컴퓨터의 모든 네트워크 주소로 받겠다'는 뜻이다.)

    python -m weather.serve            8080 포트로 띄운다
    python -m weather.serve 9000       포트를 바꾼다
"""

from __future__ import annotations

import socket
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import PROJECT_ROOT

WEB_DIR = PROJECT_ROOT / "web"
DEFAULT_PORT = 8080


class Handler(SimpleHTTPRequestHandler):
    """web/ 폴더를 그대로 내보내는 기본 처리기.

    JSON 자료는 절대 캐시하지 않게 한다.
    안 그러면 새로 수집해도 폰이 옛 자료를 계속 보여 준다.
    """

    def end_headers(self) -> None:
        if self.path.startswith("/data/") or self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        else:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # 요청 한 줄씩 찍히면 시끄러우니 오류만 남긴다.
        status = args[1] if len(args) > 1 else ""
        if str(status).startswith(("4", "5")):
            sys.stderr.write("[웹] {} {}\n".format(self.path, status))


def local_ips() -> list[str]:
    """이 컴퓨터가 가진 주소들을 모아 온다(폰에서 칠 주소를 안내하려고)."""
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass

    # 바깥으로 나가는 경로에 쓰이는 주소를 한 번 더 확인한다.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        probe.close()
        if ip not in found:
            found.insert(0, ip)
    except OSError:
        pass
    return found


def run(port: int = DEFAULT_PORT) -> int:
    if not (WEB_DIR / "index.html").exists():
        print("web/index.html 을 찾을 수 없습니다: {}".format(WEB_DIR))
        return 1

    data_dir = WEB_DIR / "data"
    if not (data_dir / "forecast.json").exists():
        print("아직 웹용 자료가 없습니다.")
        print("1_수집하기.bat 을 먼저 실행하세요.")
        return 1

    handler = partial(Handler, directory=str(WEB_DIR))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    except OSError as exc:
        print("{} 포트를 쓸 수 없습니다: {}".format(port, exc))
        print("이미 서버가 떠 있는지 확인하거나 다른 포트를 쓰세요.")
        print("  예) python -m weather.serve 9000")
        return 1

    print("=" * 66)
    print(" 웹 화면을 띄웠습니다. 아래 주소로 들어가세요.")
    print("=" * 66)
    print("  이 컴퓨터에서 :  http://localhost:{}".format(port))
    for ip in local_ips():
        tag = "  (Tailscale)" if ip.startswith("100.") else ""
        print("  다른 기기에서 :  http://{}:{}{}".format(ip, port, tag))
    print()
    print("  폰에서 볼 때는 같은 Wi-Fi 에 연결하고 위 주소를 그대로 치세요.")
    print("  멈추려면 이 창을 닫거나 Ctrl+C 를 누르세요.")
    print("=" * 66)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n웹 서버를 멈췄습니다.")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    port = DEFAULT_PORT
    if args:
        try:
            port = int(args[0])
        except ValueError:
            print("포트는 숫자여야 합니다. 예) python -m weather.serve 9000")
            return 2
    return run(port)


if __name__ == "__main__":
    raise SystemExit(main())
