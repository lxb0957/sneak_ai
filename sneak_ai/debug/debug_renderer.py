"""调试渲染层：叠加显示视野锥、听觉半径、射线、可疑度等。

按 F1 开关。这是面试演示的杀手锏 —— 让面试官直观看到 AI 的
感知范围与行为状态，而不是只看到一个会动的红点。
"""

import math

import pygame

from .. import constants as C
from ..ai.trace import KIND_COLORS
from ..core.camera import Camera
from ..core.grid import GridMap
from ..core.i18n import t
from ..core.ui import font

# 面板显示最近几条。9 条 ≈ 屏幕上 150 像素高，1280x960 下不挡视野中心，
# 800x600 下也放得下；再多就会盖住玩家脚下那一圈。
PANEL_ROWS = 9


def _fit(f, text: str, max_w: int) -> str:
    """把一行文字裁到 max_w 以内，末尾用省略号。

    二分找切点而不是逐字试：`Font.size()` 本身要量一遍字形，
    40 个字逐字量是 O(n) 次调用，二分只要 6 次。
    """
    if f.size(text)[0] <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.size(text[:mid] + "…")[0] <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


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
        self._draw_trace(screen, world)

    # ---- 决策流面板 ----
    @staticmethod
    def panel_rect(size, n_rows: int = PANEL_ROWS) -> pygame.Rect:
        """面板几何。抽成静态方法是为了让测试能在**不渲染**的情况下断言
        "它落在窗口里" —— 只在截图里肉眼看，窗口一缩小就会重新溢出。"""
        sw, sh = size
        # 430 而不是更宽：底部居中那句"暴露！快找掩体"从 W/2 向两侧各伸约
        # 100 像素，面板再宽就会在 **1280 窗口**上压住它 —— 而那行字恰好
        # 是最需要看见的时候（可疑度刚过确认线）才出现的。
        w = min(430, sw - 20)
        h = 26 + n_rows * 17 + 10
        # 底栏（H-62）和处境文字（H-96）都在底部，所以整个面板要抬到它们
        # 上面去：压在体力条上会让玩家在最紧张的时候看不见体力。
        return pygame.Rect(10, max(0, sh - h - 122), w, h)

    def _draw_trace(self, screen, world) -> None:
        """左下角的决策流：谁、在第几秒、因为什么、改了什么主意。

        为什么值得占一块屏幕：F1 上的视野锥只回答"他现在看得见什么"，
        而面试里真正会被追问的是"他为什么突然回头" —— 那是一条跨越几秒的
        因果链（石子落地 → 起疑 → 转调查 → 9 秒后放弃），任何一个瞬间的
        截图都讲不出来。滚动的时间线可以。
        """
        trace = getattr(world, "trace", None)
        if trace is None:
            return
        rect = self.panel_rect(screen.get_size())
        panel = pygame.Surface(rect.size, pygame.SRCALPHA)
        panel.fill((8, 10, 14, 210))
        pygame.draw.rect(panel, (74, 84, 100, 230), panel.get_rect(), 1)
        screen.blit(panel, rect.topleft)

        f = font(13)
        screen.blit(font(13, True).render(
            t(f"AI 决策流 · 最近 {PANEL_ROWS} 条"), True, (152, 164, 180)),
            (rect.x + 9, rect.y + 5))
        rows = trace.recent(PANEL_ROWS)
        if not rows:
            screen.blit(f.render(t("（还没有事件：开局头几秒通常只有巡逻）"),
                                 True, (118, 120, 128)),
                        (rect.x + 9, rect.y + 24))
            return
        y = rect.y + 24
        for r in rows:
            color = KIND_COLORS.get(r.kind, (200, 200, 200))
            who = f"#{r.who}" if r.who else "--"
            line = _fit(f, t(f"{r.t:6.1f} {who} {r.text}"), rect.w - 20)
            screen.blit(f.render(line, True, color), (rect.x + 9, y))
            y += 17

    def _draw_enemy_senses(self, cam, world, perception, player) -> None:
        from ..core.renderer import vision_polygon

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
