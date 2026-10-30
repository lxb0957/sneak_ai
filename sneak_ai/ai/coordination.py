"""多敌人协同任务分配：追击 / 包抄 / 封路。

解决的核心问题（面试深度）：**N 个敌人追同一个玩家时，如果各自独立
用 A* 追，会全部挤在同一条最优路径上，既难看又低效，甚至互相阻挡。**

方案：一个 Coordinator 周期性（非每帧）根据"能看见玩家的敌人 + 玩家位置"
给每个敌人分配一个角色：

- CHASE（追击）：最近的几个敌人沿最短路径直接逼近玩家。
- FLANK（包抄）：绕到玩家移动方向的两侧/背后，形成夹击。
- BLOCK（封路）：抢占玩家可能的逃生通道口（门、走廊口），堵住去路。

性能关键：
1. assign 节流（每 0.3s 一次），而非每帧全量重算。
2. "咽喉格"（通道口）是地图静态属性，构建时预计算一次并缓存，
   封路角色直接查表，O(1)。
"""

from enum import Enum
from typing import List, Optional, Tuple

import time


def _monotonic() -> float:
    return time.perf_counter()


class Role(Enum):
    PATROL = "patrol"    # 未发现：巡逻
    CHASE = "chase"      # 追击：直接逼近
    FLANK = "flank"      # 包抄：绕侧翼
    BLOCK = "block"      # 封路：堵逃生口


class Coordinator:
    """协同分配器：给定敌人列表和玩家位置，返回每个敌人的目标点与角色。"""

    def __init__(self, grid=None):
        self.assignments: dict = {}  # id(enemy) -> (Role, target_tile)
        self._last_assign = 0.0  # 上次分配时刻
        self._chokepoints = None  # 咽喉格缓存（地图静态属性）
        if grid is not None:
            self.precompute_chokepoints(grid)

    def precompute_chokepoints(self, grid) -> List[Tuple[int, int]]:
        """预计算"咽喉格"：可走、且邻墙多（3~6 个）的格子，即门/通道口。

        这是地图的静态属性，只在初始化时算一次，之后封路角色直接查。
        """
        pts = []
        for y in range(grid.h):
            for x in range(grid.w):
                if not grid.is_walkable(x, y):
                    continue
                walls = 0
                for ox in (-1, 0, 1):
                    for oy in (-1, 0, 1):
                        if ox == 0 and oy == 0:
                            continue
                        if grid.is_wall(x + ox, y + oy):
                            walls += 1
                if 3 <= walls <= 6:
                    pts.append((x, y))
        self._chokepoints = pts
        return pts

    def invalidate(self) -> None:
        """让下一次 assign 立即重算（参与人数变化时需要，别等节流窗口）。"""
        self._last_assign = 0.0

    def assign(
        self,
        enemies: List[object],
        player_pos: Tuple[float, float],
        grid,
    ) -> dict:
        """重新分配角色。返回 {id(enemy): (Role, (tx, ty) 或 None)}。

        注意：此方法可能被多个敌人每帧调用，但内部有节流（0.3s），
        真正重算只在节流窗口到点时执行一次，其余调用直接返回缓存。
        """
        px, py = player_pos
        player_tile = (int(px), int(py))

        # 节流：非首次调用时，未到重算点直接返回缓存
        now = _monotonic()
        if self.assignments and (now - self._last_assign) < 0.3:
            return self.assignments

        ordered = sorted(
            enemies,
            key=lambda e: (e.x - px) ** 2 + (e.y - py) ** 2,
        )
        n = len(ordered)
        assignments = {}

        for i, e in enumerate(ordered):
            if i < max(2, n // 3):  # 最近的 2 个（或 1/3）追击
                assignments[id(e)] = (Role.CHASE, player_tile)
            elif i < max(4, n * 2 // 3):  # 中间一部分包抄
                flank = self._flank_target(player_tile, i, grid)
                assignments[id(e)] = (Role.FLANK, flank)
            else:  # 最远的封路（查预计算的咽喉格，O(1)）
                block = self._block_target(player_tile, grid)
                assignments[id(e)] = (Role.BLOCK, block)

        self.assignments = assignments
        self._last_assign = now
        return assignments

    @staticmethod
    def _flank_target(
        player_tile: Tuple[int, int],
        idx: int,
        grid,
    ) -> Tuple[int, int]:
        """包抄点：玩家周围斜对角偏移，左右交替，绕到侧翼。"""
        px, py = player_tile
        offsets = [
            (5, -5), (-5, -5), (5, 5), (-5, 5),
            (8, 0), (-8, 0), (0, 8), (0, -8),
            (10, -4), (-10, -4), (10, 4), (-10, 4),
        ]
        dx, dy = offsets[(idx - 2) % len(offsets)]
        tx, ty = px + dx, py + dy
        return _nearest_walkable(grid, tx, ty, player_tile)

    def _block_target(self, player_tile, grid) -> Tuple[int, int]:
        """封路点：从预计算的咽喉格里选离玩家最近的一个。O(咽喉格数)。"""
        if not self._chokepoints:
            self.precompute_chokepoints(grid)
        px, py = player_tile
        best = None
        best_d = float("inf")
        for (cx, cy) in self._chokepoints:
            d = (cx - px) ** 2 + (cy - py) ** 2
            if d < best_d:
                best_d = d
                best = (cx, cy)
        return best or player_tile


def _nearest_walkable(grid, x, y, fallback):
    """从 (x,y) 螺旋向外找最近的可走格，找不到返回 fallback。"""
    if grid.is_walkable(x, y):
        return (x, y)
    for r in range(1, 6):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                nx, ny = x + dx, y + dy
                if grid.is_walkable(nx, ny):
                    return (nx, ny)
    return fallback
