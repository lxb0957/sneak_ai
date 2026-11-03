"""小地图：80x60 的地图 + 跟随相机，玩家很容易迷路，小地图是必需件。

实现要点（性能）：
墙体是**静态**的，所以整张底图在初始化时渲染一次到 Surface，
每帧只需要 blit 底图 + 画十几个小点。绝对不要在每帧里重画 4800 个格子。
"""

import math

import pygame

from .. import constants as C
from ..ai.coordination import Role
from .ui import draw_panel, render

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

        # 目标点：坐标来自当前关卡（world.terminals / world.exfil），
        # 不是 constants 里的全局常量 —— 否则第二关的撤离点会画在第一关的角落。
        if world.state == C.STATE_INFILTRATE:
            for _i, pt in world.remaining():
                self._dot(screen, box, pt, C.COL_TERMINAL, 3)
        else:
            self._dot(screen, box, world.exfil, C.COL_EXFIL, 3)
            pygame.draw.circle(
                screen, (255, 255, 255),
                self._to_box(box, world.exfil),
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


# 每格像素数的上下限：3 是"还看得出走廊"的下界，14 是"再大就超出窗口"的上界。
FULL_TILE_MIN = 3
FULL_TILE_MAX = 14


class FullMap:
    """整张关卡的俯视图（Tab 打开）。

    为什么非要有这个：小地图每格 2 像素，第 4 关 92 格宽的直廊被压成 184 像素，
    三处收窄段和通往总闸的支路全糊成一片灰；而窗口一次只显示 40x30 格，
    也就是**不到全图的两成**。玩家在长廊里走了半天，不知道自己在整条廊的哪一段 ——
    "看不到完整的地图"不是视觉问题，是**没有一张能一次看完的图**。

    刻意只画静态信息（墙、地面、目标、总闸）+ 玩家的位置和朝向：
    敌人不在上面，否则这张图就成了全图透视，潜行游戏紧张感的来源就没了。
    """

    def __init__(self, grid, max_box: tuple = (1160, 760)):
        self.grid = grid
        # 按整图能否塞进 max_box 选缩放档位，再用整数倍 —— 非整数倍会让墙线发虚
        fit = min(max_box[0] // max(grid.w, 1), max_box[1] // max(grid.h, 1))
        self.tile = max(FULL_TILE_MIN, min(fit, FULL_TILE_MAX))
        self.w = grid.w * self.tile
        self.h = grid.h * self.tile
        self.base = self._build()

    def _build(self) -> pygame.Surface:
        """整图烘焙一次。

        配色和 2 像素的小地图**故意相反**：小地图是"墙亮地暗"，因为那点尺寸下
        墙的轮廓才是唯一能认出来的东西；整图有 12 像素一格，玩家要认的是
        **能走的形状**，所以让地板亮、墙暗 —— 走廊、房间、三处收窄段才会自己
        从底色里浮出来。第一版照抄小地图的配色，结果整张图是一块灰板。
        """
        surf = pygame.Surface((self.w, self.h)).convert()
        surf.fill((10, 13, 18))          # 界外：比墙更暗，地图边界自己会显出来
        floor = (78, 88, 104)
        wall = (26, 31, 40)
        t = self.tile
        for y in range(self.grid.h):
            for x in range(self.grid.w):
                color = wall if self.grid.is_wall(x, y) else floor
                surf.fill(color, (x * t, y * t, t, t))
        return surf

    def _to_screen(self, origin: tuple, pos) -> tuple:
        return (origin[0] + int(pos[0] * self.tile),
                origin[1] + int(pos[1] * self.tile))

    def draw(self, screen, world, cam) -> None:
        W, H = screen.get_size()
        # 底下一层压暗，但不全黑：关掉全图的那一帧要能立刻回到游戏
        veil = pygame.Surface((W, H), pygame.SRCALPHA)
        veil.fill((4, 6, 10, 196))
        screen.blit(veil, (0, 0))

        box = pygame.Rect((W - self.w) // 2, (H - self.h) // 2, self.w, self.h)
        draw_panel(screen, box.inflate(18, 18), alpha=235)
        screen.blit(self.base, box.topleft)

        # 当前视野框：让"我看到的这一块"和"整张图"能对上号。
        # 用暗一点的灰蓝，纯白会把视野框读成一道墙
        vx = cam.offset_x / C.TILE_SIZE * self.tile
        vy = cam.offset_y / C.TILE_SIZE * self.tile
        pygame.draw.rect(
            screen, (108, 122, 146),
            (box.x + int(vx), box.y + int(vy),
             int(C.VIEW_W * self.tile), int(C.VIEW_H * self.tile)),
            width=2,
        )

        # 目标点：已完成的不画，剩下的用终端色
        # remaining() 产的是 (序号, 坐标)，别把序号当坐标乘缩放
        if world.state == C.STATE_INFILTRATE:
            targets = [pos for _i, pos in world.remaining()]
        else:
            targets = [world.exfil]
        for pt in targets:
            x, y = self._to_screen(box.topleft, pt)
            pygame.draw.circle(screen, C.COL_TERMINAL, (x, y), 5)
            pygame.draw.circle(screen, (10, 12, 16), (x, y), 5, 1)

        # 总闸：未用的亮琥珀色、用过的灰掉 —— 决策依据必须一眼可读
        for node in world.power:
            x, y = self._to_screen(box.topleft, (node["x"], node["y"]))
            col = (96, 92, 88) if node.get("used") else (232, 176, 72)
            pygame.draw.rect(screen, col, (x - 5, y - 5, 10, 10), border_radius=2)
            pygame.draw.rect(screen, (10, 12, 16), (x - 5, y - 5, 10, 10), 1,
                             border_radius=2)

        # 玩家：三角指示朝向，和小地图保持一致的白
        px, py = self._to_screen(box.topleft, (world.player.x, world.player.y))
        poly = []
        for delta, r in ((0.0, 9.0), (2.4, 7.0), (-2.4, 7.0)):
            a = world.player.facing + delta
            poly.append((px + int(math.cos(a) * r), py + int(math.sin(a) * r)))
        pygame.draw.polygon(screen, (255, 255, 255), poly)
        pygame.draw.polygon(screen, (10, 12, 16), poly, 1)

        # 标题 + 说明：居中写在图的上方。左对齐会正好压住 HUD 的总闸那一行，
        # 而屏幕上沿中间是空的 —— 摊开地图时不该出现两层字叠在一起
        name = world.level_name or ""
        title = f"第{world.level_index + 1}关｜{name}"
        t_surf = render(title, 22, (232, 236, 244), bold=True)
        screen.blit(t_surf, ((W - t_surf.get_width()) // 2, box.y - 58))
        hint = render("TAB 关闭  ·  地图不含敌人位置", 17, (144, 152, 166))
        screen.blit(hint, ((W - hint.get_width()) // 2, box.y - 28))
