"""生成"尸体目击"的对照图（README 用）：两个真实引擎帧，只差一件事 —— 目击者的朝向。

这张图要证明的是**代价存在且可读**：

    同样一枪、同样的位置，唯一的区别是"有没有人看见他倒下"。

左边：目击者背对着，尸体没人认领 → 没有警报，这块区域还是安全的。
右边：目击者正面对着 → 红色扩散环 + 头顶 ? + 暴露条变红，这一枪被记账了。

所以两张图必须**同一个裁切框、同一个受害者位置**，只改 witness.facing。
读者一眼就能看出"差别只在朝向"，而不是相信一段文字。

用法：
    SDL_VIDEODRIVER=dummy python scripts/make_body_figure.py
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


def pick_pair(g):
    """找一对"距离 3.5~5 格、视线通畅"的哨兵，用来演目击。

    距离卡在这个区间是为了构图：太近两个人挤成一团，
    太远一个 640×480 的裁切框塞不下。
    """
    w, grid = g.world, g.world.grid
    for victim in w.enemies:
        for witness in w.enemies:
            if witness is victim:
                continue
            d = math.hypot(witness.x - victim.x, witness.y - victim.y)
            if not (3.5 <= d <= 5.0):
                continue
            if _los(grid, victim.x, victim.y, witness.x, witness.y):
                return victim, witness
    raise RuntimeError("地图里找不到合适的目击组合")


def build(face_victim: bool):
    """跑出一帧真实画面。`face_victim` 只控制目击者的朝向，别的一模一样。"""
    g = Game()
    w, p = g.world, g.player
    victim, witness = pick_pair(g)

    ang = math.atan2(victim.y - witness.y, victim.x - witness.x)
    witness.facing = ang if face_victim else ang + math.pi

    # 让两个演员站定（清空巡逻网 = 不移动），画面才可复现
    for e in (victim, witness):
        e.patrol = []

    # 其他哨兵全挪到地图角落：演示图要"看得清因果"，不要一堆人抢戏
    for e in w.enemies:
        if e not in (victim, witness):
            e.x, e.y = 76.5, 56.5
            e.patrol = []

    # 玩家放到出生舱（保证是可走格），离现场足够远，不干扰判定
    p.x, p.y = C.EXFIL_POINT
    g.cam.follow((victim.x + witness.x) / 2, (victim.y + witness.y) / 2, 0.0)

    victim.kill()
    # 推到刚好能看见红环扩散、又还没进入预警倒计时的时刻
    for _ in range(int(0.26 * 60)):
        w.update(1 / 60)

    g._render(1 / 60)
    return g, victim, witness


def snap(g, path):
    pygame.image.save(g.screen, str(path))
    return Image.open(path).convert("RGB")


def main():
    tmp = ROOT / "assets" / "_body_tmp.png"

    # ---- 左：目击者背对（安全）----
    g_a, victim_a, wit_a = build(face_victim=False)
    assert g_a.world.bodies_found == 0, f"背对时不该报警，实际 {g_a.world.bodies_found}"
    a = snap(g_a, tmp)

    # ---- 右：目击者面对（记账）----
    g_b, victim_b, wit_b = build(face_victim=True)
    assert g_b.world.bodies_found == 1, (
        f"面对时应该报警，实际 {g_b.world.bodies_found}"
    )
    b = snap(g_b, tmp)

    # ---- 同一个裁切框：包住尸体 + 目击者 ----
    vx, vy = g_b.cam.world_to_screen(victim_b.x, victim_b.y)
    wx, wy = g_b.cam.world_to_screen(wit_b.x, wit_b.y)
    # margin 要够大：警报环半径 6.5 格（208px），框太紧会让右图看起来像"画错了半个圆"
    margin = 110
    x0, x1 = min(vx, wx) - margin, max(vx, wx) + margin
    y0, y1 = min(vy, wy) - margin, max(vy, wy) + margin
    bw, bh = max(360, x1 - x0), max(270, y1 - y0)
    bw = max(bw, bh * 4 // 3)
    bh = bw * 3 // 4
    bw, bh = min(bw, 460), min(bh, 345)
    ccx, ccy = (vx + wx) // 2, (vy + wy) // 2
    # 两个方向都要夹在屏幕内：框一旦越过窗口边界，裁出来的那一条是纯黑的，
    # 看起来像"地图没画完"，而不是"这里没东西"。
    ccx = max(bw // 2, min(C.WINDOW_W - bw // 2, ccx))
    # 上边界躲开 HUD 信息条，否则两张图顶上都会横着一条文字
    ccy = max(ccy, 84 + bh // 2)
    ccy = min(ccy, C.WINDOW_H - bh // 2)
    box = (ccx - bw // 2, ccy - bh // 2, ccx + bw // 2, ccy + bh // 2)
    a, b = a.crop(box), b.crop(box)

    # ---- 合成 ----
    W, H = PANEL[0] * 2, PANEL[1] + 74
    canvas = Image.new("RGB", (W, H), (16, 18, 22))
    canvas.paste(a.resize(PANEL, Image.NEAREST), (0, 74))
    canvas.paste(b.resize(PANEL, Image.NEAREST), (PANEL[0], 74))

    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 26)
    sub = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
    d.text((22, 14), "① 背对着：没人看见他倒下", font=font, fill=(150, 226, 190))
    d.text((22, 46), "这一枪只有枪声；5 秒后他补位，但这片区域还是安全的",
           font=sub, fill=(150, 158, 170))
    d.text((PANEL[0] + 22, 14), "② 正对着：尸体被记账了", font=font, fill=(255, 128, 116))
    d.text((PANEL[0] + 22, 46), "红环 = 警报半径 6.5 格（超出画面）· ? = 原地警戒 12 秒，不堵门",
           font=sub, fill=(150, 158, 170))
    d.line([(PANEL[0], 0), (PANEL[0], H)], fill=(52, 58, 68), width=2)

    out = ROOT / "assets" / "body_demo.png"
    canvas.save(out)
    tmp.unlink(missing_ok=True)
    print(f"saved {out} {canvas.size}  裁切框 {bw}x{bh}")
    print(f"受害者 ({victim_b.x:.1f},{victim_b.y:.1f})  目击者 ({wit_b.x:.1f},{wit_b.y:.1f})")


if __name__ == "__main__":
    main()
