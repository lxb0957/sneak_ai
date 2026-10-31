"""声音在关卡里的传播：从声源出发的**加权最短路代价场**。

为什么不能用直线距离
--------------------
`can_hear` 原来是纯欧氏距离：中间隔了三堵墙、二十几格实体岩，只要直线
距离够近，哨兵照样听得一清二楚。而视觉那边是用 raycast 做过遮挡的 ——
同一套感知层，眼睛要射线，耳朵却能透视，这不是"简化"，是漏子。

补法有两种：

1. 数射线穿过的墙，按墙数衰减（便宜，但仍然假设声音只会走直线）；
2. 按**能走通的路**算代价，穿墙额外收费（贵一点，但符合实际）。

这里选 2。原因不只在物理：地图是从实心岩体里挖出来的 θ 形，两个格子可能
在屏幕上只差 3 格，实际却要绕过 20 格的走廊。声音"绕过去"，玩家才能用
"拐个弯"摆脱听觉；如果按直线算，玩家会觉得哨兵在隔着岩层透视。

代价模型
--------
一次移动的代价 = 欧氏步长（1 或 √2）+ 目标是墙则加 `SOUND_WALL_COST`。

- 走廊里：代价 ≈ 实际路程，和旧行为几乎一致（不会莫名其妙变难）；
- 隔一层薄墙：多付 `SOUND_WALL_COST` 格 —— 还能听见，但传不远；
- 隔厚岩层：每格墙都收费，三格之后就超出听觉半径，自然听不见。

因为声源、听觉半径都是逐帧变化的，代价场按声源**所在格**缓存
（玩家每跨一格才重算），单个场只展开到半径上限，实测 1ms 量级。
"""

import heapq
import math
from typing import Dict, Optional, Tuple

from .. import constants as C


# (dx, dy, 步长)：8 邻域。对角 1.414 而不是 1 —— 否则"斜着走"会便宜 30%，
# 声音会沿着 45° 方向传得比直线更远，形成肉眼可见的八角星。
_NEI = (
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, math.sqrt(2.0)), (1, -1, math.sqrt(2.0)),
    (-1, 1, math.sqrt(2.0)), (-1, -1, math.sqrt(2.0)),
)


def hearing_range(base_range: float, noise: float) -> float:
    """噪音系数 → 有效听觉半径。

    这个公式原来抄了两份（`Perception.can_hear` 一份、`Player.noise_radius`
    一份），是典型的"改一处忘一处"结构：哪天调了系数，屏幕上画的圈就
    不再是 AI 真正用的那个圈，玩家看到的指南是假的。收成一份。
    """
    if noise <= C.NOISE_STILL:
        return 0.0
    return base_range * min(1.5, 0.55 + 0.6 * noise)


class SoundField:
    """从 `src` 出发、最大代价 `limit` 的声音传播代价场。

    用法：

        field = SoundField(grid, (x, y), limit=9.0)
        d = field.cost_at(enemy.pos)
        if d <= 9.0: ...

    `cost_at` 返回**累计代价**（不是直线距离）；没被场覆盖到的格子返回
    `math.inf` —— 调用方不需要区分"太远"和"被挡住"，两者都是听不见。
    """

    def __init__(self, grid, src, limit: float):
        self.grid = grid
        self.src: Tuple[int, int] = (int(src[0]), int(src[1]))
        self.limit = float(limit)
        self.cost: Dict[Tuple[int, int], float] = {self.src: 0.0}
        self._flood()

    def _flood(self) -> None:
        w, h = self.grid.w, self.grid.h
        tiles = self.grid.tiles
        wall_cost = C.SOUND_WALL_COST
        limit = self.limit
        cost = self.cost
        heap = [(0.0, self.src[0], self.src[1])]
        push, pop = heapq.heappush, heapq.heappop

        while heap:
            c, x, y = pop(heap)
            # 堆里可能有同一个格子的旧条目（Dijkstra 的常规处理）
            if c > cost.get((x, y), math.inf) + 1e-9:
                continue
            for dx, dy, step in _NEI:
                nx, ny = x + dx, y + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                nc = c + step
                if tiles[ny][nx]:
                    nc += wall_cost
                if nc > limit:
                    continue
                key = (nx, ny)
                if nc < cost.get(key, math.inf) - 1e-9:
                    cost[key] = nc
                    push(heap, (nc, nx, ny))

    def cost_at(self, pos) -> float:
        """`pos` 处的传播代价；场没覆盖到就是 inf。"""
        return self.cost.get((int(pos[0]), int(pos[1])), math.inf)

    def reached(self, pos, radius: float) -> Optional[float]:
        """落在 `radius` 内则返回 1→0 的衰减系数，否则 None。

        `1 - d/radius` 和旧版的 `1 - dist/eff_range` 是同一个形状，
        只是把"直线距离"换成了"声音实际走了多远"。
        """
        d = self.cost_at(pos)
        if d > radius:
            return None
        return 1.0 - d / radius


def path_cost(grid, src, dst, limit: float = 64.0) -> float:
    """单次查询两点声音代价。给测试和一次性判定用；批量请用 `SoundField`。"""
    return SoundField(grid, src, limit).cost_at(dst)
