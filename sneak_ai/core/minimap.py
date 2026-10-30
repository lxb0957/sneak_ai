"""小地图：80x60 的地图 + 跟随相机，玩家很容易迷路，小地图是必需件。

实现要点（性能）：
墙体是**静态**的，所以整张底图在初始化时渲染一次到 Surface，
每帧只需要 blit 底图 + 画十几个小点。绝对不要在每帧里重画 4800 个格子。
"""

import math

import pygame

from .. import constants as C
from ..ai.coordination import Role
from .ui import draw_panel

TILE = 2  # 小地图每格 2 像素 -> 160x120


class Minimap:
    def __init__(self, grid, margin: int = 14):
        self.grid = grid
        self.w = grid.w * TILE
        self.h = grid.h * TILE
        self.margin = margin
        self.base = self._build_base()

    def _build_base(self) -> pygame.Surface:
        surf = pygame.Surface((self.w, self.h)).convert()
        surf.fill((16, 19, 24))
        floor = (50, 57, 68)
        wall = (104, 113, 128)
        for y in range(self.grid.h):
            for x in range(self.grid.w):
                color = wall if self.grid.is_wall(x, y) else floor
                surf.fill(color, (x * TILE, y * TILE, TILE, TILE))
        return surf

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(
            C.WINDOW_W - self.w - self.margin,
            self.margin,
            self.w,
            self.h,
        )

    def draw(self, screen, world, cam, pulse: float = 0.0) -> None:
        box = self.rect
        draw_panel(screen, box.inflate(10, 10), alpha=205)
        screen.blit(self.base, box.topleft)

        # 相机视野框（让玩家知道当前看到的是哪一块）
        vx = cam.offset_x / C.TILE_SIZE
        vy = cam.offset_y / C.TILE_SIZE
        pygame.draw.rect(
            screen, (150, 158, 172),
            (box.x + int(vx * TILE), box.y + int(vy * TILE),
             int(C.VIEW_W * TILE), int(C.VIEW_H * TILE)),
            width=1,
        )

        # 目标点
        if world.state == C.STATE_INFILTRATE:
            self._dot(screen, box, C.DATA_TERMINAL, C.COL_TERMINAL, 3)
        else:
            self._dot(screen, box, C.EXFIL_POINT, C.COL_EXFIL, 3)
            pygame.draw.circle(
                screen, (255, 255, 255),
                self._to_box(box, C.EXFIL_POINT),
                5 + int(1.5 * pulse), 1,
            )

        # 敌人：只显示"附近"或"已警觉"的，避免小地图变成全图透视、失去紧张感
        for e in world.enemies:
            if not e.alive:
                continue
            dist2 = (e.x - world.player.x) ** 2 + (e.y - world.player.y) ** 2
            alerted = e.role != Role.PATROL
            if dist2 > 16.0 ** 2 and not alerted:
                continue
            color = C.COL_ROLE.get(e.role.value, C.COL_ENEMY)
            self._dot(screen, box, (e.x, e.y), color, 2)

        # 玩家（带朝向的三角）
        px, py = self._to_box(box, (world.player.x, world.player.y))
        ang = world.player.facing
        tri = []
        for delta, r in ((0.0, 5.0), (2.5, 4.0), (-2.5, 4.0)):
            a = ang + delta
            tri.append((px + int(math.cos(a) * r), py + int(math.sin(a) * r)))
        pygame.draw.polygon(screen, (255, 255, 255), tri)
        pygame.draw.circle(screen, C.COL_PLAYER, (px, py), 2)

    # ---- 工具 ----
    @staticmethod
    def _to_box(box: pygame.Rect, pos) -> tuple:
        return (box.x + int(pos[0] * TILE), box.y + int(pos[1] * TILE))

    def _dot(self, screen, box, pos, color, radius) -> None:
        x, y = self._to_box(box, pos)
        pygame.draw.circle(screen, color, (x, y), radius)
        pygame.draw.circle(screen, (10, 12, 16), (x, y), radius, 1)
