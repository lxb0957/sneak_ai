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

第二轮优化（v1.8.10）：BFS 和方向场都不再逐格调 `is_walkable`，改用
`GridMap.neighbors8()` 缓存好的邻接表（墙在运行期不变，一场只算一次）。
之前每次重建要付 3.8 万次 `is_walkable` 调用，实测 6.5~14ms ——
追击时每 0.5 秒重建一次，正好把一帧顶出 16.7ms，能看出规律顿挫。
改用邻接表之后同一份数据只要遍历现成的元组，且不改变任何数值结果。

代价：一次 O(全场格子数) 的预处理，摊到 N 个单位上近乎免费。
"""

from typing import Dict, List, Optional, Tuple

from ..core.grid import GridMap

# 对角代价用的整数近似：直线 1000、对角 1414。
# 邻接表只给方向和坐标，代价在这里按方向补 —— 别把 1414 改成 sqrt(2)*1000，
# 积分场是整数排序的，浮点代价会让平局规则变得依赖精度。
_DIAG_COST = 1414
_STRAIGHT_COST = 1000


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
        # 邻接表来自 GridMap 的缓存：墙在运行期不变，这份表一场只算一次。
        # 口径必须和这里原来的 BFS 一致 —— 只要求目标格可走，允许斜穿墙角。
        adj = grid.neighbors8(corner_cut=True)

        # 1. 反向 BFS（八方向，整数代价）
        start_idx = gy * w + gx
        dist[start_idx] = 0
        dq = deque([start_idx])
        while dq:
            idx = dq.popleft()
            d = dist[idx]
            for nidx, dx, dy in adj[idx]:
                nd = d + (_STRAIGHT_COST if dx == 0 or dy == 0 else _DIAG_COST)
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
            best_dx = best_dy = 0
            best_d = dist[idx]
            for nidx, dx, dy in adj[idx]:
                nd = dist[nidx]
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
