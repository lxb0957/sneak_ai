"""关卡拓扑体检：用图论量出"玩家的路径选择到底有多少"。

为什么需要这个脚本：改地图之前，"路径很少"只是一种感觉 ——
"空地加墙"的图你永远可以贴边走，实际路线数是几十条，难度只能靠堆敌人。
改成实心开挖后，路线数量是**可以算出来**的：

1. **互不重叠路线数**（最大流 / Menger 定理）
   把每个可走格拆成"进→出"两个节点、容量 1，跑最大流。
   这个数等于**最小割**：要同时堵住几个格子才能把地图切断。
   = 1 是单线；2~3 是"有取舍但不多"；>6 基本就是空地撒欢。

2. **一条路线要穿几道门**
   最短路线经过多少个门格。门宽固定 1~2 格，所以这个数字直接等于
   "这一趟要在多少个哨兵眼皮底下卡着时机过门"。

3. **最短路线本身**
   README 的难度表里那一行"最短路线 235 格"就是它 —— 但**口径必须只有一处**，
   否则每关各说各的：曾经第 1 关按"往返合计"写 235、第 4 关按"单程"写 88，
   第 3 关改过地图之后 238 一直没更新（真实是 192）。三个数放在同一行里，
   读的人没法比较，改图的人也不知道该改哪个。

   现在定死的口径（`route_stats()` 是唯一实现，README 与 test_w31 都引它）：

   **格 = 从出生格开始，依次走完这一关所有数据节点，最后走到撤离格，
   路径上经过的格子数。每接一段把起点算进来（+1），所以**
   `格 = 步 + 段数`，`段数 = 数据节点数 + 1`。

   为什么按"节点顺序"走而不是求"访问所有节点的最短路"：关卡里的节点顺序
   本来就是建议顺序（第 3 关东南→西北的 Z 字），玩家不会去解 TSP；
   这个数字要回答的是"照设计意图走一趟有多远"，不是"理论最短是多少"。

跑法：
    python scripts/audit_routes.py

退出码：任何一关走不通（数据节点或撤离点不可达）→ 1，可以当门禁用。
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai.core.levels import LEVELS

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _floor_cells(grid):
    return [(x, y) for y in range(grid.h) for x in range(grid.w)
            if not grid.is_wall(x, y)]


def _cell(point):
    """连续坐标 → 格子下标（玩家坐标是以格中心记的，取整就是所在格）。"""
    return (int(point[0]), int(point[1]))


def _bfs(grid, start, want_path=False):
    """单源 BFS：返回 (距离表, 父节点表)。"""
    dist = {start: 0}
    parent = {}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for dx, dy in DIRS:
            n = (x + dx, y + dy)
            if n in dist or grid.is_wall(*n):
                continue
            dist[n] = dist[(x, y)] + 1
            parent[n] = (x, y)
            q.append(n)
    return (dist, parent) if want_path else (dist, None)


def min_cut_size(grid, start, goal, cap=10):
    """最小割大小 = 最大互不重叠路线数（点不相交）。

    标准的节点拆分 + Edmonds-Karp：每格拆成 in→out 容量 1，
    相邻格的 out→in 容量 1。容量全是 1，几次增广就收敛。
    """
    cells = _floor_cells(grid)
    idx = {c: i for i, c in enumerate(cells)}
    n = len(cells)
    graph = [[] for _ in range(2 * n)]

    def add_edge(u, v, c):
        graph[u].append([v, c, len(graph[v])])
        graph[v].append([u, 0, len(graph[u]) - 1])

    s, t = 2 * idx[start], 2 * idx[goal]
    for c in cells:
        i = idx[c]
        # 起终点自身不设容量：它们要承载所有路线
        add_edge(2 * i, 2 * i + 1, cap if c in (start, goal) else 1)
    for (x, y) in cells:
        for dx, dy in DIRS:
            nb = (x + dx, y + dy)
            if nb in idx:
                add_edge(2 * idx[(x, y)] + 1, 2 * idx[nb], 1)

    flow = 0
    while flow < cap:
        prev = [None] * (2 * n)
        prev[s] = (-1, -1)
        q = deque([s])
        while q and prev[t] is None:
            u = q.popleft()
            for ei, e in enumerate(graph[u]):
                if e[1] > 0 and prev[e[0]] is None:
                    prev[e[0]] = (u, ei)
                    q.append(e[0])
        if prev[t] is None:
            break
        v = t
        while v != s:
            u, ei = prev[v]
            e = graph[u][ei]
            e[1] -= 1
            graph[v][e[2]][1] += 1
            v = u
        flow += 1
    return flow


def door_map(level):
    """门格 → 门名。`Level.doors` 统一是 (矩形, 名字)，矩形含两端。

    按**名字**去重再计数：一道 2 格宽的门，路线从中间穿过时会踩到 2 个门格，
    但玩家只过了一道门 —— 数格子会把"过 9 道门"说成"过 18 道门"。
    """
    cells = {}
    for rect, name in level.doors:
        x0, y0, x1, y1 = rect
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for x in range(min(x0, x1), max(x0, x1) + 1):
                cells[(x, y)] = name
    return cells


def level_legs(level):
    """这一关要走的几段：出生 → 节点 1 → … → 节点 n → 撤离。"""
    pts = [level.spawn, *level.terminals, level.exfil]
    return [(_cell(a), _cell(b)) for a, b in zip(pts, pts[1:])]


def route_stats(level, grid=None):
    """按"出生 → 各节点 → 撤离"求最短路，返回口径里的全部数字。

    返回 {"steps", "cells", "legs", "doors", "ok"}：
      steps —— 相邻格之间的移动次数；
      cells —— 经过的格子数 = steps + 段数；
      legs  —— 每段的步数（走不通的那段是 None）；
      doors —— 整条路线穿过几道门（按门名去重，不是门格数）；
      ok    —— 每一段都走得到。
    """
    grid = grid if grid is not None else level.build()
    doors = door_map(level)
    steps, legs, crossed = 0, [], set()
    ok = True
    for start, goal in level_legs(level):
        dist, parent = _bfs(grid, start, want_path=True)
        if goal not in dist:
            ok = False
            legs.append(None)
            continue
        steps += dist[goal]
        legs.append(dist[goal])
        cur = goal
        while cur in parent:
            if cur in doors:
                crossed.add(doors[cur])
            cur = parent[cur]
        if cur in doors:
            crossed.add(doors[cur])
    return {
        "steps": steps,
        "cells": steps + len(legs),
        "legs": legs,
        "doors": len(crossed),
        "ok": ok,
    }


def main() -> int:
    print("关卡拓扑体检（量的是 LEVELS 注册表，不是某一张手写地图）")
    print("口径：格 = 出生 → 依次摸完所有数据节点 → 撤离，路径上经过的格子数")
    print("      格 = 步 + 段数（每段起点各算一次）")
    print("=" * 78)
    header = (f"{'关':<3}{'名称':<8}{'地图':>9}{'可走':>6}"
              f"{'最短路线':>10}{'步':>6}{'段':>4}{'穿门':>5}{'最小割':>6}")
    print(header)
    print("-" * 78)
    failed = False
    for lv in LEVELS:
        grid = lv.build()
        st = route_stats(lv, grid)
        floors = len(_floor_cells(grid))
        cells = f"{st['cells']} 格" if st["ok"] else "走不通"
        legs = "+".join("?" if v is None else str(v) for v in st["legs"])
        # 最小割用出生点与第一个节点：撤离点可能和出生点重合（第 4 关），
        # 拿它当终点会让"互不重叠路线"变成 0。
        cut = min_cut_size(grid, _cell(lv.spawn), _cell(lv.terminals[0]))
        print(f"{lv.idx + 1:<3}{lv.name:<8}{grid.w:>4}x{grid.h:<3}{floors:>7}"
              f"{cells:>10}{st['steps']:>7}{len(st['legs']):>5}"
              f"{st['doors']:>5}{cut:>6}   路线 {'+'.join(str(v) for v in st['legs'])}")
        if not st["ok"]:
            failed = True
    print("-" * 78)
    print("判读：最小割 = 要同时堵住几格才能切断这一关（1=单线，>6=四处漏风）；")
    print("      穿门 = 这一趟要过几道门 = 几次卡时机（按门名去重）。")
    for lv in LEVELS:
        print(f"      第 {lv.idx + 1} 关：{len(lv.doors)} 道门")
    if failed:
        print("\n✗ 有目标点走不通 —— 改图之后忘了挖通路？")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
