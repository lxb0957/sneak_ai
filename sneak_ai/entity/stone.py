"""可投掷的石子：潜行游戏里最便宜、也最像"人"的一件工具。

为什么加它：在这之前玩家面对哨兵只有一种交互 —— **等它自己转开**。
所有主动权都在 AI 手里，玩家能做的只是"躲得对不对"。投石改变的是
信息的流向：你可以在别处制造一个假线索，让哨兵按你的意愿离开岗位。

它不是"控制技能"，因为：
1. 落点会被哨兵**猜偏**（听觉定位有误差），不是精确制导；
2. 只把可疑度抬到"起疑"（0.42），不会直接进入追击；
3. 已经在追你的哨兵不会理石子（视觉信息优先于听觉）；
4. 每局只有 3 颗，扔完就没了。
"""

import math

from .. import constants as C


def plan_landing(grid, origin, facing: float, target=None,
                 max_range: float = C.STONE_RANGE) -> tuple:
    """算出石子的落点：从 origin 沿方向前进，撞墙前最后一个可走点。

    target 不为空时朝它扔（鼠标瞄准），否则朝角色朝向扔。
    逐点步进而不是"取射线交点"：格子地图上这样最不容易出现
    "落点落在墙里、哨兵去调查一堵墙"的尴尬情况。
    """
    ox, oy = origin
    if target is not None:
        dx, dy = target[0] - ox, target[1] - oy
        dist = math.hypot(dx, dy)
        if dist < 0.35:
            dx, dy = math.cos(facing), math.sin(facing)
            dist = 1.0
        else:
            dx, dy = dx / dist, dy / dist
            max_range = min(max_range, dist)
    else:
        dx, dy = math.cos(facing), math.sin(facing)

    step = 0.06
    d = 0.4
    last = (ox + dx * 0.3, oy + dy * 0.3)
    while d <= max_range:
        px, py = ox + dx * d, oy + dy * d
        if grid.is_wall(int(px), int(py)):
            break
        last = (px, py)
        d += step
    return last


class Stone:
    """一颗在飞 / 已落地的石子。飞行只是表现层，落点在扔出时就算好了。"""

    def __init__(self, x: float, y: float, tx: float, ty: float):
        self.x0, self.y0 = x, y
        self.x, self.y = x, y
        self.tx, self.ty = tx, ty
        self.dist = math.hypot(tx - x, ty - y)
        # 飞行时长随距离增加，但不太长（否则玩家会觉得"扔出去没反应"）
        self.dur = max(0.14, 0.10 + 0.038 * self.dist)
        self.t = 0.0
        self.landed = False
        self.h = 0.0      # 抛物线高度（格），只影响渲染
        self.spin = 0.0

    def sample(self, back: float) -> tuple:
        """取"倒退 back 秒"时的位置与高度，用于画尾迹。"""
        t = max(0.0, self.t - back)
        k = min(1.0, t / self.dur)
        h = math.sin(math.pi * k) * (0.42 + 0.055 * self.dist)
        return (
            self.x0 + (self.tx - self.x0) * k,
            self.y0 + (self.ty - self.y0) * k,
            h,
        )

    def update(self, dt: float) -> bool:
        """推进飞行。返回 True 表示这一帧落地（调用方据此触发噪音）。"""
        if self.landed:
            return False
        self.t += dt
        k = min(1.0, self.t / self.dur)
        self.x = self.x0 + (self.tx - self.x0) * k
        self.y = self.y0 + (self.ty - self.y0) * k
        # 抛物线高度：起手与落地都在地面，中途最高约 1 格（小石头，不是手雷）
        self.h = math.sin(math.pi * k) * (0.42 + 0.055 * self.dist)
        self.spin += dt * 11.0
        if k >= 1.0:
            self.landed = True
            self.h = 0.0
            return True
        return False
