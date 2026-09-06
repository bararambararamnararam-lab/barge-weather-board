# -*- coding: utf-8 -*-
"""앱 아이콘을 그린다.

    python tools/make_icon.py      (Pillow 필요)

구성
    왼쪽 위 : 바람 (기상 앱이라는 표시)
    오른쪽  : 블록을 실은 바지선. 오른쪽 화면 밖까지 뻗어 일부만 보인다.
    아래    : 물결

블록은 두 가지다. 하나는 뱃머리처럼 앞이 휘어 올라간 선수 블록,
하나는 그냥 네모난 블록. 조선소에서 싣고 다니는 그 모양이다.

크게 그린 뒤 줄여서 가장자리를 부드럽게 만든다(안티앨리어싱).
안드로이드가 아이콘을 동그랗게 깎아도(maskable) 블록이 남도록
중요한 것은 가운데 80% 안에 둔다.
"""
import math
import os
from PIL import Image, ImageDraw

S = 512
K = 4
W = S * K

BG_TOP = (12, 20, 42)
BG_BOT = (32, 62, 108)
WIND = (163, 205, 255)
WAVE_A = (86, 156, 236)
WAVE_B = (62, 128, 208)
HULL = (14, 26, 46)           # 바지선 몸통
HULL_EDGE = (44, 66, 100)     # 갑판 모서리
BLOCK = (226, 234, 246)       # 블록 밝은 면
BLOCK_SIDE = (150, 168, 196)  # 블록 어두운 면 (입체감)

img = Image.new("RGB", (W, W), BG_TOP)
d = ImageDraw.Draw(img)

for y in range(W):
    t = (y / (W - 1)) ** 0.85
    d.line([(0, y), (W, y)],
           fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))


def stroke(points, color, width, cap=True):
    d.line(points, fill=color, width=width, joint="curve")
    if cap:
        r = width // 2
        for p in (points[0], points[-1]):
            d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)


def wind_line(y, x0, x1, amp, width):
    """바람 한 줄. 끝을 동그랗게 말아 '분다'는 느낌을 준다.

    ★ 갈고리를 본선과 매끄럽게 잇는 것이 핵심이다.
      예전에는 원호를 대충 끝점 근처에 그려서 이음매가 어긋나 보였다.
      지금은 본선 끝의 '진행 방향' 을 재고, 그 방향에 딱 접하는 원을
      찾아 거기서부터 감는다. 그래서 꺾이지 않고 이어진다.
    """
    pts = []
    n = 90
    for i in range(n + 1):
        t = i / n
        pts.append((x0 + (x1 - x0) * t, y + math.sin(t * math.pi * 1.5) * amp))

    # 본선 끝에서의 진행 방향(접선)
    ex, ey = pts[-1]
    px, py = pts[-8]
    dx, dy = ex - px, ey - py
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length

    # 접선에 수직인 두 방향 중 '위쪽' 을 고른다(위로 말려 올라가게)
    nx, ny = -uy, ux
    if ny > 0:
        nx, ny = uy, -ux

    r = width * 1.05
    cx, cy = ex + nx * r, ey + ny * r      # 접하는 원의 중심

    a0 = math.atan2(ey - cy, ex - cx)      # 원 중심에서 본선 끝을 본 각도
    # 감는 방향: 접선을 따라 계속 나아가는 쪽으로
    cross = ux * ny - uy * nx
    sweep = math.radians(255) * (1.0 if cross < 0 else -1.0)

    arc = []
    m = 40
    for i in range(m + 1):
        a = a0 + sweep * (i / m)
        arc.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    stroke(pts + arc, WIND, width)         # 본선과 갈고리를 한 붓으로


# ---- 바람 : 왼쪽에만 ----
wind_line(W * 0.150, W * 0.055, W * 0.315, W * 0.018, int(W * 0.028))
wind_line(W * 0.245, W * 0.030, W * 0.400, W * 0.022, int(W * 0.032))
wind_line(W * 0.340, W * 0.075, W * 0.270, W * 0.014, int(W * 0.025))


# ---- 블록을 실은 바지선 : 오른쪽 2/3 ----
DECK = W * 0.620          # 갑판 윗면
HULL_BOT = W * 0.735      # 배 밑
BOW = W * 0.330           # 배 왼쪽 끝(뱃머리)
STERN = W * 1.06          # 오른쪽은 화면 밖으로


def box_block(x, w, h):
    """네모난 블록."""
    top = DECK - h
    side = w * 0.15
    face = x + w - side
    d.rectangle([x, top, face, DECK], fill=BLOCK)
    d.polygon([(face, top), (x + w, top + h * 0.05),
               (x + w, DECK), (face, DECK)], fill=BLOCK_SIDE)


def bow_block(x, w, h):
    """뱃머리처럼 앞(왼쪽)이 휘어 올라간 블록."""
    top = DECK - h
    side = w * 0.15
    face = x + w - side

    pts = [(x, DECK)]
    n = 36
    for i in range(n + 1):          # 왼쪽 곡선: 아래에서 위로 휘어 오른다
        t = i / n
        pts.append((x + (w * 0.52) * (t ** 1.75),
                    DECK - h * math.sin(t * math.pi / 2) ** 0.9))
    pts += [(face, top), (face, DECK)]
    d.polygon(pts, fill=BLOCK)
    # 오른쪽 어두운 면
    d.polygon([(face, top), (x + w, top + h * 0.05),
               (x + w, DECK), (face, DECK)], fill=BLOCK_SIDE)


bow_block(W * 0.395, W * 0.270, W * 0.250)   # 선수 블록
box_block(W * 0.720, W * 0.215, W * 0.175)   # 네모 블록

# 갑판(평평한 판) + 몸통. 뱃머리 쪽을 비스듬히 깎는다.
d.polygon([(BOW + W * 0.030, DECK), (STERN, DECK),
           (STERN, DECK + W * 0.021), (BOW, DECK + W * 0.021)], fill=HULL_EDGE)
d.polygon([(BOW, DECK + W * 0.021), (STERN, DECK + W * 0.021),
           (STERN, HULL_BOT), (BOW + W * 0.055, HULL_BOT)], fill=HULL)


# ---- 물결 (배를 살짝 덮어 물에 떠 있게) ----
def wave_pts(y, amp, phase):
    pts = []
    n = 150
    for i in range(n + 1):
        t = i / n
        pts.append((-W * 0.06 + W * 1.12 * t,
                    y + math.sin(t * math.pi * 3.0 + phase) * amp))
    return pts


stroke(wave_pts(HULL_BOT + W * 0.004, W * 0.022, 0.4), WAVE_A, int(W * 0.038), cap=False)
stroke(wave_pts(HULL_BOT + W * 0.082, W * 0.026, 1.6), WAVE_B, int(W * 0.036), cap=False)
stroke(wave_pts(HULL_BOT + W * 0.172, W * 0.023, 2.8), WAVE_A, int(W * 0.034), cap=False)
stroke(wave_pts(HULL_BOT + W * 0.258, W * 0.020, 4.0), WAVE_B, int(W * 0.032), cap=False)

out = img.resize((S, S), Image.LANCZOS)
path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "web", "icon.png")
out.save(path, "PNG", optimize=True)
print("저장: %s (%d 바이트, %dx%d)" % (path, os.path.getsize(path), S, S))
