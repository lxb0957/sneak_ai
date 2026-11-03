"""相机：世界坐标↔屏幕坐标，支持跟随玩家滚动。

地图比视口(40x30)大，相机以玩家为中心平滑跟随，并 clamp 到地图边界内，
避免看到地图外的空白。

**边界必须按当前关卡算，不能用常量 C.GRID_W/GRID_H**。原来这里写死 80x60
（第 1 关的尺寸），而第 4 关是 92x42：相机在 x=1280 就停住，可它本该能滑到
1664 —— 最右边 12 格永远进不了屏幕，出口终端在那儿，于是"窗口过不去、
看不到最右边"。反过来底部又允许滑到 960（第 4 关只需要 384），能滑出地图下沿。
"""

import math

from .. import constants as C


class Camera:
    def __init__(self, offset_x: float = 0.0, offset_y: float = 0.0):
        self.offset_x = offset_x
        self.offset_y = offset_y
        # 关卡尺寸（格）。默认取常量，关卡加载时由 set_bounds 覆盖
        self.grid_w = C.GRID_W
        self.grid_h = C.GRID_H

    def set_bounds(self, grid_w: int, grid_h: int) -> None:
        """关卡切换时必须调用：相机夹取的边界跟着这一关的尺寸走。"""
        self.grid_w = max(1, int(grid_w))
        self.grid_h = max(1, int(grid_h))

    def follow(self, player_x: float, player_y: float, dt: float = 0.0) -> None:
        """以玩家为中心，clamp 到地图范围。

        dt > 0 时做指数平滑（跟随有轻微延迟，画面更"重"也更耐看）；
        dt == 0 时瞬间对齐（测试/录屏等需要确定性时用）。
        """
        # 目标偏移：让玩家位于视口中心
        target_x = player_x * C.TILE_SIZE - C.WINDOW_W / 2
        target_y = player_y * C.TILE_SIZE - C.WINDOW_H / 2
        # clamp：不超出地图像素范围。地图比窗口小时 max 会是负数，
        # 夹到 0 而不是负数，否则画面会被推到地图左上方的空白里去
        max_x = max(0.0, self.grid_w * C.TILE_SIZE - C.WINDOW_W)
        max_y = max(0.0, self.grid_h * C.TILE_SIZE - C.WINDOW_H)
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
