"""生成"瞄准 → 击倒"的对照图（README 用）：两张真实引擎截图，不是手绘示意。

这张图要讲清楚两件事：
1. **准星亮着 = 这一枪打得中** —— 它是"命中点只有脚底那一小圈"这个 bug 的现场记录；
2. 击倒不是删除：尸体上是 5 秒倒计时，蓝红圈是它**要回来上岗的位置**。

所以两张图必须用同一个裁切框，而且这个框要同时框住"尸体"和"复活落点"——
否则第二张图里的红圈跑到画面外，读者只能相信文字，没法自己看。

场景自动从真实地图里挑：找一个"玩家站得住、中间没有墙、距离 3~6 格"的哨兵，
把玩家放到它对面。这样换地图也不用改坐标。

用法：
    SDL_VIDEODRIVER=dummy python scripts/make_gun_figure.py
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


def _los(grid, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    dx, dy = dx / length, dy / length
    t = 0.0
    while t < length:
        t += 0.08
        if grid.is_wall(int(x0 + dx * t), int(y0 + dy * t)):
            return False
    return True


def setup():
    """挑一对"有视线、距离 3~6 格、且复活点不在尸体原地"的站位。

    最后那个条件是为了图能自证：如果复活点恰好等于被打倒的位置，
    读者会以为"它就是原地刷回来"，而这正好是设计里最想避免的观感。
    """
    g = Game()
    w, p, grid = g.world, g.player, g.world.grid

    cands = []
    for e in w.enemies:
        for ang in range(0, 360, 10):
            for d in (3.0, 4.0, 5.0, 6.0):
                a = math.radians(ang)
                px, py = e.x + math.cos(a) * d, e.y + math.sin(a) * d
                if grid.is_wall(int(px), int(py)):
                    continue
                if _los(grid, px, py, e.x, e.y):
                    cands.append((e, px, py))
                    break

    pick = None
    for e, px, py in cands:
        p.x, p.y = px, py
        spot = w.pick_respawn_point(e)
        if math.hypot(spot[0] - e.x, spot[1] - e.y) < 3.5:
            continue
        # 还要保证"尸体"和"复活落点"都在画面中部：贴到窗口边缘时，
        # 裁切框会被顶出去，人物头顶就会被切掉一半。
        g.cam.follow(px, py, 0.0)
        ex, ey = g.cam.world_to_screen(e.x, e.y)
        rx, ry = g.cam.world_to_screen(*spot)
        if not (250 < ey < C.WINDOW_H - 320 and 220 < ex < C.WINDOW_W - 220):
            continue
        if not (150 < ry < C.WINDOW_H - 150):
            continue
        pick = (e, px, py)
        break
    if pick is None:                      # 兜底：地图改了也不至于直接崩
        pick = cands[0]

    guard, px, py = pick
    p.x, p.y = px, py
    p.facing = math.atan2(guard.y - py, guard.x - px)

    # 其它哨兵挪到地图另一头：演示图要"看得清因果"，不要一堆人抢戏。
    # 目标哨兵的巡逻网**保持原样** —— 复活落点必须是真的算出来的那个点。
    for e in w.enemies:
        if e is guard:
            e.facing = math.atan2(py - e.y, px - e.x)      # 面朝玩家，画面更有张力
        else:
            e.x, e.y = 76.5, 56.5
            e.patrol = []

    g.cam.follow(p.x, p.y, 0.0)
    return g, guard


def snap(g, path):
    g._render(1 / 60)
    pygame.image.save(g.screen, str(path))
    return Image.open(path).convert("RGB")


def main():
    tmp = ROOT / "assets" / "_gun_tmp.png"
    g, guard = setup()
    w, p = g.world, g.player

    # ---- 面板 A：准星亮着（鼠标压在它身上）----
    sx, sy = g.cam.world_to_screen(guard.x, guard.y)
    pygame.mouse.get_pos = lambda: (sx, sy - int(C.WEAPON_AIM_OFFSET * C.TILE_SIZE))
    body = g.cam.screen_to_world(*pygame.mouse.get_pos())
    assert w.aim_target(body) is guard, "这张图的前提是准星确实吸附到了这个哨兵"
    a = snap(g, tmp)

    # ---- 面板 B：开枪 → 倒地，倒计时环跑起来 ----
    assert w.shoot(body) == "enemy", "演示图的前提是这一枪打得中"
    for _ in range(int(3.9 * 60)):          # 3.9s：尸体倒计时 + 复活预警环同框
        w.update(1 / 60)
    b = snap(g, tmp)
    assert guard.respawn_at is not None, "3.9s 时应该已经算出复活落点"

    # ---- 同一个裁切框，包住尸体与复活落点 ----
    cx, cy = g.cam.world_to_screen(guard.x, guard.y)
    rx, ry = g.cam.world_to_screen(*guard.respawn_at)
    margin = 56
    x0, x1 = min(cx, rx) - margin, max(cx, rx) + margin
    y0, y1 = min(cy, ry) - margin, max(cy, ry) + margin
    bw, bh = max(320, x1 - x0), max(240, y1 - y0)
    # 保持 4:3；放大倍数控制在 1.6~2 倍，人看得清又不会糊
    bw = max(bw, bh * 4 // 3)
    bh = bw * 3 // 4
    ccx, ccy = (cx + rx) // 2, (cy + ry) // 2
    box = (ccx - bw // 2, ccy - bh // 2, ccx + bw // 2, ccy + bh // 2)
    a, b = a.crop(box), b.crop(box)

    # ---- 合成：两张图 + 标题条 ----
    W = PANEL[0] * 2
    H = PANEL[1] + 74
    canvas = Image.new("RGB", (W, H), (16, 18, 22))
    canvas.paste(a.resize(PANEL, Image.NEAREST), (0, 74))
    canvas.paste(b.resize(PANEL, Image.NEAREST), (PANEL[0], 74))

    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 26)
    sub = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
    d.text((22, 14), "① 准星亮着 = 这一枪打得中", font=font, fill=(238, 226, 198))
    d.text((22, 46), f"鼠标点在身体上（不是脚底）· 弹药 {p.ammo + 1} 发", font=sub,
           fill=(150, 158, 170))
    d.text((PANEL[0] + 22, 14), "② 击倒 ≠ 删掉：5 秒倒计时", font=font, fill=(232, 120, 110))
    d.text((PANEL[0] + 22, 46), "蓝圈 = 他要回来上岗的位置（自己巡逻网里离你最远的点）",
           font=sub, fill=(150, 158, 170))
    d.line([(PANEL[0], 0), (PANEL[0], H)], fill=(52, 58, 68), width=2)

    out = ROOT / "assets" / "gun_demo.png"
    canvas.save(out)
    tmp.unlink(missing_ok=True)
    print(f"saved {out} {canvas.size}  裁切框 {bw}x{bh}  复活点 {guard.respawn_at}")


if __name__ == "__main__":
    main()
