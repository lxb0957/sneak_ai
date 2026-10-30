"""生成"沿途补给 → 护盾生效"的对照图（README 用）：两张真实引擎截图。

这张图要讲清楚三件事：
1. 补给是**路上顺手拿的**，不是开局白送 —— 所以一屏里同时出现三种点位，
   而且它们分散在房间的不同角落（要绕一下才拿得到）；
2. 三种点位靠**形状**区分，不靠颜色 —— 暖色地板上黄蓝都容易糊；
3. 护盾必须是**看得见的资源**：拿到之后角色身上有光圈、HUD 右下角那格亮起来。

场景取自真实关卡的 G6 南机房片区：那里天然同时有弹药包、石子堆、护盾，
一屏装得下，不用摆拍。哨兵挪到地图另一头，是为了让"资源"而不是"人"抢戏。

用法：
    SDL_VIDEODRIVER=dummy python scripts/make_pickup_figure.py
"""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame

pygame.init()

from PIL import Image, ImageDraw, ImageFont

from sneak_ai import constants as C
from sneak_ai.__main__ import Game

PANEL = (640, 480)
# G6 片区一屏能同时框住的三处真实点位：弹药 / 石子 / 护盾
STAGE = (38.5, 41.5)


def setup():
    g = Game()
    w, p = g.world, g.player

    # 哨兵挪到地图另一头：这张图讲的是"资源怎么读"，不要让追兵抢戏
    for e in w.enemies:
        e.x, e.y = 76.5, 56.5
        e.patrol = []

    p.x, p.y = STAGE
    p.facing = -math.pi / 2
    g.cam.follow(p.x, p.y, 0.0)
    return g


def snap(g, path):
    g._render(1 / 60)
    pygame.image.save(g.screen, str(path))
    return Image.open(path).convert("RGB")


def main():
    tmp = ROOT / "assets" / "_pickup_tmp.png"
    g = setup()
    w, p = g.world, g.player

    # 护盾必须选**离舞台最近**的那个（G6 南角），不能随便挑 ——
    # 挑到地图另一头的护盾，两张图的裁切框就会各自对准不同的地方。
    shield = min((it for it in w.pickups if it["kind"] == "shield"),
                 key=lambda it: math.hypot(it["x"] - STAGE[0], it["y"] - STAGE[1]))

    def on_screen(it):
        sx, sy = g.cam.world_to_screen(it["x"], it["y"])
        return 90 < sx < C.WINDOW_W - 90 and 120 < sy < C.WINDOW_H - 120

    shown = {it["kind"] for it in w.pickups if on_screen(it)}
    assert {"ammo", "stone", "shield"} <= shown, (
        f"这一屏必须真的同时出现三种补给，实际只有 {shown}")
    # 只在**屏内**的点位里挑代表：列表里排前面的点位可能在几百像素外，
    # 质心被拽出屏幕，裁切框就会整框切到画面外（PIL 越界填黑）。
    by_kind = {}
    for it in w.pickups:
        if on_screen(it) and it["kind"] not in by_kind:
            by_kind[it["kind"]] = it
    # 裁切中心取三种点位的质心：保证同一张图里三种都看得见
    anchor = (sum(it["x"] for it in by_kind.values()) / 3,
              sum(it["y"] for it in by_kind.values()) / 3)

    # ---- 面板 A：三种补给同屏，玩家还没拿 ----
    a = snap(g, tmp)
    sa = g.cam.world_to_screen(*anchor)

    # ---- 面板 B：走上护盾点 → 身上光圈 + HUD 那格亮起 ----
    p.x, p.y = shield["x"], shield["y"]
    w.update(1 / 60)
    assert p.shield == 1, "演示图的前提是护盾真的被捡起来了"
    assert shield["taken"]
    for _ in range(12):          # 让呼吸光圈动起来一点，但别走太远
        w.update(1 / 60)
    b = snap(g, tmp)
    sb = g.cam.world_to_screen(*anchor)

    # ---- 两张图各自以护盾点为心裁切：相机跟着玩家走了，
    #      用同一个绝对坐标框会让第二张图切到别的地方去 ----
    # 框必须盖住全部三个点位 + 玩家：护盾在最南（y=45.5）、石子最北（y=33.5），
    # 纵向跨 12 格 —— 470x352 只有 11 格高，护盾六边形必然被切出画面。
    bw, bh = 640, 480
    def box_for(c):
        return (c[0] - bw // 2, c[1] - bh // 2,
                c[0] + bw // 2, c[1] + bh // 2)
    a, b = a.crop(box_for(sa)), b.crop(box_for(sb))

    W, H = PANEL[0] * 2, PANEL[1] + 74
    canvas = Image.new("RGB", (W, H), (16, 18, 22))
    canvas.paste(a.resize(PANEL, Image.NEAREST), (0, 74))
    canvas.paste(b.resize(PANEL, Image.NEAREST), (PANEL[0], 74))

    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 26)
    sub = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
    d.text((22, 14), "① 沿途补给：形状区分类型", font=font, fill=(238, 226, 198))
    d.text((22, 46), "子弹=弹药 / 三粒圆=石子 / 六边形=护盾（都摆在角落，要绕一下）",
           font=sub, fill=(150, 158, 170))
    d.text((PANEL[0] + 22, 14), "② 护盾必须是看得见的资源", font=font,
           fill=(120, 220, 255))
    d.text((PANEL[0] + 22, 46), "捡到后身上一直亮着蓝色光圈：被抓住时它会挡一次，碰它的追兵倒下",
           font=sub, fill=(150, 158, 170))
    d.line([(PANEL[0], 0), (PANEL[0], H)], fill=(52, 58, 68), width=2)

    out = ROOT / "assets" / "pickup_demo.png"
    canvas.save(out)
    tmp.unlink(missing_ok=True)
    print(f"saved {out} {canvas.size}  护盾点 ({shield['x']:.1f},{shield['y']:.1f})")


if __name__ == "__main__":
    main()
