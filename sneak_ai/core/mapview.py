"""地图视图：把整张静态地图**预渲染一次**成一个大 Surface，之后每帧只 blit 可视区域。

性能动机：原实现每帧对可视范围（~42x32 = 1300 格）逐格 fill + draw.rect，
也就是每帧上千次绘制调用。地图是静态的 —— 只要渲染一次 2560x1920 的
大图，每帧改成 1 次 blit（带 source rect），CPU 开销直接掉一个数量级，
省下来的预算可以拿去画视野锥、粒子这些真正每帧都变的东西。

顺带把美术细节（墙面砖缝、地板颗粒）也一次性烘进这张图里 —— 反正只画一次。
"""

import random

import pygame

from .. import constants as C


class MapView:
    def __init__(self, grid):
        self.grid = grid
        self.surface = self._build()

    def draw(self, screen, cam) -> None:
        screen.blit(
            self.surface,
            (0, 0),
            (
                int(cam.offset_x),
                int(cam.offset_y),
                C.WINDOW_W,
                C.WINDOW_H,
            ),
        )

    # ---- 烘焙 ----
    def _build(self) -> pygame.Surface:
        ts = C.TILE_SIZE
        surf = pygame.Surface((self.grid.w * ts, self.grid.h * ts)).convert()
        surf.fill(C.COL_BG)
        rng = random.Random(20261020)

        for y in range(self.grid.h):
            for x in range(self.grid.w):
                px, py = x * ts, y * ts
                if self.grid.is_wall(x, y):
                    self._wall_tile(surf, x, y, px, py, rng)
                else:
                    self._floor_tile(surf, x, y, px, py, rng)
        return surf

    @staticmethod
    def _floor_tile(surf, x, y, px, py, rng) -> None:
        ts = C.TILE_SIZE
        base = C.COL_FLOOR_A if (x + y) % 2 == 0 else C.COL_FLOOR_B
        surf.fill(base, (px, py, ts, ts))

        # 网格线（只画上/左两条，避免相邻格重复画同一条线）
        pygame.draw.line(surf, C.COL_GRID_LINE, (px, py), (px + ts - 1, py))
        pygame.draw.line(surf, C.COL_GRID_LINE, (px, py), (px, py + ts - 1))

        # 地板颗粒：让"纯色方块"有材质感（一次性烘进静态图，零运行时成本）
        for _ in range(2):
            gx = px + rng.randrange(3, ts - 3)
            gy = py + rng.randrange(3, ts - 3)
            shade = rng.choice(((36, 41, 48), (25, 29, 35), (33, 38, 45)))
            surf.fill(shade, (gx, gy, 2, 2))

    def _wall_tile(self, surf, x, y, px, py, rng) -> None:
        """墙体 2.5D：顶面亮、正面暗。

        关键细节：**只在"露出来的面上"画高光/阴影**。
        连续墙段中间的格子不该有顶面亮边，否则整面墙会变成一条条横纹
        （像梯子），这是把逐格绘制拼成连续墙面必须处理的事。
        """
        ts = C.TILE_SIZE
        top_h = int(ts * 0.28)
        exposed_top = not self.grid.is_wall(x, y - 1)
        exposed_bottom = not self.grid.is_wall(x, y + 1)
        exposed_left = not self.grid.is_wall(x - 1, y)
        exposed_right = not self.grid.is_wall(x + 1, y)

        surf.fill(C.COL_WALL_FRONT, (px, py, ts, ts))

        if exposed_top:
            surf.fill(C.COL_WALL_TOP, (px, py, ts, top_h))
            surf.fill((99, 106, 119), (px, py, ts, 2))
            pygame.draw.line(surf, C.COL_WALL_EDGE,
                             (px, py + top_h), (px + ts, py + top_h), 1)
        # 被墙挡住的顶面不画任何亮边 —— 连续墙面必须保持一整片，否则出现横纹

        # 砖缝：只在"真正露出的正面"画，且颜色比墙身只亮一点点，避免花
        if exposed_top or exposed_bottom or exposed_left or exposed_right:
            front_y = py + (top_h if exposed_top else 0)
            for by in range(front_y + 9, py + ts - 3, 9):
                surf.fill((58, 62, 72), (px + 1, by, ts - 2, 1))

        if exposed_bottom:
            surf.fill((40, 44, 52), (px, py + ts - 3, ts, 3))
        if exposed_left:
            surf.fill((45, 49, 58), (px, py, 2, ts))
        if exposed_right:
            surf.fill((45, 49, 58), (px + ts - 2, py, 2, ts))

        if exposed_top and rng.random() < 0.15:
            gx = px + rng.randrange(6, ts - 8)
            surf.fill((70, 76, 88), (gx, py + 4, 3, 2))
