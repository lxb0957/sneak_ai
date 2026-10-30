"""A* 寻路：八方向，欧几里得/切比雪夫启发式。

经典算法，作为三代寻路的基准与正确性参照。
使用二叉堆（heapq）维护开放集，closed 用集合去重。
"""

import heapq
from typing import List, Optional, Tuple

from ..core.grid import GridMap

# 八方向偏移，含代价（斜向 * sqrt2）
_DIRS = [
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, 1.4142135623730951),
    (1, -1, 1.4142135623730951),
    (-1, 1, 1.4142135623730951),
    (-1, -1, 1.4142135623730951),
]


def _heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> float:
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    # 八方向移动（斜向代价 sqrt2）的 admissible 启发式：octile distance
    # = min(dx, dy) * sqrt2 + (max(dx, dy) - min(dx, dy))
    SQRT2 = 1.4142135623730951
    return SQRT2 * min(dx, dy) + (max(dx, dy) - min(dx, dy))


def astar(
    grid: GridMap,
    start: Tuple[int, int],
    goal: Tuple[int, int],
    corner_cut: bool = False,
) -> Optional[List[Tuple[int, int]]]:
    """返回从 start 到 goal 的瓦片路径，含起点；不可达返回 None。

    corner_cut=False（默认，游戏用）禁止斜穿墙角：对角移动要求两个正交邻格
    都可走。开启它只是为了让 JPS 这类"经典语义"的算法有一个同口径的对照。
    """
    if not grid.is_walkable(*goal):
        return None
    if start == goal:
        return []

    open_heap = []
    g_score = {start: 0.0}
    parent = {}
    closed = set()

    f_start = _heuristic(start, goal)
    heapq.heappush(open_heap, (f_start, 0.0, start))

    while open_heap:
        f, g, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        closed.add(cur)

        if cur == goal:
            return _reconstruct(parent, start, goal)

        cx, cy = cur
        for dx, dy, cost in _DIRS:
            nx, ny = cx + dx, cy + dy
            if not grid.is_walkable(nx, ny):
                continue
            # 禁止斜穿墙角：对角移动时，两个正交邻格必须都可走。
            # 否则路径会从两个墙格夹出的"角点"上直切过去 —— 对无体积的点
            # 没问题，但半径为 0.35 的实体圆心正好落在这个角点上，
            # 碰撞判定会把两个坐标轴同时挡住，敌人就永远顶在墙角里抖。
            if dx != 0 and dy != 0 and not corner_cut:
                if not grid.is_walkable(cx + dx, cy) or not grid.is_walkable(cx, cy + dy):
                    continue
            ng = g + cost
            nxt = (nx, ny)
            if nxt in closed:
                continue
            if ng < g_score.get(nxt, float("inf")):
                g_score[nxt] = ng
                parent[nxt] = cur
                heapq.heappush(open_heap, (ng + _heuristic(nxt, goal), ng, nxt))

    return None


def _reconstruct(parent, start, goal):
    path = [goal]
    cur = goal
    while cur != start:
        cur = parent[cur]
        path.append(cur)
    path.reverse()
    return path
