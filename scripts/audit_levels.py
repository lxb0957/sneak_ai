"""关卡"绕路体检"：量化地图结构到底逼玩家绕了多少。

为什么加这个脚本：机器人成绩只能说明"这一版在这个相位下多快"，
换个参数就可能变 —— BLOG 经验 31 已经证明过。但有一条是稳定的：
**地图允许的最短路线有多长**。它跟哨兵相位无关，是纯结构量。

三个指标：
1. 最短路线（出生 → 终端 → 撤离）格数；
2. 同样三点间的直线距离（欧氏）；
3. 绕路比 = 最短路线 / 直线距离。
   1.0 = 一条直线走过去（地形完全没参与）；
   1.3+ = 结构性绕行；
   2.0+ = 基本是迷宫。

第 3 关当初设计成"柱林"，本意是逼玩家在缝里做选择。如果绕路比接近 1，
说明柱子全站在路的两边、没挡在路上 —— 那它只是装饰。

跑法：
    SDL_VIDEODRIVER=dummy python scripts/audit_levels.py
"""
import math
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai.core.levels import LEVELS

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def bfs_len(grid, start, goal):
    """4 邻接 BFS，返回格数（含首尾）；不可达返回 None。"""
    sx, sy = int(start[0]), int(start[1])
    gx, gy = int(goal[0]), int(goal[1])
    if grid.is_wall(sx, sy) or grid.is_wall(gx, gy):
        return None
    dist = {(sx, sy): 0}
    q = deque([(sx, sy)])
    while q:
        x, y = q.popleft()
        if (x, y) == (gx, gy):
            return dist[(x, y)] + 1
        for dx, dy in DIRS:
            n = (x + dx, y + dy)
            if n in dist or grid.is_wall(*n):
                continue
            dist[n] = dist[(x, y)] + 1
            q.append(n)
    return None


def straight(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def route_len(grid, spawn, nodes, exfil):
    """按"最近的先拿"顺序走完所有节点，返回总格数与顺序。"""
    cur, total, order = spawn, 0, []
    left = list(nodes)
    while left:
        nxt = min(left, key=lambda n: straight(cur, n))
        d = bfs_len(grid, cur, nxt)
        if d is None:
            return None, order
        total += d
        cur = nxt
        order.append(nxt)
        left.remove(nxt)
    d = bfs_len(grid, cur, exfil)
    if d is None:
        return None, order
    return total + d, order


def main():
    print("关卡绕路体检")
    print("=" * 66)
    for lv in LEVELS:
        grid = lv.build()
        nodes = list(lv.terminals)
        walk, order = route_len(grid, lv.spawn, nodes, lv.exfil)
        if walk is None:
            print(f"[{lv.idx + 1}] {lv.name}: 不可达！")
            continue
        air = straight(lv.spawn, order[0])
        for a, b in zip(order, order[1:]):
            air += straight(a, b)
        air += straight(order[-1], lv.exfil)
        ratio = walk / air
        floor = sum(1 for y in range(grid.h) for x in range(grid.w)
                    if not grid.is_wall(x, y))
        print(f"[{lv.idx + 1}] {lv.name}")
        print(f"    可走格 {floor:5d}   最短路线 {walk:4d} 格   直线 {air:6.1f} 格"
              f"   绕路比 {ratio:.2f}")
        print(f"    数据节点 {len(nodes)} 个，顺序 "
              f"{' → '.join(f'({x:.0f},{y:.0f})' for x, y in order)}")
        print(f"    par S/A/B = {lv.par}   门 {len(lv.doors)} 道")
    print()
    print("判读：绕路比 <1.15 = 地形没参与（走廊/空地都能这么低）；")
    print("      >1.35 = 结构真的在逼人拐弯。")


if __name__ == "__main__":
    main()
