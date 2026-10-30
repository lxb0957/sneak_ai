"""生成投石诱敌的对照图（README 用）：两张真实引擎截图，不是手绘示意。

场景取自真实地图的左上房间：
    玩家在 (6.5, 17.5)，哨兵在 (11.5, 17.5) 面朝东 —— 正好背对玩家。
    第一张：石子刚出手（玩家在走路，脚下是 5.8 格的噪音圈）。
    第二张：石子落地 0.75 秒后，哨兵转身去查，玩家改成蹲行从它背后过去。

用法：
    python scripts/make_decoy_figure.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame

pygame.init()

from PIL import Image, ImageDraw, ImageFont

from sneak_ai.__main__ import Game
from sneak_ai import constants as C

PANEL = (640, 480)


def setup():
    g = Game()
    w, p = g.world, g.player
    p.x, p.y = 6.5, 17.5
    p.facing = 0.0
    # 只留一个哨兵，其它人挪到地图另一头：演示图要的是"看得清因果"
    for i, e in enumerate(w.enemies):
        if i == 0:
            e.x, e.y = 11.5, 17.5
            e.facing = 0.0
            e.patrol = []
            e._dwell = 0.0
        else:
            e.x, e.y = 76.5, 56.5
            e.patrol = []
    g.cam.follow(p.x, p.y)
    return g


def step(g, frames, keys=None):
    p = g.world.player
    p._keys = set(keys or ())
    for _ in range(frames):
        g.world.update(1 / 60)
        g.cam.follow(p.x, p.y)


def shot(g, path):
    g._render(1 / 60)
    pygame.image.save(g.screen, str(path))
    return Image.open(path).convert("RGB")


def main():
    tmp = ROOT / "assets" / "_decoy_a.png"
    g = setup()
    w, p = g.world, g.player

    # ---- 面板 A：扔出去（第 7 帧，石子还在空中）----
    p._keys = {pygame.K_d}          # 在走路 → 噪音圈 5.8 格
    w.throw_stone((10.5, 10.5))
    step(g, 9, keys={pygame.K_d})
    a = shot(g, tmp)

    # ---- 面板 B：哨兵被引走，玩家改蹲行 ----
    step(g, 45, keys={pygame.K_d, pygame.K_LSHIFT})
    b = shot(g, tmp)

    # ---- 合成：两张图 + 标题条 ----
    W = PANEL[0] * 2
    H = PANEL[1] + 74
    canvas = Image.new("RGB", (W, H), (16, 18, 22))
    canvas.paste(a.resize(PANEL, Image.NEAREST), (0, 74))
    canvas.paste(b.resize(PANEL, Image.NEAREST), (PANEL[0], 74))

    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 26)
    sub = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
    d.text((22, 14), "① 朝空地扔一颗石子", font=font, fill=(238, 226, 198))
    d.text((22, 46), "Q 朝朝向扔 / 右键瞄准落点 · 石子只剩 2 颗", font=sub, fill=(150, 158, 170))
    d.text((PANEL[0] + 22, 14), "② 它去查声音，位置空了", font=font, fill=(160, 220, 190))
    d.text((PANEL[0] + 22, 46), "玩家改蹲行（噪音圈缩到 3.6 格）从背后过去", font=sub,
           fill=(150, 158, 170))
    d.line([(PANEL[0], 0), (PANEL[0], H)], fill=(52, 58, 68), width=2)

    out = ROOT / "assets" / "decoy_demo.png"
    canvas.save(out)
    tmp.unlink(missing_ok=True)
    print("saved", out, canvas.size)


if __name__ == "__main__":
    main()
