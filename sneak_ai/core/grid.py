"""网格地图：瓦片类型、碰撞查询、射线遮挡（raycast）。"""

from dataclasses import dataclass
from typing import List, Tuple

WALL = 1
FLOOR = 0


@dataclass
class GridMap:
    w: int
    h: int
    tiles: List[List[int]]

    @classmethod
    def empty(cls, w: int, h: int) -> "GridMap":
        return cls(w, h, [[FLOOR] * w for _ in range(h)])

    @classmethod
    def filled(cls, w: int, h: int) -> "GridMap":
        """整张图先填成实体岩层，再由关卡代码"挖"出通道。

        这是关卡结构的根本改动：从"空地加墙"变成"实心开挖"。
        差别在于**路径数量由挖洞决定**，而不是由玩家绕柱子的自由度决定 ——
        想限制路线选择，就得先有实心可挖的岩体。
        """
        return cls(w, h, [[WALL] * w for _ in range(h)])

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def is_wall(self, x: int, y: int) -> bool:
        if not self.in_bounds(x, y):
            return True  # 边界外视为墙，天然封闭
        return self.tiles[y][x] == WALL

    def is_walkable(self, x: int, y: int) -> bool:
        return not self.is_wall(x, y)

    def set_wall(self, x: int, y: int) -> None:
        if self.in_bounds(x, y):
            self.tiles[y][x] = WALL

    def set_floor(self, x: int, y: int) -> None:
        if self.in_bounds(x, y):
            self.tiles[y][x] = FLOOR


def raycast_blocks(
    grid: GridMap,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> bool:
    """从 (x0,y0) 到 (x1,y1) 打一条射线，是否被墙挡住。

    用 DDA（数字微分分析）遍历射线穿过的所有瓦片，这是 FOV 遮挡的
    核心：只有射线完全无阻挡时，观察者才真正"看见"目标。
    """
    dx = x1 - x0
    dy = y1 - y0
    dist = (dx * dx + dy * dy) ** 0.5
    if dist == 0:
        return False
    steps = int(dist * 2) + 1  # 步进密度，保证不跳过薄墙
    sx = dx / steps
    sy = dy / steps
    cx, cy = x0, y0
    for _ in range(steps):
        cx += sx
        cy += sy
        tx, ty = int(cx), int(cy)
        if grid.is_wall(tx, ty):
            return True
    return False
