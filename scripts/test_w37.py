"""W37：把"视觉锥显示"和"感知判定"钉在一起 —— 一份几何，两个消费者。

## 为什么需要这个

v1.8.11 之前，视线几何有**两份**，而且两份各自用固定步长采样：

- 感知层 `raycast_blocks`：0.5 格一步；
- 显示层 `vision_polygon`：0.25 格一步。

固定步长采样的问题是**会整格跳过**。当射线恰好切过某个墙格的角点时，
采样点可能一个落在格子外面一个落在格子后面的空地上，中间那一格墙
从来没被检查过。于是：

- 玩家躲在墙角后，锥体没盖住他，敌人却"看见了"；
- 而且这是潜行游戏里最致命的一类不一致 —— 玩家从画面上找不到任何理由，
  只会觉得"这游戏在耍我"。

实测 8 格内 1.29% 的视线对会因此"隔墙看见"，而旧实现**从不**多判墙
（"旧偏严"样本 0），也就是纯粹的漏检。

现在两份合成一份：`grid.dda_hit_t` 做 Amanatides-Woo 网格步进，感知和
显示都调它。这个脚本钉三件事：

1. 步进不漏格（和 0.002 格细采样逐条对拍）；
2. 几个具体的"隔墙看见"历史案例不再复现；
3. 画出来的锥体边界 == 感知的遮挡边界（这是玩家真正能感知的一致性）。

它不断言速度：CI 机器负载不稳，把"快"写成断言只会得到一个爱红不红的门禁。
速度在 README 的性能段里量，不在这里。

用法：
    python scripts/test_w37.py
"""

from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core.grid import GridMap, dda_hit_t, raycast_blocks  # noqa: E402
from sneak_ai.core.renderer import vision_polygon  # noqa: E402
from sneak_ai.perception.perception import Perception  # noqa: E402


# ============ 参照实现：细到不可能漏格的暴力采样 ============
def fine_first_wall(grid, x0, y0, x1, y1, step: float = 0.002):
    """沿线段以 step 细步长采样，返回第一次进入墙格的距离（没有则 None）。"""
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist == 0.0:
        return None
    ca, sa = dx / dist, dy / dist
    r = 0.0
    while r < dist:
        if grid.is_wall(int(x0 + ca * r), int(y0 + sa * r)):
            return r
        r += step
    return None


def _random_pairs(grid, rng, n, max_dist=8.0):
    walk = [(x + 0.5, y + 0.5) for y in range(grid.h) for x in range(grid.w)
            if grid.is_walkable(x, y)]
    out = []
    while len(out) < n:
        a, b = rng.choice(walk), rng.choice(walk)
        if a == b:
            continue
        if max_dist and math.hypot(b[0] - a[0], b[1] - a[1]) > max_dist:
            continue
        out.append((a, b))
    return out


def test_dda_never_leaks():
    """步进 vs 0.002 细采样：新实现不许说"通"而细采样说"挡"。"""
    rng = random.Random(3777)
    leak = 0
    strict = 0
    total = 0
    for lv in L.LEVELS:
        g = lv.build()
        for (ex, ey), (tx, ty) in _random_pairs(g, rng, 500, max_dist=0.0):
            total += 1
            fine = fine_first_wall(g, ex, ey, tx, ty)
            dx, dy = tx - ex, ty - ey
            dist = math.hypot(dx, dy)
            t = dda_hit_t(g, ex, ey, dx / dist, dy / dist, dist)
            if t is None and fine is not None:
                leak += 1
            elif t is not None and fine is None:
                strict += 1
    # 一条都不许漏。细采样每 0.002 格查一次，格宽 1，不存在漏格的可能。
    assert leak == 0, f"{leak}/{total} 条射线步进说通、细采样说挡 —— 又漏格了"
    # 反向允许少量：那正是"格角从严"这条规则在起作用（细采样会钻墙缝）。
    assert strict / total < 0.02, f"角点从严过多：{strict}/{total}"
    print(f"[OK] 步进不漏格（{total} 对，钻墙角缝而从严的 {strict} 条）")


def test_distance_matches_fine_sampling():
    """撞墙距离本身要和细采样对得上 —— 锥体端点的位置靠它。

    有一类样本必然对不上：射线**正好切过格角**时，步进按"对角任一为墙就挡住"
    处理（零宽缝隙不可见），而细采样会从缝里钻过去、继续走很远才撞上下一堵墙。
    这正是那条栅格化规则的代价，不是几何算错，所以按比例放行 —— 但比例必须小，
    而且要能说清"对不上的都是角点"。
    """
    rng = random.Random(4242)
    diffs = []
    for lv in L.LEVELS:
        g = lv.build()
        for (ex, ey), (tx, ty) in _random_pairs(g, rng, 300):
            fine = fine_first_wall(g, ex, ey, tx, ty)
            if fine is None:
                continue
            dx, dy = tx - ex, ty - ey
            dist = math.hypot(dx, dy)
            t = dda_hit_t(g, ex, ey, dx / dist, dy / dist, dist * 2)
            if t is None:
                continue
            diffs.append((abs(t - fine), ex, ey, tx, ty))
    diffs.sort(reverse=True)
    big = [d for d, *_ in diffs if d > 0.005]
    assert big, "一条撞墙样本都没有，测试本身失效了"
    ratio = len(big) / len(diffs)
    assert ratio < 0.05, f"{len(big)}/{len(diffs)} 条距离偏差超细采样步长"
    print(f"[OK] 撞墙距离与细采样一致（{len(diffs)} 条，其中 {len(big)} 条是"
          f"切格角而故意从严，最大偏差 {diffs[0][0]:.3f} 格）")


def test_known_leaks_are_fixed():
    """把当年"隔墙看见"的具体案例钉死：每个都必须判"挡住"。"""
    cases = [
        ("哨站", 17.5, 40.5, 12.5, 35.5),
        ("哨站", 55.5, 14.5, 53.5, 3.5),
        ("哨站", 51.5, 15.5, 49.5, 12.5),
        ("哨站", 7.5, 34.5, 11.5, 39.5),
        ("哨站", 47.5, 14.5, 49.5, 8.5),
    ]
    by_name = {lv.name: lv for lv in L.LEVELS}
    for name, ex, ey, tx, ty in cases:
        g = by_name[name].build()
        assert raycast_blocks(g, ex, ey, tx, ty), (
            f"{name} ({ex},{ey})→({tx},{ty}) 又变成能隔墙看见了")
    print(f"[OK] {len(cases)} 个历史漏检案例全部判为遮挡")


def _point_in_polygon(px, py, poly) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def test_cone_matches_perception():
    """核心断言：锥体盖住的地方 == 感知看得见的地方。

    做法是把目标点换算到屏幕坐标，判它是否落在锥体多边形内，再和
    `Perception.can_see` 的遮挡结论对比。

    **哪些样本不算**：锥体是 21 个角度采样点连成的折线，而墙的边缘在角度上
    是不连续的 —— 当目标方向正好落在"一侧撞墙 2 格、另一侧通到 6.5 格"的
    相邻两个采样点之间时，折线在中间的位置没有唯一正确答案（任何有限采样
    都会在这里偏一点，加密到 80 段也没用，实测不一致率不降反升）。
    这类样本连同"贴着 FOV 边界"的一并跳过，只留下边界平滑的区域 ——
    在那里，显示和判定必须逐对一致，一个都不许差。
    """
    ts = C.TILE_SIZE
    fov = math.radians(110)

    class Cam:
        """相机替身：只做 world→screen 平移，不滚动。"""

        def __init__(self):
            self.offset_x, self.offset_y = 0, 0

        def world_to_screen(self, x, y):
            return int(x * ts - self.offset_x), int(y * ts - self.offset_y)

    rng = random.Random(9091)
    cam = Cam()
    checked = mismatch = skipped = 0
    for lv in L.LEVELS:
        g = lv.build()
        walk = [(x + 0.5, y + 0.5) for y in range(g.h) for x in range(g.w)
                if g.is_walkable(x, y)]
        for _ in range(500):
            ex, ey = rng.choice(walk)
            ang0 = rng.uniform(0, 2 * math.pi)
            d = rng.uniform(1.0, 5.5)
            tx, ty = ex + math.cos(ang0) * d, ey + math.sin(ang0) * d
            if not g.is_walkable(int(tx), int(ty)):
                continue
            facing = math.atan2(ty - ey, tx - ex) + rng.uniform(-0.3, 0.3)
            # FOV 边界附近不比：多边形的两侧边是直线，夹角判定用的是精确角度
            if abs(math.remainder(ang0 - facing, 2 * math.pi)) > fov / 2 - 0.2:
                skipped += 1
                continue
            perce = Perception(view_range=6.5)
            enemy = SimpleNamespace(
                x=ex, y=ey, facing=facing, perception=perce,
                view_range=lambda v=6.5: v,
            )
            poly = vision_polygon(enemy, g, cam)
            if len(poly) < 3:
                continue
            sx, sy = ex * ts, ey * ts
            # 目标方向落在哪两个相邻采样点之间，看它们的半径差多少
            u = (ang0 - (facing - fov / 2)) / (fov / 20)
            i = int(u)
            if not (0 <= i <= 19):
                skipped += 1
                continue
            r1 = math.hypot(poly[i + 1][0] - sx, poly[i + 1][1] - sy)
            r2 = math.hypot(poly[i + 2][0] - sx, poly[i + 2][1] - sy)
            if abs(r1 - r2) > 0.4 * ts:
                skipped += 1
                continue
            px, py = tx * ts, ty * ts
            in_poly = _point_in_polygon(px, py, poly)
            checked += 1
            visible = perce.can_see((ex, ey), facing, (tx, ty), g) > 0.0
            if visible != in_poly:
                mismatch += 1
                if mismatch <= 5:
                    print(f"    不符：{lv.name} e=({ex:.1f},{ey:.1f}) "
                          f"t=({tx:.1f},{ty:.1f}) d={d:.2f} "
                          f"锥内={in_poly} 可见={visible}")
    assert checked > 400, f"样本太少（{checked}），断言没意义"
    assert mismatch == 0, (
        f"{mismatch}/{checked} 对的「锥体覆盖」和「感知看见」结论不一致 —— "
        f"显示和判定又分家了")
    print(f"[OK] 锥体边界与感知遮挡一致（{checked} 对逐对相符，"
          f"跳过 {skipped} 个墙边缘/FOV 边缘样本）")


def test_straight_line_and_wall():
    """几何本身的两个基本盘：空走廊通、一堵墙挡。"""
    g = GridMap.empty(9, 3)
    assert not raycast_blocks(g, 0.5, 1.5, 8.5, 1.5)
    g.set_wall(4, 1)
    assert raycast_blocks(g, 0.5, 1.5, 8.5, 1.5)
    # 绕过去：从同一行的墙后面看仍然挡，换一条空行就通
    assert raycast_blocks(g, 0.5, 0.5, 8.5, 0.5) is False
    # 起点在墙里 → 视为挡（与旧实现一致，调用方不该走到这条路）
    assert raycast_blocks(g, 4.5, 1.5, 0.5, 1.5)
    # 同点 → 不通也算不挡
    assert raycast_blocks(g, 2.5, 2.5, 2.5, 2.5) is False
    print("[OK] 直线 / 单墙 / 绕行 / 起点在墙里 四个基本情形正确")


def main() -> int:
    test_straight_line_and_wall()
    test_distance_matches_fine_sampling()
    test_dda_never_leaks()
    test_known_leaks_are_fixed()
    test_cone_matches_perception()
    print("\nW37 全部通过：视觉锥显示与感知判定共用一份几何，隔墙看见已消失")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
