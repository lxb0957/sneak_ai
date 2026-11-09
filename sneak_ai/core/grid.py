"""网格地图：瓦片类型、碰撞查询、射线遮挡（raycast）。

**邻接表缓存**（v1.8.10 加的）
------------------------------
流场每 0.5 秒重建一次，每次要对全场格子 × 8 个方向调 `is_walkable`——
一张 80×60 的图就是 3.8 万次调用，实测单次重建 6.5~14ms，正好把一帧顶出
16.7ms 的预算，追击时能看见规律的顿挫。而墙在运行期**一格都不会变**
（只有建关时的 `carve` / `fill` / `add_cover` 会改），所以"谁能走到谁"
是一份可以缓存的静态数据。

缓存的失效靠 `_version`：`set_wall` / `set_floor` 一改就自增，下次取表时
重建。**直接改 `tiles[y][x]` 不会让缓存失效** —— 全仓库没有任何一处这么写，
`test_w36` 会逐个格子核对缓存和 tiles 是否一致，把这条路钉死。
"""

from dataclasses import dataclass, field
import math
from typing import Dict, List, Tuple

WALL = 1
FLOOR = 0

# 八方向邻域。**这个顺序是约定，不要随手排**：
# 流场在"两个邻居一样近"时取先遇到的那个（方向场靠它决定平局），
# 顺序一变，个别格子上的箭头就会翻转，敌人的走位跟着变 ——
# 而这种变化不会让任何数值测试变红。
# 顺序与 `pathing/astar.py` 的 `_DIRS`、`scripts/botrun.py` 的 `_DIRS8` 一致。
NEI8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


@dataclass
class GridMap:
    w: int
    h: int
    tiles: List[List[int]]
    # 墙体版本号：set_wall / set_floor 改动了就 +1，邻接表据此失效
    _version: int = field(default=0, init=False, repr=False, compare=False)
    # {(corner_cut): (version, adj)}，adj[i] = ((nidx, dx, dy), ...)，i = y*w+x
    _adj: Dict[bool, Tuple[int, list]] = field(default_factory=dict, init=False,
                                              repr=False, compare=False)

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
        """是不是墙。越界也算墙（天然封闭）。

        这里是全项目调用最密的函数（单局机器人 480 万次），所以不调
        `in_bounds` 而是把边界判断内联 —— 省下的是一次函数调用，
        而它占了调用方的 15%。
        """
        w = self.w
        if 0 <= x < w and 0 <= y < self.h:
            return self.tiles[y][x] == WALL
        return True

    def is_walkable(self, x: int, y: int) -> bool:
        return not self.is_wall(x, y)

    def set_wall(self, x: int, y: int) -> None:
        if self.in_bounds(x, y):
            if self.tiles[y][x] != WALL:
                self.tiles[y][x] = WALL
                self._version += 1

    def set_floor(self, x: int, y: int) -> None:
        if self.in_bounds(x, y):
            if self.tiles[y][x] != FLOOR:
                self.tiles[y][x] = FLOOR
                self._version += 1

    # ---- 邻接表（寻路的静态数据，一次算好反复用）----
    def neighbors8(self, corner_cut: bool = False) -> list:
        """八方向邻接表，扁平索引；`adj[y*w+x]` = ((nidx, dx, dy), ...)。

        corner_cut=False（默认）：禁止斜穿墙角 —— 对角要走，两个正交邻格
        必须都可走。这是实体移动的口径（半径 0.35 的圆心正好卡在角点上，
        两个轴同时被挡，敌人会顶着墙抖）。

        corner_cut=True：只要求目标格可走。这是**流场 BFS 一直以来的口径**，
        保留它是为了不改动历史行为（流场只给追击用方向，实际移动另有碰撞）。

        顺序严格按 `NEI8`，平局取先遇到的 —— 方向场的结果依赖它。
        """
        hit = self._adj.get(corner_cut)
        if hit is not None and hit[0] == self._version:
            return hit[1]

        w, h, tiles = self.w, self.h, self.tiles
        n = w * h
        adj = [()] * n
        for y in range(h):
            row = tiles[y]
            base = y * w
            for x in range(w):
                if row[x] == WALL:
                    continue
                out = []
                for dx, dy in NEI8:
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < w and 0 <= ny < h):
                        continue
                    if tiles[ny][nx] == WALL:
                        continue
                    if not corner_cut and dx and dy:
                        if row[nx] == WALL or tiles[ny][x] == WALL:
                            continue
                    out.append((ny * w + nx, dx, dy))
                adj[base + x] = tuple(out)

        self._adj[corner_cut] = (self._version, adj)
        return adj


def circle_hits_wall(x: float, y: float, r: float, grid: "GridMap",
                     inset: float = 0.0) -> bool:
    """圆心 (x, y)、半径 r 的圆是否和墙格相交（圆心到墙格最近点的距离）。

    用"最近点距离"而不是四角包围盒：贴墙但没真碰到时不能被判成碰撞，
    否则贴着墙走会一卡一卡（早期版本最烦人的 bug 之一）。

    `inset` 是给"恰好贴在角点上"留的容差（从 r² 里减掉）：
    - 敌人移动传 `1e-6`。圆心正好落在墙格角点时最近点距离为 0，严格比较
      会把它判成"在墙里"，实体被永久钉死；
    - 玩家移动传 `0`。这是历史行为，手感逐帧调过，不为"统一"而改。

    **为什么抽到这里**：这段几何原来在 `enemy.py` 和 `player.py` 各写了一份，
    而且已经悄悄分叉（敌人那份有容差，玩家那份没有）。两份实现跑的是同一张
    地图、同一个半径语义 —— 一边修了卡角、另一边还在卡，就是"两把尺子"。
    实现只有一份，口径由参数决定，才不会再分叉。

    候选格按半径取整（`floor(x-r) … floor(x+r)`）而不是固定 3×3：半径 0.3
    的实体最多只可能碰到 1~2 格宽的一条带，3×3 里有 5 格以上连"最近点距离
    < r"都不可能满足。逐坐标对拍过（`test_w36`，2.8 万组），结果与 3×3 一致。
    """
    x0, x1 = math.floor(x - r), math.floor(x + r)
    y0, y1 = math.floor(y - r), math.floor(y + r)
    rr = r * r - inset
    is_wall = grid.is_wall
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            if not is_wall(tx, ty):
                continue
            px = max(tx, min(x, tx + 1))
            py = max(ty, min(y, ty + 1))
            dx = x - px
            dy = y - py
            if dx * dx + dy * dy < rr:
                return True
    return False


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
