"""W36：性能优化的**等价性**钉子 —— 快可以，但不能快出另一个游戏。

## 为什么需要这个

v1.8.10 做了四处"只改实现、不改行为"的提速：

1. `GridMap.neighbors8()`：把"谁能走到谁"缓存成邻接表，流场/A* 不再逐格
   调 `is_walkable`；
2. `FlowField._build`：改用上面那份邻接表；
3. `circle_hits_wall`：碰撞几何从"玩家 / 敌人各一份"合并成一份，候选格从
   固定 3×3 改成"按半径算"—— 两份实现早就悄悄分叉了（敌人有 1e-6 容差，
   玩家没有），容差现在是显式参数 `inset`；
4. `add_cover`：空地总数只数一次，可达性用 `reachable_count`（不建集合）。

这四处的共同点是：**改错了不会崩、不会报错、不会让任何现有断言变红**，
只会让某只哨兵在某条走廊里的走位偏一格 —— 然后 README 里所有历史数字
（胜率、最短路线、难度表）静默失效。比崩溃难查一百倍。

所以这里不做"跑起来没异常"这种检查，而是**逐值对拍**：

- 邻接表 vs 现算的暴力枚举（两种 corner_cut 口径），每个格子比；
- 流场 vs 旧实现的直接翻译（同一份 BFS 逐行照抄），积分场/方向场逐元素比；
- 碰撞判定 vs 旧的 3×3 扫描，随机坐标 + 整数/角点边界，**两种容差口径**都比；
- 可达格数 vs `len(flood_floor(...))`，五关每关比；
- 缓存的失效：`set_wall` / `set_floor` 之后必须重算，**而且重算结果要对**。

用法：
    python scripts/test_w36.py
"""

from __future__ import annotations

import math
import os
import random
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core.grid import FLOOR, NEI8, WALL, GridMap  # noqa: E402
from sneak_ai.entity.enemy import Enemy  # noqa: E402
from sneak_ai.entity.player import Player  # noqa: E402
from sneak_ai.pathing.flowfield import FlowField  # noqa: E402


# ============ 参照实现（旧代码的直接翻译，故意写得笨） ============
def _ref_neighbors(grid, corner_cut):
    """不用缓存，逐格现算邻接表。"""
    out = {}
    for y in range(grid.h):
        for x in range(grid.w):
            if grid.is_wall(x, y):
                continue
            lst = []
            for dx, dy in NEI8:
                nx, ny = x + dx, y + dy
                if grid.is_wall(nx, ny):
                    continue
                if not corner_cut and dx and dy:
                    if grid.is_wall(x + dx, y) or grid.is_wall(x, y + dy):
                        continue
                lst.append((ny * grid.w + nx, dx, dy))
            out[y * grid.w + x] = tuple(lst)
    return out


def _ref_flowfield(grid, goal):
    """旧版 FlowField 的逻辑，一字不改地重写一遍。"""
    w, h = grid.w, grid.h
    gx, gy = goal
    if not grid.is_walkable(gx, gy):
        return ([-1] * (w * h), [0.0] * (w * h), [0.0] * (w * h))
    INF = 1 << 30
    dist = [INF] * (w * h)
    start = gy * w + gx
    dist[start] = 0
    dq = deque([start])
    dirs = list(NEI8)
    while dq:
        idx = dq.popleft()
        cx, cy = idx % w, idx // w
        d = dist[idx]
        for dx, dy in dirs:
            nx, ny = cx + dx, cy + dy
            if not grid.is_walkable(nx, ny):
                continue
            nidx = ny * w + nx
            nd = d + (1000 if dx == 0 or dy == 0 else 1414)
            if nd < dist[nidx]:
                dist[nidx] = nd
                dq.append(nidx)
    vx = [0.0] * (w * h)
    vy = [0.0] * (w * h)
    for idx in range(w * h):
        if dist[idx] >= INF:
            continue
        cx, cy = idx % w, idx // w
        bd = dist[idx]
        bdx = bdy = 0
        for dx, dy in dirs:
            nx, ny = cx + dx, cy + dy
            if not grid.is_walkable(nx, ny):
                continue
            nd = dist[ny * w + nx]
            if nd < bd:
                bd, bdx, bdy = nd, dx, dy
        if bdx or bdy:
            ln = math.hypot(bdx, bdy)
            vx[idx], vy[idx] = bdx / ln, bdy / ln
    return (dist, vx, vy)


def _ref_collides(x, y, r, grid, inset=0.0):
    """旧版 3×3 扫描（`inset` 对应两份历史实现的差别）。"""
    cx, cy = int(x), int(y)
    for tx in (cx - 1, cx, cx + 1):
        for ty in (cy - 1, cy, cy + 1):
            if not grid.is_wall(tx, ty):
                continue
            px = max(tx, min(x, tx + 1))
            py = max(ty, min(y, ty + 1))
            dx, dy = x - px, y - py
            if dx * dx + dy * dy < r * r - inset:
                return True
    return False


# ============ 测试 ============
def test_terrain_hash_pinned():
    """优化不许动地图。这五个哈希是 v1.8.9 发布时的地形，一个字节都不许变。"""
    want = {
        0: "62ccb965ce1b9384", 1: "326b932daeb31acd", 2: "5b9f320f150e153f",
        3: "6d9c4e47d33351e6", 4: "341bcf0b342e985b",
    }
    import hashlib
    for i, lv in enumerate(L.LEVELS):
        m = lv.build()
        raw = bytes(v for row in m.tiles for v in row)
        got = hashlib.sha256(raw).hexdigest()[:16]
        assert got == want[i], f"{lv.name} 地形变了：{got} != {want[i]}"
    print("[OK] 五关地形哈希与 v1.8.9 一致（提速没有顺带改图）")


def test_neighbors_cache_matches_brute_force():
    """邻接表 = 现算的暴力枚举，两种口径逐格比。"""
    for lv in L.LEVELS:
        g = lv.build()
        for cc in (True, False):
            fast = g.neighbors8(corner_cut=cc)
            ref = _ref_neighbors(g, cc)
            for idx, cells in ref.items():
                assert fast[idx] == cells, (
                    f"{lv.name} corner_cut={cc} 格 {idx % g.w},{idx // g.w} "
                    f"邻接表不一致：{fast[idx]} != {cells}")
            # 墙格的表必须为空
            for y in range(g.h):
                for x in range(g.w):
                    if g.is_wall(x, y):
                        assert fast[y * g.w + x] == (), "墙格却挂了边"
    print("[OK] 邻接表与暴力枚举逐格一致（两种 corner_cut 口径）")


def test_neighbors_cache_invalidates():
    """set_wall / set_floor 之后必须重算 —— 缓存不能变成"第二个真值"。"""
    g = GridMap(12, 10, [[FLOOR] * 12 for _ in range(10)])
    before = g.neighbors8(corner_cut=False)
    assert before[5 * 12 + 5], "空格居然没有边"

    g.set_wall(6, 5)
    after = g.neighbors8(corner_cut=False)
    assert after[5 * 12 + 5] != before[5 * 12 + 5], "加了墙，邻接表没变 —— 缓存没失效"
    assert all(nidx != 5 * 12 + 6 for nidx, _, _ in after[5 * 12 + 5]), \
        "新加的墙还在邻接表里"

    g.set_floor(6, 5)
    back = g.neighbors8(corner_cut=False)
    assert back[5 * 12 + 5] == before[5 * 12 + 5], "拆了墙，邻接表没恢复"

    # 斜穿墙角的口径也要跟着变：在角上补一堵墙
    g2 = GridMap(8, 8, [[FLOOR] * 8 for _ in range(8)])
    g2.set_wall(5, 4)          # (4,4) → (5,5) 的斜边靠它
    free = g2.neighbors8(corner_cut=True)
    strict = g2.neighbors8(corner_cut=False)
    idx = 4 * 8 + 4
    assert any(n == 5 * 8 + 5 for n, _, _ in free[idx]), "corner_cut=True 应该允许斜穿"
    assert not any(n == 5 * 8 + 5 for n, _, _ in strict[idx]), \
        "corner_cut=False 不许斜穿墙角"
    print("[OK] 缓存随 set_wall/set_floor 失效并重算正确")


def test_flowfield_matches_reference():
    """流场逐元素对拍：积分场和两个方向场分量都必须完全相同。"""
    for lv in L.LEVELS:
        g = lv.build()
        goals = [(int(lv.exfil[0]), int(lv.exfil[1])),
                 (int(lv.spawn[0]), int(lv.spawn[1]))]
        rnd = random.Random(77)
        while len(goals) < 5:
            x, y = rnd.randrange(1, g.w - 1), rnd.randrange(1, g.h - 1)
            if g.is_walkable(x, y):
                goals.append((x, y))
        for goal in goals:
            ff = FlowField(g, goal)
            rd, rvx, rvy = _ref_flowfield(g, goal)
            assert ff._integration == rd, f"{lv.name} {goal} 积分场不一致"
            assert ff._vec_x == rvx and ff._vec_y == rvy, f"{lv.name} {goal} 方向场不一致"
    print("[OK] 流场 25 组（五关 × 五目标）与旧实现逐元素相同")


def test_collides_matches_reference():
    """碰撞判定：随机坐标 + 整数/角点边界，**两种容差口径**逐个对拍。

    玩家和敌人共用 `circle_hits_wall`，但 `inset` 不同（玩家 0、敌人 1e-6）。
    这两个数不是随便给的：敌人那份是"防卡角"的修正，玩家那份是历史手感。
    合并实现时最怕的就是"顺手统一" —— 统一了就没有卡角修正，或者玩家会
    突然穿得过以前穿不过的缝。所以两种口径都要钉。
    """
    n = 0
    for lv in L.LEVELS:
        g = lv.build()
        rnd = random.Random(4)
        for _ in range(4000):
            x = rnd.uniform(-1, g.w + 1)
            y = rnd.uniform(-1, g.h + 1)
            r = rnd.choice([0.02, 0.1, 0.28, 0.3, 0.35, 0.5, 0.7071, 0.99])
            n += 1
            assert Enemy._collides(x, y, r, g) == _ref_collides(x, y, r, g, 1e-6), \
                f"{lv.name} ({x:.4f},{y:.4f}) r={r} 敌人口径不一致"
            assert Player._collides(x, y, r, g) == _ref_collides(x, y, r, g, 0.0), \
                f"{lv.name} ({x:.4f},{y:.4f}) r={r} 玩家口径不一致"
        # 边界：整数坐标、格角、极小偏移 —— 这些才是"差一点就判错"的地方
        for x in range(0, min(g.w, 10)):
            for y in range(0, min(g.h, 10)):
                for r in (0.0, 0.3, 0.5, 0.7071067811865476):
                    for ox in (0.0, 1e-9, 0.5, 1 - 1e-9):
                        n += 1
                        assert Enemy._collides(x + ox, y, r, g) == \
                            _ref_collides(x + ox, y, r, g, 1e-6), \
                            f"敌人边界不一致 ({x + ox},{y}) r={r}"
                        assert Player._collides(x + ox, y, r, g) == \
                            _ref_collides(x + ox, y, r, g, 0.0), \
                            f"玩家边界不一致 ({x + ox},{y}) r={r}"
    print(f"[OK] 碰撞判定 {n} 组 × 两种容差口径，与旧 3×3 扫描完全一致")


def test_reachable_count_matches_flood():
    """可达格数必须等于 flood_floor 的集合大小（含"起点在墙里"的边界）。"""
    for lv in L.LEVELS:
        g = lv.build()
        sx, sy = int(lv.spawn[0]), int(lv.spawn[1])
        assert L.reachable_count(g, sx, sy) == len(L.flood_floor(g, sx, sy)), \
            f"{lv.name} 可达格数对不上"
    # 起点是墙 → 0；1x1 的空图 → 1
    tiny = GridMap(3, 3, [[WALL] * 3 for _ in range(3)])
    assert L.reachable_count(tiny, 1, 1) == 0
    tiny.set_floor(1, 1)
    assert L.reachable_count(tiny, 1, 1) == 1
    print("[OK] 可达格数与 flood_floor 一致（五个关卡 + 两个边界）")


def main() -> int:
    test_terrain_hash_pinned()
    test_neighbors_cache_matches_brute_force()
    test_neighbors_cache_invalidates()
    test_flowfield_matches_reference()
    test_collides_matches_reference()
    test_reachable_count_matches_flood()
    print("\nW36 全部通过：四处提速逐值对拍，没有一个值变了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
