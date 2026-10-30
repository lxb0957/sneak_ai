"""感知层：视觉锥（FOV）+ 射线遮挡 + 听觉半径 + 可疑度累积。

这是 AI "看见世界"的眼睛。关键设计：
1. 视觉锥：只对朝向一定夹角内的目标产生感知（敌人不是 360° 透视）。
2. 射线遮挡：即便在锥内，中间有墙也看不见 —— 用 raycast 逐瓦片判定。
3. 距离衰减：越远越难发现，避免"秒发现"。
4. 听觉：潜行时脚步轻、跑动时脚步重，影响被听见的距离。
"""

import math

from .. import constants as C
from ..core.grid import GridMap, raycast_blocks


def angle_diff(a: float, b: float) -> float:
    """两个弧度角的最小夹角，归一化到 [-pi, pi]。"""
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return d


class Perception:
    def __init__(
        self,
        fov_angle: float = math.radians(110),
        view_range: float = 6.5,
        hear_range: float = C.HEAR_RANGE,
    ):
        self.fov_angle = fov_angle  # 总视场角（弧度）
        self.view_range = view_range
        self.hear_range = hear_range
        self.suspicion = 0.0  # 可疑度 0~1，累积到阈值才"确认"发现

    def can_see(
        self,
        eye: tuple,
        facing: float,
        target: tuple,
        grid: GridMap,
        view_range: float = None,
    ) -> float:
        """返回 0~1 的可见置信度，0 = 完全看不见。

        置信度受距离衰减影响；锥外或遮挡直接返回 0。
        view_range 可传入"本次判定的临时视野"（如警觉状态 12 格），
        这样就不必为了改视野去修改共享 Perception 实例的字段
        —— 多个敌人共用一个 Perception 时，改字段会互相污染。
        """
        rng = self.view_range if view_range is None else view_range
        ex, ey = eye
        tx, ty = target
        dx, dy = tx - ex, ty - ey
        dist = (dx * dx + dy * dy) ** 0.5
        if dist > rng:
            return 0.0
        if dist < 1e-6:
            # 完全重合：贴脸 = 彻底暴露，返回 1.0。
            # 旧版这里和 "超距" 合并成 `return 0.0`（只是为了避免除零），
            # 结果变成"站在哨兵身上反而最安全"—— 一个荒谬但确实存在的边界，
            # 是"敌人贴到玩家脸上却抓不到"的回归测试把它揪出来的。
            return 1.0

        # 视觉锥判定
        ang = math.atan2(dy, dx)
        if abs(angle_diff(facing, ang)) > self.fov_angle / 2:
            return 0.0

        # 遮挡判定（核心）
        if raycast_blocks(grid, ex, ey, tx, ty):
            return 0.0

        # 距离衰减：贴脸 1.0，视野边缘趋近 0
        return 1.0 - dist / rng

    def can_hear(
        self,
        eye: tuple,
        target: tuple,
        noise: float,
    ) -> float:
        """返回 0~1 的听觉强度。noise 是目标的噪音系数（0~2）。

        噪音越大，能被听见的有效半径越远、强度越高：
        - 潜行（0.30）：几乎只能贴脸听见，是潜行玩法的核心收益
        - 走路（1.00）：基础听觉半径
        - 冲刺（1.85）：半径接近翻倍，跑起来会"招人"
        """
        if noise <= C.NOISE_STILL:
            return 0.0
        ex, ey = eye
        tx, ty = target
        dist = ((tx - ex) ** 2 + (ty - ey) ** 2) ** 0.5
        # 有效听觉半径随噪音放大（0.55 ~ 1.5 倍之间）
        eff_range = self.hear_range * min(1.5, 0.55 + 0.6 * noise)
        if dist > eff_range:
            return 0.0
        return min(1.0, noise * 0.8) * (1.0 - dist / eff_range)

    def update_suspicion(
        self,
        eye: tuple,
        facing: float,
        target: tuple,
        grid: GridMap,
        dt: float,
        noise: float,
    ) -> float:
        """累积可疑度；看不见听不见时缓慢衰减。返回当前可疑度。"""
        see = self.can_see(eye, facing, target, grid)
        hear = self.can_hear(eye, target, noise)
        stim = max(see, hear * 0.6)  # 听觉线索弱于视觉
        if stim > 0:
            self.suspicion = min(1.0, self.suspicion + stim * dt * 1.5)
        else:
            self.suspicion = max(0.0, self.suspicion - dt * 0.3)
        return self.suspicion
