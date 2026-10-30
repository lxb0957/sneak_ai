"""相机：世界坐标↔屏幕坐标，支持跟随玩家滚动。

地图(80x60)大于视口(40x30)，相机以玩家为中心平滑跟随，
并 clamp 到地图边界内，避免看到地图外的空白。
"""

import math

from .. import constants as C


class Camera:
    def __init__(self, offset_x: float = 0.0, offset_y: float = 0.0):
        self.offset_x = offset_x
        self.offset_y = offset_y

    def follow(self, player_x: float, player_y: float, dt: float = 0.0) -> None:
        """以玩家为中心，clamp 到地图范围。

        dt > 0 时做指数平滑（跟随有轻微延迟，画面更"重"也更耐看）；
        dt == 0 时瞬间对齐（测试/录屏等需要确定性时用）。
        """
        # 目标偏移：让玩家位于视口中心
        target_x = player_x * C.TILE_SIZE - C.WINDOW_W / 2
        target_y = player_y * C.TILE_SIZE - C.WINDOW_H / 2
        # clamp：不超出地图像素范围
        max_x = C.GRID_W * C.TILE_SIZE - C.WINDOW_W
        max_y = C.GRID_H * C.TILE_SIZE - C.WINDOW_H
        target_x = max(0.0, min(target_x, max_x))
        target_y = max(0.0, min(target_y, max_y))

        if dt <= 0.0:
            self.offset_x, self.offset_y = target_x, target_y
            return
        # 帧率无关的指数平滑：k 越大跟得越紧
        k = 1.0 - math.exp(-14.0 * dt)
        self.offset_x += (target_x - self.offset_x) * k
        self.offset_y += (target_y - self.offset_y) * k

    def world_to_screen(self, x: float, y: float) -> tuple:
        return (
            int(x * C.TILE_SIZE - self.offset_x),
            int(y * C.TILE_SIZE - self.offset_y),
        )

    def screen_to_world(self, sx: int, sy: int) -> tuple:
        return (
            (sx + self.offset_x) / C.TILE_SIZE,
            (sy + self.offset_y) / C.TILE_SIZE,
        )
