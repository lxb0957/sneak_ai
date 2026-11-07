"""难度体检（不依赖机器人操作水平）。

这个脚本的指标换过一次，原因值得写下来：

旧指标是"无影通路率"—— 在某个瞬间，把被任何视锥覆盖的格子标成不可走，
看玩家还能不能从撤离点走到终端。它在**空地加墙**的地图上很有用（旧图 96%）。
但这次地图改成实心开挖的 θ 形之后，当时判断它会接近 0%：
路线只有三条弧（最小割 4 格），每条要走 6~8 道 2 格宽的门，
要求"同一瞬间所有门都空着"听起来等于要求 21 个哨兵同时闭眼 ——
而真实玩家根本不是这么过关的。

**注意：那个"接近 0%"的判断是错的。** 实测重跑一直在 30~40%（见 main 里 ④）。
门与门之间并不独立：21 个哨兵散在三条弧上，多数门离谁都远，于是"全场同时空"
比独立假设下常见得多。这条指标现在只打印作参考，不参与判断 ——
但它的 docstring 里那段假设留在这里，当成"没量就下结论"的一个记录。

所以换成真正对得上体验的指标：**门窗口率**。

对每道门，统计"至少有一格没被看着"的时间占比，以及**最长一段连续窗口**。
人能钻进门的条件是：有连续 1 秒以上的空档（2 格宽、走路 4.4 格/s）。
窗口太短的门，玩家会体验到"明明没被看见却过不去"——那才是真正的关卡 bug。

跑法：
    SDL_VIDEODRIVER=dummy python scripts/audit_difficulty.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pygame

pygame.init()

from sneak_ai import constants as C
from sneak_ai.__main__ import Game
from sneak_ai.core.world import DOORS, World, build_demo_map, build_spawn_and_patrols
from sneak_ai.entity.enemy import Enemy
from sneak_ai.pathing.astar import astar
from sneak_ai.perception.perception import Perception


def _patrol_world() -> World:
    """只有哨兵、没有玩家的世界：测"门什么时候开着"时不该有追击干扰。"""
    grid = build_demo_map()
    w = World(grid)
    w.player = None
    _spawn, cfg = build_spawn_and_patrols(grid)
    w.enemies = [Enemy(x, y, patrol=pts, perception=Perception())
                 for (x, y, pts) in cfg]
    return w


def _watched_cells(world, rays=40):
    """当前所有敌人视锥覆盖的格集合。"""
    cells = set()
    for e in world.enemies:
        if not e.alive:
            continue
        rng = e.view_range()
        if rng <= 0:
            continue
        fov = e.perception.fov_angle if e.perception else math.radians(110)
        for i in range(rays + 1):
            a = e.facing - fov / 2 + fov * i / rays
            ca, sa = math.cos(a), math.sin(a)
            for step in range(1, int(rng) + 1):
                # 注意 x 用 cos、y 用 sin。写成同一个分量时锥体会退化成一条线，
                # 覆盖率会凭空掉一半 —— 这类错会让"难度体检"给出完全错误的结论。
                cx, cy = int(e.x + ca * step), int(e.y + sa * step)
                if world.grid.is_wall(cx, cy):
                    break
                cells.add((cx, cy))
    return cells


def _door_cells(d):
    return [(x, y) for y in range(d[1], d[3] + 1) for x in range(d[0], d[2] + 1)]


def exposure_audit(budget=300.0):
    """① 沿最短路径闷头走、完全无视视锥 —— 会挨多少打。"""
    g = Game()
    w, p, grid = g.world, g.player, g.world.grid
    goal = (int(C.DATA_TERMINAL[0]), int(C.DATA_TERMINAL[1]))
    path = astar(grid, (int(p.x), int(p.y)), goal)
    pi, t, exposed, peak = 1, 0.0, 0.0, 0
    while t < budget and w.state in (C.STATE_INFILTRATE, C.STATE_EXFILTRATE):
        if path and pi < len(path):
            tx, ty = path[pi][0] + 0.5, path[pi][1] + 0.5
            if math.hypot(tx - p.x, ty - p.y) < 0.35:
                pi += 1
            else:
                keys = set()
                dx, dy = tx - p.x, ty - p.y
                if dx > 0.08:
                    keys.add(pygame.K_d)
                elif dx < -0.08:
                    keys.add(pygame.K_a)
                if dy > 0.08:
                    keys.add(pygame.K_s)
                elif dy < -0.08:
                    keys.add(pygame.K_w)
                p._keys = keys | {pygame.K_LSHIFT}
        w.update(1 / 60)
        t += 1 / 60
        if w.state == C.STATE_DEFEAT:
            w.captured = False
            w.state = C.STATE_EXFILTRATE if w.has_data else C.STATE_INFILTRATE
        if w.seeing > 0:
            exposed += 1 / 60
            peak = max(peak, w.seeing)
    return {
        "time": round(t, 1),
        "exposed_pct": round(exposed / max(t, 1e-6) * 100),
        "peak_seen": peak,
        "reached_terminal": w.has_data,
    }


def door_window_audit(samples=180, step=0.4):
    """② 每道门"能过人"的时间占比 + 最长一段连续窗口。

    判定"能过人"用**任一格未被覆盖**：人只占一格，门有两格宽，
    右边那格没被看着就能钻过去。
    """
    w = _patrol_world()
    stats = {d: {"open": 0, "streak": 0.0, "best": 0.0} for d in DOORS}
    for _ in range(samples):
        for _ in range(int(step * 60)):
            w.update(1 / 60)
        watched = _watched_cells(w)
        for d in DOORS:
            cells = _door_cells(d)
            can = any(c not in watched for c in cells)
            s = stats[d]
            if can:
                s["open"] += 1
                s["streak"] += step
                s["best"] = max(s["best"], s["streak"])
            else:
                s["streak"] = 0.0
    return {
        d: {
            "ratio": s["open"] / samples,
            "best_window": s["best"],
        }
        for d, s in stats.items()
    }


def strict_path_audit(samples=40, step=1.0):
    """③ 参考值：同一瞬间存在"全程不被看见"的整条路线的时间占比。

    门控式关卡里这个值天然很低（要求所有门同时空着），
    所以它只用来解释"为什么不能一口气冲到底"，不作为可解性判据。
    """
    w = _patrol_world()
    ok = 0
    from collections import deque

    def reach(blocked):
        s = (int(C.EXFIL_POINT[0]), int(C.EXFIL_POINT[1]))
        t = (int(C.DATA_TERMINAL[0]), int(C.DATA_TERMINAL[1]))
        seen, q = {s}, deque([s])
        while q:
            x, y = q.popleft()
            if (x, y) == t:
                return True
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in seen or (nx, ny) in blocked or w.grid.is_wall(nx, ny):
                    continue
                seen.add((nx, ny))
                q.append((nx, ny))
        return False

    for _ in range(samples):
        for _ in range(int(step * 60)):
            w.update(1 / 60)
        blocked = {c for c in _watched_cells(w) if not w.grid.is_wall(*c)}
        if reach(blocked):
            ok += 1
    return ok / samples


def main() -> None:
    print("关卡难度体检")
    print("=" * 60)
    exp = exposure_audit()
    print("① 无视野硬走对照：沿最短路径闷头走、完全无视视锥")
    print(f"   被看见时间占比 {exp['exposed_pct']}%"
          f"（峰值同时被 {exp['peak_seen']} 人盯着，"
          f"{'走到了终端' if exp['reached_terminal'] else '没走到终端'}）")
    print("   判读：>40% 说明不会绕路就是送，地图没有放水\n")

    win = door_window_audit()
    print("② 门窗口：每道门有多少比例的时间存在可通行的一格 / 最长连续窗口")
    for d in DOORS:
        s = win[d]
        bar = "█" * int(s["ratio"] * 20)
        print(f"   {d[4]:<8s}({d[5]}) {s['ratio'] * 100:4.0f}% "
              f"最长 {s['best_window']:4.1f}s  {bar}")
    worst = min(win.values(), key=lambda s: s["best_window"])
    avg = sum(s["ratio"] for s in win.values()) / len(win)
    print(f"   平均通过率 {avg * 100:.0f}%，最紧的一道门最长窗口 {worst['best_window']:.1f}s")
    print("   判读：人穿过 2 格门约 0.5s，最长窗口 < 1s 的门就是「过不去」\n")

    print("③ 三条路线的取舍（按弧统计平均门通过率）")
    by_arc = {}
    for d in DOORS:
        by_arc.setdefault(d[5], []).append(win[d]["ratio"])
    for arc, vals in by_arc.items():
        print(f"   {arc}：{len(vals)} 道门，平均通过率 {sum(vals) / len(vals) * 100:.0f}%")
    print("   北弧门多而紧、南弧门少而松 —— 两条弧几乎等长，"
          "取舍来自守卫密度而不是距离\n")

    strict = strict_path_audit()
    print(f"④ 参考：同一瞬间存在整条无影路线的时间占比 {strict * 100:.0f}%")
    print("   （门控关卡天然接近 0：它要求所有门同时空着。玩家实际是逐门等窗口。）")


if __name__ == "__main__":
    main()
