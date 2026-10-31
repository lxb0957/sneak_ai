"""调试渲染层：叠加显示视野锥、听觉半径、射线、可疑度等。

按 F1 开关。这是面试演示的杀手锏 —— 让面试官直观看到 AI 的
感知范围与行为状态，而不是只看到一个会动的红点。
"""

import math

import pygame

from .. import constants as C
from ..core.camera import Camera
from ..core.grid import GridMap


class DebugRenderer:
    def __init__(self):
        self.enabled = False
        self.surf = pygame.Surface((C.WINDOW_W, C.WINDOW_H), pygame.SRCALPHA)

    def toggle(self) -> None:
        self.enabled = not self.enabled

    def draw(
        self,
        screen: pygame.Surface,
        cam: Camera,
        world,
        perception,
        player,
    ) -> None:
        if not self.enabled:
            return
        self.surf.fill((0, 0, 0, 0))
        self._draw_enemy_senses(cam, world, perception, player)
        screen.blit(self.surf, (0, 0))

    def _draw_enemy_senses(self, cam, world, perception, player) -> None:
        from ..core.renderer import vision_polygon
        from ..core.ui import font

        label_font = font(14)
        for e in world.enemies:
            ex, ey = cam.world_to_screen(e.x, e.y)
            # 视觉锥：和玩法层共用同一份"撞墙截断"的几何，
            # 否则调试视图会显示一个穿墙的大扇形，和实际判定对不上。
            view_range = e.view_range() if hasattr(e, "view_range") else perception.view_range
            r = view_range * C.TILE_SIZE
            pts = vision_polygon(e, world.grid, cam)
            if len(pts) >= 3:
                pygame.draw.polygon(self.surf, (*C.COL_FOV, 58), pts)
                pygame.draw.polygon(self.surf, (*C.COL_FOV, 130), pts, 1)
            # 朝向线
            pygame.draw.line(
                self.surf,
                C.COL_FOV,
                (ex, ey),
                (ex + math.cos(e.facing) * r * 0.35, ey + math.sin(e.facing) * r * 0.35),
                1,
            )
            # 听觉半径：画的是**名义半径**（空气里的上限），不是"这一圈都听得见"。
            # 实际听觉范围由声音代价场决定 —— 隔着墙会被削掉一截，
            # 所以真实的可闻区域是这张圆被墙"啃"过的样子，不是这个圆本身。
            pygame.draw.circle(
                self.surf,
                (*C.COL_HEAR, 50),
                (ex, ey),
                int(perception.hear_range * C.TILE_SIZE),
                1,
            )
            # 射线：命中玩家时画一条红色视线
            hit = perception.can_see(
                e.pos, e.facing, player.pos, world.grid, view_range=view_range
            )
            if hit > 0:
                px, py = cam.world_to_screen(player.x, player.y)
                pygame.draw.line(self.surf, C.COL_ENEMY, (ex, ey), (px, py), 1)

            # 追击/包抄/封路角色：画一条到目标点的连线
            role = getattr(e, "role", None)
            target = getattr(e, "target_tile", None)
            if role is not None and role.value != "patrol" and target is not None:
                tx, ty = cam.world_to_screen(target[0] + 0.5, target[1] + 0.5)
                pygame.draw.line(self.surf, C.COL_ENEMY, (ex, ey), (tx, ty), 1)

            # 可疑度 / 行为名标签（行为由行为树选出：patrol / investigate / chase…）
            susp = getattr(e, "suspicion", 0.0)
            state = getattr(e, "state", "patrol")
            label = f"{state} {susp:.0%}"
            color = C.COL_ENEMY if susp >= 0.35 else (200, 200, 200)
            txt = label_font.render(label, True, color)
            self.surf.blit(txt, (ex + 16, ey - 8))
