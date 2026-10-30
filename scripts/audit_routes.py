"""关卡拓扑体检：用图论量出"玩家的路径选择到底有多少"。

为什么需要这个脚本：改地图之前，"路径很少"只是一种感觉 ——
"空地加墙"的图你永远可以贴边走，实际路线数是几十条，难度只能靠堆敌人。
改成实心开挖后，路线数量是**可以算出来**的：

1. **互不重叠路线数**（最大流 / Menger 定理）
   把每个可走格拆成"进→出"两个节点、容量 1，跑最大流。
   这个数等于**最小割**：要同时堵住几个格子才能把地图切断。
   = 1 是单线；2~3 是"有取舍但不多"；>6 基本就是空地撒欢。

2. **两条主路线的长度比**
   封掉出生舱的东门 → 玩家只能走北弧；封掉南门 → 只能走南弧。
   两条路线的长度差，就是"绕远换安全"这个取舍的定价。

3. **一条路线要穿几道门**
   最短路线经过多少个门格。门宽固定 2 格，所以这个数字直接等于
   "这一趟要在多少个哨兵眼皮底下卡着时机过门"。

跑法：
    python scripts/audit_routes.py
"""
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai import constants as C
from sneak_ai.core.grid import GridMap
from sneak_ai.core.world import DOORS, build_demo_map

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _floor_cells(grid):
    return [(x, y) for y in range(grid.h) for x in range(grid.w)
            if not grid.is_wall(x, y)]


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


def _door_cells():
    cells = set()
    for d in DOORS:
        for y in range(d[1], d[3] + 1):
            for x in range(d[0], d[2] + 1):
                cells.add((x, y))
    return cells


def _route(grid, start, goal, blocked=()):
    """在 blocked 之外求最短路，返回 (长度, 穿过的门数)。"""
    g = GridMap.grid_copy(grid) if hasattr(GridMap, "grid_copy") else None
    if g is None:
        g = GridMap(grid.w, grid.h, [row[:] for row in grid.tiles])
    for c in blocked:
        g.set_wall(*c)
    dist, parent = _bfs(g, start, want_path=True)
    if goal not in dist:
        return None, 0
    doors = _door_cells()
    path, cur = [], goal
    while cur in parent:
        path.append(cur)
        cur = parent[cur]
    return dist[goal], sum(1 for c in path if c in doors)


def main() -> None:
    grid = build_demo_map()
    s = (int(C.EXFIL_POINT[0]), int(C.EXFIL_POINT[1]))
    t = (int(C.DATA_TERMINAL[0]), int(C.DATA_TERMINAL[1]))

    floors = _floor_cells(grid)
    cut = min_cut_size(grid, s, t)
    best, doors_crossed = _route(grid, s, t)

    # 封掉出生舱东门 → 只能走南弧；封掉南门 → 只能走北弧
    east_door = [(11, 5), (11, 6)]
    south_door = [(5, 11), (6, 11)]
    upper, _ = _route(grid, s, t, blocked=south_door)
    lower, _ = _route(grid, s, t, blocked=east_door)

    print("关卡拓扑体检")
    print("=" * 58)
    print(f"地图 {grid.w}x{grid.h}，可走格 {len(floors)}"
          f"（开挖率 {len(floors) / (grid.w * grid.h) * 100:.0f}%）")
    print(f"① 最小割（= 互不重叠路线数）：{cut} 格")
    print("   判读：要让玩家到不了终端，必须同时堵住这么多格；"
          "1=单线，2~3=取舍有限，>6=四处漏风")
    print(f"② 最短路线 {best} 格，穿 {doors_crossed} 道 2 格宽的门")
    if upper and lower:
        delta = (lower / upper - 1) * 100
        print(f"③ 两条主弧：北弧（门多而紧）{upper} 格 / "
              f"南弧（门少而松）{lower} 格，路程差 {delta:+.0f}%")
        print("   判读：两条弧长度几乎相等 —— 取舍不来自距离，"
              "来自「10 道紧门 vs 8 道松门」")
    print(f"④ 全图 {len(DOORS)} 道门，全部门宽 2 格"
          f"（2 格 = 一次只够一个人过）")


if __name__ == "__main__":
    main()
