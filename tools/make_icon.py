# -*- coding: utf-8 -*-
"""앱 아이콘.

남색 밤바다 + 바람(위) + 블록을 실은 바지선(가운데) + 물결(아래).

바지선은 화면 좌우 밖까지 뻗게 그린다. 배 전체를 우겨넣지 않고
'실려 있는 블록' 을 크게 보여 주는 편이 작은 아이콘에서 잘 읽힌다.

크게 그린 뒤 줄여서 가장자리를 부드럽게 만든다(안티앨리어싱).
안드로이드가 아이콘을 동그랗게 깎아도(maskable) 블록은 남도록
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
HULL = (14, 26, 46)          # 바지선 몸통
HULL_EDGE = (44, 66, 100)    # 갑판 모서리 (몸통과 구분)
BLOCK = (226, 234, 246)      # 블록 밝은 면
BLOCK_SIDE = (150, 168, 196) # 블록 어두운 면 (입체감)

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
    """바람 한 줄. 끝을 살짝 말아 '분다'는 느낌만 준다."""
    pts = []
    n = 90
    for i in range(n + 1):
        t = i / n
        pts.append((x0 + (x1 - x0) * t, y + math.sin(t * math.pi * 1.5) * amp))
    stroke(pts, WIND, width)
    cx, cy = pts[-1]
    r = width * 0.95
    arc = []
    for i in range(25):
        a = math.radians(100 - i * 9.5)
        arc.append((cx - r + r * math.cos(a), cy - r + r * math.sin(a)))
    stroke(arc, WIND, width)


# ---- 바람 (위쪽) ----
wind_line(W * 0.145, W * 0.17, W * 0.70, W * 0.021, int(W * 0.029))
wind_line(W * 0.240, W * 0.11, W * 0.82, W * 0.025, int(W * 0.033))
wind_line(W * 0.330, W * 0.22, W * 0.63, W * 0.017, int(W * 0.025))


# ---- 블록을 실은 바지선 ----
DECK = W * 0.615          # 갑판 윗면
HULL_BOT = W * 0.730      # 배 밑
X0, X1 = -W * 0.06, W * 1.06   # 좌우 화면 밖까지


def block(x, w, h, step=0.0):
    """조선소 블록 하나.

    오른쪽에 어두운 면을 붙여 납작한 사각형이 아니라 덩어리로 보이게 한다.
    step 을 주면 위를 한 단 낮춰, 택배 상자가 아니라 구조물 느낌을 준다.
    """
    top = DECK - h
    side = w * 0.15
    face = x + w - side          # 밝은 면의 오른쪽 끝

    if step:
        # 왼쪽이 한 단 낮은 2단 덩어리
        lw = w * step
        ltop = top + h * 0.32
        d.rectangle([x, ltop, x + lw, DECK], fill=BLOCK)
        d.rectangle([x + lw, top, face, DECK], fill=BLOCK)
        d.polygon([(face, top), (x + w, top + h * 0.05),
                   (x + w, DECK), (face, DECK)], fill=BLOCK_SIDE)
        # 낮은 단의 윗면도 살짝 어둡게 (빛 방향 통일)
        d.polygon([(x, ltop), (x + lw, ltop),
                   (x + lw, ltop + h * 0.05), (x, ltop + h * 0.05)],
                  fill=BLOCK_SIDE)
    else:
        d.rectangle([x, top, face, DECK], fill=BLOCK)
        d.polygon([(face, top), (x + w, top + h * 0.05),
                   (x + w, DECK), (face, DECK)], fill=BLOCK_SIDE)


# 블록 3개 (가운데가 제일 높다)
block(W * 0.140, W * 0.225, W * 0.185, step=0.40)
block(W * 0.405, W * 0.255, W * 0.280, step=0.34)
block(W * 0.700, W * 0.195, W * 0.150)

# 갑판(평평한 판) + 몸통
d.rectangle([X0, DECK, X1, DECK + W * 0.020], fill=HULL_EDGE)
d.polygon([(X0, DECK + W * 0.020), (X1, DECK + W * 0.020),
           (X1 - W * 0.055, HULL_BOT), (X0 + W * 0.055, HULL_BOT)], fill=HULL)


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
stroke(wave_pts(HULL_BOT + W * 0.085, W * 0.026, 1.6), WAVE_B, int(W * 0.036), cap=False)
stroke(wave_pts(HULL_BOT + W * 0.180, W * 0.023, 2.8), WAVE_A, int(W * 0.034), cap=False)
stroke(wave_pts(HULL_BOT + W * 0.268, W * 0.020, 4.0), WAVE_B, int(W * 0.032), cap=False)

out = img.resize((S, S), Image.LANCZOS)
path = "F:/AI/Weather_gathering/claude-weather/web/icon.png"
out.save(path, "PNG", optimize=True)
print("저장: %d 바이트, %dx%d" % (os.path.getsize(path), S, S))
