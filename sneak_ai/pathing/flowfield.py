"""流场（Flow Field）寻路：面向"大量单位追同一目标"的批量寻路。

核心思想：A*/JPS 是"每个单位独立算一条路径"，单位一多就 O(N * 单次寻路)。
流场反过来——**只为目标做一次全场计算**，得到每个格子"该往哪走"的方向场，
之后任意数量的单位都只需要 O(1) 查表跟流走。

步骤：
1. 从目标反向 BFS，得到每个可走格子的"距离积分场"（integration field）。
2. 对每个格子，看邻居中积分最小的方向，得到"方向场"（vector field）。
3. 单位每帧读自己所在格子的方向向量移动。

性能关键：积分场/方向场都用**扁平 list 数组**（而非 dict）存储，
索引 = y * w + x，避免 Python dict 的哈希开销。这是"多敌人 60 FPS"
这个数字能成立的前提。

代价：一次 O(全场格子数) 的预处理，摊到 N 个单位上近乎免费。
"""

from typing import Dict, List, Optional, Tuple

from ..core.grid import GridMap

# 八方向向量
_DIRS = [
    (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (1, -1), (-1, 1), (-1, -1),
]
# 对应方向的对角代价（对角 1414，直线 1000，用整数近似避免浮点）
_COSTS = [1000, 1000, 1000, 1000, 1414, 1414, 1414, 1414]


class FlowField:
    """为单个目标构建的方向场，可被任意多单位共享查询。

    内部用扁平数组存积分场和方向场，查询 O(1)。
    """

    def __init__(self, grid: GridMap, goal: Tuple[int, int]):
        self.grid = grid
        self.goal = goal
        self.w = grid.w
        self.h = grid.h
        self._integration = None  # 扁平 list，索引 y*w+x，-1 表示不可达
        self._vec_x = None
        self._vec_y = None
        self._build()

    def _build(self) -> None:
        from collections import deque

        grid = self.grid
        w, h = self.w, self.h
        gx, gy = self.goal
        if not grid.is_walkable(gx, gy):
            self._integration = [-1] * (w * h)
            self._vec_x = [0.0] * (w * h)
            self._vec_y = [0.0] * (w * h)
            return

        INF = 1 << 30
        dist = [INF] * (w * h)

        # 1. 反向 BFS（八方向，整数代价）
        start_idx = gy * w + gx
        dist[start_idx] = 0
        dq = deque([start_idx])
        while dq:
            idx = dq.popleft()
            cx = idx % w
            cy = idx // w
            d = dist[idx]
            for (dx, dy), cost in zip(_DIRS, _COSTS):
                nx, ny = cx + dx, cy + dy
                if not grid.is_walkable(nx, ny):
                    continue
                nidx = ny * w + nx
                nd = d + cost
                if nd < dist[nidx]:
                    dist[nidx] = nd
                    dq.append(nidx)

        self._integration = dist

        # 2. 方向场：每个格子指向积分最小的邻居
        vec_x = [0.0] * (w * h)
        vec_y = [0.0] * (w * h)
        for idx in range(w * h):
            if dist[idx] >= INF:
                continue
            cx = idx % w
            cy = idx // w
            best_dx = best_dy = 0
            best_d = dist[idx]
            for (dx, dy), cost in zip(_DIRS, _COSTS):
                nx, ny = cx + dx, cy + dy
                if not grid.is_walkable(nx, ny):
                    continue
                nd = dist[ny * w + nx]
                if nd < best_d:
                    best_d = nd
                    best_dx, best_dy = dx, dy
            if best_dx != 0 or best_dy != 0:
                l = (best_dx ** 2 + best_dy ** 2) ** 0.5
                vec_x[idx] = best_dx / l
                vec_y[idx] = best_dy / l

        self._vec_x = vec_x
        self._vec_y = vec_y

    def direction_at(self, x: int, y: int) -> Optional[Tuple[float, float]]:
        """查询某格子的方向向量，不可达/越界返回 None。"""
        if not (0 <= x < self.w and 0 <= y < self.h):
            return None
        idx = y * self.w + x
        if self._vec_x is None or self._vec_x[idx] == 0.0 and self._vec_y[idx] == 0.0:
            return None
        return (self._vec_x[idx], self._vec_y[idx])


def flowfield_path(
    grid: GridMap,
    start: Tuple[int, int],
    goal: Tuple[int, int],
) -> Optional[List[Tuple[int, int]]]:
    """用流场从 start 走到 goal，返回瓦片路径（用于与 A*/JPS 对比）。"""
    ff = FlowField(grid, goal)
    if ff.direction_at(*start) is None:
        return None
    path = []
    cur = start
    visited = set()
    while cur != goal:
        if cur in visited:
            return None
        visited.add(cur)
        path.append(cur)
        d = ff.direction_at(*cur)
        if d is None:
            return None
        cur = (cur[0] + round(d[0]), cur[1] + round(d[1]))
    path.append(goal)
    return path
