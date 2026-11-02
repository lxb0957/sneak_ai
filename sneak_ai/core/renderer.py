"""程序化像素风渲染器。

不依赖外部素材，用 pygame 纯代码画出：
- 4 方向行走动画的像素小人（头/身/腿/朝向眼睛）
- 地面阴影（椭圆软影）与角色立体感
- 数据终端 / 撤离点的发光标记 + 漂浮粒子
- 警觉状态的红色描边反馈

这是"全程序化像素风"的核心：面试能讲清楚每个元素为什么这么画。
"""

import math
import random

import pygame

from .. import constants as C
from .sprites import SpriteBank


def vision_polygon(enemy, grid, cam, steps: int = 20) -> list:
    """算出一个敌人视野锥的屏幕多边形（逐角度 DDA，撞墙即截断）。

    玩法层（淡显锥体）和 F1 调试层共用这一份几何，
    避免"调试里看得到、实际看不见"这种自相矛盾的显示。
    """
    view = enemy.view_range()
    if view <= 0:
        return []
    ex, ey = cam.world_to_screen(enemy.x, enemy.y)
    ts = C.TILE_SIZE
    fov = enemy.perception.fov_angle if enemy.perception else math.radians(110)
    pts = [(ex, ey)]
    for i in range(steps + 1):
        a = enemy.facing - fov / 2 + fov * i / steps
        ca, sa = math.cos(a), math.sin(a)
        r = 0.0
        while r < view:
            nx, ny = enemy.x + ca * r, enemy.y + sa * r
            if grid.is_wall(int(nx), int(ny)):
                break
            r += 0.25
        r = min(r, view)
        pts.append((ex + ca * r * ts, ey + sa * r * ts))
    return pts


class PixelRenderer:
    """负责角色、目标点、粒子的像素风绘制。"""

    def __init__(self):
        # 粒子池：数据终端/撤离点周围的漂浮光点
        self._particles = []
        self._rng = random.Random(7)
        # 特效图层：视野锥共用一张，每帧只清空一次（避免逐个敌人建 Surface）
        self._fx = None
        self._vignette = None
        self._dark = None   # 黑灯遮罩（与视锥层分开：一张常驻、一张每帧覆盖）
        self._icon_cache = {}
        self._shadow = None
        self.sprites = SpriteBank()

    # ============ 角色绘制 ============
    def draw_character(self, screen, sx, sy, facing, body_color, head_color=None,
                       is_player=False, moving=False, alert=False, sneaking=False,
                       accent=None):
        """画一个战术小人（像素精灵，预烘焙）。

        造型从"圆头 + 方身 + 线段腿"换成了真正的像素角色：
        头盔 + 护目镜 + 战术背心 + 腰带 + 弹匣袋 + 靴子，1px 深色描边。
        绘制成本不变 —— 精灵在第一次用到时烘焙好放进缓存，之后每帧只有一次 blit。
        """
        if accent is None:
            if is_player:
                accent = C.COL_VISOR_PLAYER
            elif alert:
                accent = C.COL_VISOR_HUNT
            else:
                accent = C.COL_VISOR_ENEMY

        # 脚下软阴影（预渲染一次，别每帧建 Surface）
        if self._shadow is None:
            self._shadow = pygame.Surface((26, 12), pygame.SRCALPHA)
            pygame.draw.ellipse(self._shadow, (0, 0, 0, 80), (0, 0, 26, 12))
        screen.blit(self._shadow, (sx - 13, sy - 3))

        spr = self.sprites.sprite(body_color, facing, moving,
                                  crouch=sneaking, accent=accent)
        ax, ay = self.sprites.anchor
        screen.blit(spr, (sx - ax, sy - ay))

        # 被追击/警觉：脚下一圈红环，远看也能一眼找到"谁在追我"
        if alert and not is_player:
            pygame.draw.ellipse(screen, (232, 70, 58), (sx - 13, sy - 5, 26, 12), 1)

    # ============ 噪音可视化（听觉系统本来是个黑箱） ============
    @staticmethod
    def _lerp_color(a, b, t: float):
        t = max(0.0, min(1.0, t))
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def draw_noise(self, screen, cam, world) -> None:
        """画两样东西：玩家脚下的"噪音圈"、以及投石落点的声波。

        为什么值得占画面：在这之前，听觉机制对玩家完全是不可见的 ——
        你只能通过"莫名其妙被发现"来反推"跑动会招人"。
        把有效听觉半径画成虚线圆之后，三档移动的取舍就变成了看得见的几何：
        潜行 3.6 格 / 走路 5.8 格 / 冲刺 7.5 格。玩家不需要读文档。
        """
        floor = C.COL_FLOOR_A
        ts = C.TILE_SIZE

        # ---- 玩家噪音圈（虚线，避免和实线的视野锥混淆）----
        p = world.player
        if p is not None:
            r = p.noise_radius()
            if r > 0.05:
                if p.sprinting:
                    col = C.COL_NOISE_SPRINT
                elif p.sneaking:
                    col = C.COL_NOISE_SNEAK
                else:
                    col = C.COL_NOISE_WALK
                cx, cy = cam.world_to_screen(p.x, p.y)
                rad = int(r * ts)
                # 32 段、隔一段画一段 = 虚线；再用低频呼吸让"响"的时候更扎眼
                breathe = 0.55 + 0.45 * math.sin(world.elapsed * 6.0)
                dim = self._lerp_color(col, floor, 0.15 + 0.20 * (1.0 - breathe))
                for i in range(0, 48, 2):
                    a0 = i * math.pi / 24
                    a1 = a0 + math.pi / 50
                    x0, y0 = cx + math.cos(a0) * rad, cy + math.sin(a0) * rad
                    x1, y1 = cx + math.cos(a1) * rad, cy + math.sin(a1) * rad
                    pygame.draw.line(screen, dim, (x0, y0), (x1, y1), 2)

        # ---- 投石落点：扩散的声波 ----
        for ping in world.pings:
            k = ping["age"] / max(1e-6, ping["life"])
            if k >= 1.0:
                continue
            cx, cy = cam.world_to_screen(ping["x"], ping["y"])
            # 半径先快后慢地扩到"能被听见的范围"：暗示声音传了多远
            rad = int(ping["r"] * ts * (0.30 + 0.70 * k ** 0.5))
            if rad <= 1:
                continue
            if ping["kind"] == "stone":
                base = (255, 216, 140)
            elif ping["kind"] == "body":
                # 尸体警报是红环：它是"你被记账了"，不是"那边有响动"
                base = (255, 96, 84)
            elif ping["kind"] == "pickup_ammo":
                base = C.COL_PICKUP_AMMO
            elif ping["kind"] == "pickup_stone":
                base = C.COL_PICKUP_STONE
            elif ping["kind"] == "pickup_shield":
                base = C.COL_PICKUP_SHIELD
            else:
                base = C.COL_HEAR
            col = self._lerp_color(base, floor, 0.25 + 0.7 * k)
            pygame.draw.circle(screen, col, (cx, cy), rad, 2)
            if rad > 8:
                pygame.draw.circle(screen, self._lerp_color(base, floor, 0.55 + 0.4 * k),
                                   (cx, cy), max(2, rad - 5), 1)

    def draw_stones(self, screen, cam, world) -> None:
        """画飞行中的石子：地面阴影固定，石子本体按抛物线高度上浮。

        4 像素的石头在复杂地板上太容易看丢，所以额外加两件事：
        尾迹（让"飞过去了"这件事有方向）和落点小圈（让玩家知道自己在往哪扔）。
        """
        ts = C.TILE_SIZE
        for st in world.stones:
            cx, cy = cam.world_to_screen(st.x, st.y)
            h = int(st.h * ts)

            # 落点提示：一个很淡的小圈，帮玩家确认"这一下会落在哪"
            lx, ly = cam.world_to_screen(st.tx, st.ty)
            pygame.draw.circle(screen, (86, 82, 72), (lx, ly), 5, 1)
            pygame.draw.circle(screen, (54, 54, 52), (lx, ly), 2)

            # 尾迹：取倒退 30/60ms 的两个采样点，越远越淡
            for back, col in ((0.03, (196, 186, 164)), (0.06, (126, 120, 108))):
                bx, by, bh = st.sample(back)
                sx, sy = cam.world_to_screen(bx, by)
                pygame.draw.rect(screen, col, (sx - 1, sy - int(bh * ts) - 1, 2, 2))

            # 阴影：越高越小（用尺寸而非 alpha 表达，省一张 Surface）
            sh = max(3, int(8 - st.h * 3))
            pygame.draw.ellipse(screen, (22, 24, 28),
                                (cx - sh // 2, cy + 1, sh, max(2, sh // 2)))

            px, py = cx, cy - h
            # 一圈很淡的暖色光晕：保证 5px 的小石头在复杂地板上也能被看见
            pygame.draw.circle(screen, (74, 68, 56), (px, py), 6)
            pygame.draw.rect(screen, (28, 30, 36), (px - 3, py - 3, 6, 6))
            pygame.draw.rect(screen, (186, 178, 160), (px - 2, py - 2, 5, 5))
            pygame.draw.rect(screen, (244, 238, 222), (px - 2, py - 2, 2, 2))

    # ============ 枪械：弹道 / 尸体 / 重生预警 ============
    def draw_shots(self, screen, cam, world) -> None:
        """画弹道曳光：一条很快淡出的亮线 + 枪口闪光 + 命中火花。

        为什么用"线"而不是"飞行的子弹"：hitscan 是瞬时判定，
        画一颗慢慢飞的子弹会骗玩家（明明命中了，视觉上还在半路）。
        曳光只负责告诉玩家"这一枪从哪打到哪"，所以它必须短命（0.1s）。
        """
        for s in world.shots:
            k = s["age"] / max(1e-6, s["life"])
            if k >= 1.0:
                continue
            a = 1.0 - k
            x0, y0 = cam.world_to_screen(s["x0"], s["y0"])
            x1, y1 = cam.world_to_screen(s["x1"], s["y1"])

            # 主线：越旧越暗、越细
            col = tuple(int(c * a) for c in C.COL_TRACER)
            pygame.draw.line(screen, col, (x0, y0), (x1, y1), 2 if k < 0.5 else 1)

            # 枪口闪光：开火的前 1/3 生命期里最亮
            if k < 0.35:
                r = int(9 * (1.0 - k / 0.35)) + 2
                pygame.draw.circle(screen, C.COL_MUZZLE, (x0, y0), r)
                pygame.draw.circle(screen, (255, 250, 232), (x0, y0), max(1, r // 2))

            # 命中：敌人身上是暗红溅射，墙上是橙色火花
            if s["kind"] == "enemy":
                for ang in (0.5, 2.1, 3.9, 5.4):
                    rr = 3 + 7 * k
                    pygame.draw.circle(
                        screen, (220, 70, 60),
                        (int(x1 + math.cos(ang) * rr), int(y1 + math.sin(ang) * rr)),
                        max(1, int(3 * a)),
                    )
            elif s["kind"] == "wall":
                pygame.draw.circle(screen, C.COL_IMPACT, (x1, y1), max(1, int(4 * a)))

    def draw_hitmarks(self, screen, cam, world) -> None:
        """命中记号：打中身体的那一刻在命中点闪一个白 X。

        为什么非得单独画一个记号：曳光只说明"子弹飞出过"，混战里两条线
        一条打墙一条打人，玩家分不出来。X 是射击游戏里最省事的
        "你打中了"确认，代价是每帧几次 draw.line。

        形状设计成"先小后大、快速变淡"：放大给人"这一下很结实"的力度感，
        变淡保证它绝不挡住接下来的画面。
        """
        for h in world.hit_marks:
            k = h["age"] / max(1e-6, h["life"])
            if k >= 1.0:
                continue
            cx, cy = cam.world_to_screen(h["x"], h["y"])
            # 0.6 → 1.15 倍：扩散得比曳光快，读起来像"咔"的一下
            r = int(7 + 9 * k)
            a = 1.0 - k * k
            # 逐通道夹到 [0,255]：a≈1 时 255*a+30=285 会直接抛
            # "invalid color" —— 而且只在真打中人的瞬间触发，测试很难撞上。
            col = (
                min(255, max(0, int(255 * a + 30))),
                min(255, max(0, int(246 * a + 20))),
                min(255, max(0, int(240 * a + 20))),
            )
            w = 3 if k < 0.45 else 2
            for dx, dy in ((1, 1), (1, -1)):
                pygame.draw.line(screen, col,
                                 (cx - r * dx, cy - r * dy),
                                 (cx + r * dx, cy + r * dy), w)

    def draw_pickups(self, screen, cam, world, t: float) -> None:
        """沿途补给：弹药包 / 石子堆 / 护盾。

        三件事必须在远处就能读出来 —— 玩家没时间凑近看：
        1. **形状区分类型**：弹药 = 三颗立着的子弹；石子 = 三粒小圆；护盾 = 六边形。
           光靠颜色不行：暖色地板上黄蓝都容易糊，形状才是远视距的信息通道。
        2. **常驻的柔和呼吸光晕**：补给不是敌人，不该抢视线，但也不能藏在地板纹理里。
        3. **拿走的不再画**：否则玩家会反复绕回空点，那是纯粹的挫败感。
        """
        for it in world.pickups:
            if it["taken"]:
                continue
            cx, cy = cam.world_to_screen(it["x"], it["y"])
            if not (-40 < cx < C.WINDOW_W + 40 and -40 < cy < C.WINDOW_H + 40):
                continue
            kind = it["kind"]
            col = {
                "ammo": C.COL_PICKUP_AMMO,
                "stone": C.COL_PICKUP_STONE,
                "shield": C.COL_PICKUP_SHIELD,
            }.get(kind, C.COL_PICKUP_STONE)
            pulse = 0.5 + 0.5 * math.sin(t * 3.0 + it["x"] * 0.7)
            glow = self._lerp_color(col, (24, 26, 30), 0.60 + 0.26 * pulse)
            pygame.draw.circle(screen, glow, (cx, cy), int(11 + 2 * pulse), 1)

            if kind == "ammo":
                for i in (-1, 0, 1):
                    bx = cx + i * 6
                    pygame.draw.rect(screen, col, (bx - 2, cy - 9, 4, 12),
                                     border_radius=1)
                    pygame.draw.rect(screen, (255, 246, 220),
                                     (bx - 2, cy - 9, 4, 3), border_radius=1)
            elif kind == "stone":
                for dx, dy in ((-6, 3), (0, -3), (6, 3)):
                    pygame.draw.circle(screen, col, (cx + dx, cy + dy), 4)
                    pygame.draw.circle(screen, (236, 240, 246),
                                       (cx + dx - 1, cy + dy - 1), 1)
            else:
                # 护盾：六边形 + 内圈。蓝色在整张暖色地图里是唯一的冷色，最跳
                pts = []
                for i in range(6):
                    a = math.pi / 6 + i * math.pi / 3
                    pts.append((cx + math.cos(a) * 11, cy + math.sin(a) * 11))
                pygame.draw.polygon(screen, col, pts, 2)
                pygame.draw.circle(screen, col, (cx, cy), 4, 1)

    def draw_power(self, screen, cam, world, t: float) -> None:
        """总闸：一个配电箱 + 一根手柄。三种状态必须一眼分辨。

        为什么状态要画得这么明确：这个机制的全部意义是"什么时候用"，
        如果玩家看不出"还能不能用"，那它就从决策退化成了试错。
        - 没拉过：琥珀色呼吸 + 外圈脉冲（可交互的东西在场景里应该发光）
        - 已拉下、灯还黑着：绿色常亮（正在进行中的效果）
        - 已烧掉：灰褐色，手柄下垂（一次性用完了）
        """
        for node in world.power:
            cx, cy = cam.world_to_screen(node["x"], node["y"])
            if not (-60 < cx < C.WINDOW_W + 60 and -60 < cy < C.WINDOW_H + 60):
                continue
            used = node.get("used")

            if used and world.in_blackout:
                body, handle = (46, 86, 64), (120, 220, 150)
            elif used:
                body, handle = (52, 48, 44), (120, 108, 96)
            else:
                pulse = 0.5 + 0.5 * math.sin(t * 3.4 + node["x"] * 0.3)
                body = self._lerp_color((255, 176, 60), (120, 74, 24), 0.35 * pulse)
                handle = (255, 214, 120)
                pygame.draw.circle(screen, self._lerp_color((255, 176, 60), (30, 26, 22), 0.7),
                                   (cx, cy), int(15 + 3 * pulse), 1)

            # 箱体 + 顶盖
            pygame.draw.rect(screen, (18, 18, 20), (cx - 10, cy - 8, 20, 17),
                             border_radius=2)
            pygame.draw.rect(screen, body, (cx - 9, cy - 7, 18, 15), border_radius=2)
            pygame.draw.rect(screen, handle, (cx - 6, cy - 5, 5, 2))
            # 手柄：没拉 = 朝上；已拉 = 朝下
            if used:
                pygame.draw.line(screen, handle, (cx + 2, cy + 5), (cx + 6, cy - 2), 2)
            else:
                pygame.draw.line(screen, handle, (cx + 2, cy - 2), (cx + 6, cy + 5), 2)
            pygame.draw.circle(screen, (240, 240, 230), (cx + 2, cy), 1)

    def draw_blackout(self, screen, cam, world, player) -> None:
        """黑灯遮罩：整屏压暗，玩家周围留一圈"手电光"。

        为什么不是全屏纯黑：玩家看不见敌人，敌人也看不见玩家 —— 这是公平的，
        但"什么都看不见"只会让人停在原地。留一圈光，玩家能看清脚下和近处，
        远处的追兵变成暗影 —— 这正是黑灯想要的体验：**近处可操作，远处不可知**。
        """
        if not world.in_blackout or player is None:
            return
        if self._dark is None:
            self._dark = pygame.Surface((C.WINDOW_W, C.WINDOW_H), pygame.SRCALPHA)
        # 剩余时间不多时逐渐变亮，给玩家"要来电了"的预警
        left = world.blackout_left
        fade = min(1.0, left / 1.5)
        alpha = int(150 * fade)
        self._dark.fill((6, 10, 18, alpha))
        px, py = cam.world_to_screen(player.x, player.y)
        # 由内到外渐暗：一圈圈覆盖，越外越接近全暗
        for i in range(1, 7):
            r = int(66 + i * 26)
            a = int(alpha * i / 7.0)
            pygame.draw.circle(self._dark, (6, 10, 18, a), (px, py), r)
        screen.blit(self._dark, (0, 0))

    def draw_shield_aura(self, screen, cam, player, t: float) -> None:
        """玩家身上的护盾光圈。

        必须是"看得见的资源"：护盾只在被抓住的那一瞬间生效，
        如果玩家不知道自己带着一层，那它就是个随机事件而不是决策。
        """
        if getattr(player, "shield", 0) <= 0:
            return
        cx, cy = cam.world_to_screen(player.x, player.y)
        cy -= int(C.TILE_SIZE * 0.45)          # 圈住身体，不是脚底
        breathe = 0.5 + 0.5 * math.sin(t * 2.4)
        r = int(C.TILE_SIZE * 0.52)
        col = self._lerp_color(C.COL_PICKUP_SHIELD, (24, 26, 30),
                               0.30 + 0.35 * breathe)
        pygame.draw.circle(screen, col, (cx, cy), r, 2)
        pygame.draw.circle(screen, C.COL_PICKUP_SHIELD, (cx, cy), r - 5, 1)

    def draw_downed(self, screen, cam, world, t: float) -> None:
        """画倒地的哨兵 + 重生预警环。

        尸体必须和活人**一眼可分**：去饱和的暗色剪影趴在地上，
        不画护目镜（活人的护目镜是唯一的亮色，这是最强的区分信号）。
        重生预警环则提前 1.3 秒出现在落点上，玩家还有机会离开。
        """
        for e in world.enemies:
            if e.alive:
                continue
            sx, sy = cam.world_to_screen(e.x, e.y)
            # 倒地：横向铺开的深色块，重心偏下
            pygame.draw.ellipse(screen, (30, 30, 34), (sx - 13, sy - 4, 26, 12))
            pygame.draw.ellipse(screen, C.COL_DOWNED, (sx - 11, sy - 6, 22, 10))
            pygame.draw.circle(screen, (74, 68, 70), (sx - 7, sy - 4), 4)

            # 重生倒计时：头顶一个逐渐收缩的暗环，读得出"还剩多久"
            p = max(0.0, min(1.0, e.dead_timer / C.ENEMY_RESPAWN))
            if p > 0.0:
                arc = pygame.Rect(sx - 12, sy - 26, 24, 24)
                pygame.draw.arc(screen, (70, 62, 64), arc, 0, 2 * math.pi, 1)
                pygame.draw.arc(screen, (196, 92, 84), arc,
                                0, 2 * math.pi * p, 2)

            # 预警落点：脉冲圆环 + 向内收的十字
            if e.respawn_at is not None:
                rx, ry = cam.world_to_screen(*e.respawn_at)
                pulse = 0.5 + 0.5 * math.sin(t * 12.0)
                rad = int(12 + 6 * pulse)
                pygame.draw.circle(screen, C.COL_RESPAWN, (rx, ry), rad, 2)
                pygame.draw.circle(screen, (60, 120, 160), (rx, ry), rad + 4, 1)
                for d in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    pygame.draw.line(
                        screen, C.COL_RESPAWN,
                        (rx + d[0] * (rad - 5), ry + d[1] * (rad - 5)),
                        (rx + d[0] * (rad - 1), ry + d[1] * (rad - 1)), 2,
                    )

    # ============ 瞄准准星 ============
    def draw_aim_reticle(self, screen, sx: int, sy: int, armed: bool) -> None:
        """鼠标正指着某个哨兵时，在它**身体中心**画四角准星。

        为什么要给这个反馈：判定是"吸附到本体"，而本体画在脚底上方 0.7 格。
        没有准星时，玩家无法判断"我这下点的是人还是它身后的墙" ——
        只能靠打空几次去猜。有准星之后，规则变成一眼可见：
        **有准星 = 这一枪打得中**（没准星要么没瞄准、要么被墙挡）。

        重心画在 sy - 22（约 0.7 格）而不是脚底：准星要圈住"眼睛看到的身体"。
        """
        if not armed:
            return
        cx, cy = sx, sy - int(C.WEAPON_AIM_OFFSET * C.TILE_SIZE) - 3
        r, arm = 13, 5
        col = C.COL_MUZZLE
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            x0, y0 = cx + dx * r, cy + dy * r
            pygame.draw.line(screen, col, (x0, y0), (x0 - dx * arm, y0), 2)
            pygame.draw.line(screen, col, (x0, y0), (x0, y0 - dy * arm), 2)

    # ============ 视野锥（常驻淡显） ============
    def draw_vision_cones(self, screen, cam, world) -> None:
        """把所有敌人的视野锥画成淡色扇形（**按墙体遮挡裁剪**）。

        为什么常驻显示：这是潜行游戏的"可读性"底线 ——
        玩家需要一个能判断"哪里安全"的视觉线索，否则只能靠背板。

        为什么必须裁剪：最初的版本直接画一条半径扇形，锥体会穿墙而过，
        玩家看到墙后一片黄就会以为"那边被盯着"，实际那里是安全的。
        误导性的信息比没有信息更糟 —— 现在逐角度做 DDA 采样，
        射线碰到墙就停在那儿，锥体轮廓和真实遮挡一致。
        """
        if self._fx is None:
            self._fx = pygame.Surface((C.WINDOW_W, C.WINDOW_H), pygame.SRCALPHA)
        fx = self._fx
        fx.fill((0, 0, 0, 0))

        ts = C.TILE_SIZE
        grid = world.grid
        px0, py0 = world.player.x, world.player.y
        for e in world.enemies:
            # 只画"玩家附近"的视野锥：远处的锥体贴图除了刷屏没有信息量
            if (e.x - px0) ** 2 + (e.y - py0) ** 2 > 15.0 ** 2:
                continue
            view = e.view_range()
            if view <= 0:
                continue
            rng = view * ts
            ex, ey = cam.world_to_screen(e.x, e.y)
            if ex < -rng or ey < -rng or ex > C.WINDOW_W + rng or ey > C.WINDOW_H + rng:
                continue
            hot = e.suspicion >= C.SUSPICION_CURIOUS or e.role.value != "patrol"
            base = (255, 92, 74) if hot else (255, 205, 105)
            # 加法混合而不是普通 alpha：锥体是"光"，应该把地面照亮，
            # 而不是往深灰地板上糊一层半透明的棕色（那样看起来像阴影）。
            #
            # 注意：BLEND_RGBA_ADD 会**忽略 alpha**，直接把 RGB 加到目标上。
            # 所以亮度必须预先乘进颜色里（不能靠 alpha 调），否则会得到一块纯黄。
            k = 0.34 if hot else 0.20
            fill = (int(base[0] * k), int(base[1] * k), int(base[2] * k), 255)
            edge = (int(base[0] * k * 2.4), int(base[1] * k * 2.4),
                    int(base[2] * k * 2.4), 255)
            pts = vision_polygon(e, grid, cam)
            if len(pts) >= 3:
                pygame.draw.polygon(fx, fill, pts)
                # 描一道更亮的边：多个锥体叠在一起时才分得清"谁的视野"
                pygame.draw.polygon(fx, edge, pts, 1)

        # 加法叠加：锥体重叠处自然更亮，和真实光照的直觉一致
        screen.blit(fx, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

    # ============ 头顶状态图标 ============
    def draw_status_icon(self, screen, sx, sy, icon: str, t: float) -> None:
        """在角色头顶画 ! / ? 气泡：让"AI 现在在想什么"一眼可见。

        气泡必须**悬在头顶之上**，不能压住身体：早期版本放在 sy-50、
        半径 16px，正好把哨兵的头和护目镜盖掉 —— 远看就是"一个橙色圆球
        在走"，反而看不出那是个人。现在抬高到 sy-64 并收小一圈，
        再加一个小尖角指回角色，既清楚又是"气泡"而不是"球"。
        """
        if not icon:
            return
        badge = self._icon_cache.get(icon)
        if badge is None:
            from .ui import font

            f = font(15, bold=True)
            fill = (226, 54, 44) if icon == "!" else (240, 170, 52)
            glyph = f.render(icon, True, (255, 255, 255))
            r = max(glyph.get_width(), glyph.get_height()) // 2 + (7 if icon == "!" else 5)
            badge = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(badge, (14, 16, 20), (r, r), r)
            pygame.draw.circle(badge, fill, (r, r), r - 2)
            badge.blit(glyph, glyph.get_rect(center=(r, r)))
            self._icon_cache[icon] = badge

        # 追击时上下跳动更明显，调查时轻微浮动 —— 用动作区分紧急程度
        amp = 3.0 if icon == "!" else 1.5
        bob = int(math.sin(t * (8.0 if icon == "!" else 5.0)) * amp)
        x = sx - badge.get_width() // 2
        y = sy - 64 + bob
        screen.blit(badge, (x, y))
        # 小尖角：把气泡和它下面那个人连起来
        cx = sx
        base = y + badge.get_height() - 2
        pygame.draw.polygon(screen, (14, 16, 20),
                            [(cx - 4, base - 1), (cx + 4, base - 1), (cx, base + 5)])

    def draw_awareness(self, screen, sx, sy, suspicion: float,
                       confirmed: bool, t: float) -> None:
        """正在看着你的哨兵：脚下一圈"可疑度弧"，够了就变成锁定括号。

        为什么需要它：暴露度条只告诉你"全场最高可疑度是多少"，
        但没告诉你**是谁在攒这个数**。玩家看到条子在涨却不知道躲谁，
        只能满地乱跑。这一圈把"是谁 / 还差多少"直接标在 cause 身上。

        画法：脚下 20px 的圆弧，从 12 点方向顺时针填充 suspicion；
        到确认线时换成四角括号（锁定），并且轻微放大呼吸。
        """
        cx, cy = sx, sy - 4
        r = 20
        if confirmed:
            # 锁定：四角括号，比圆弧更"锐"，一眼区分"发现"和"在看我"
            k = 7
            arm = 6 + int(1.5 + 1.5 * math.sin(t * 10))
            col = C.COL_DANGER
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                px, py = cx + dx * r, cy + dy * r
                pygame.draw.line(screen, col, (px, py), (px - dx * arm, py), 2)
                pygame.draw.line(screen, col, (px, py), (px, py - dy * arm), 2)
            return

        s = max(0.0, min(1.0, suspicion / C.SUSPICION_CONFIRM))
        if s <= 0.02:
            return
        col = C.COL_SUSPECT if s < 0.75 else C.COL_DANGER
        # pygame 的 arc 角度是逆时针、0 在 3 点方向；从 12 点顺时针 = 90° 起、负方向
        start = math.pi / 2
        end = start - s * 2 * math.pi
        pygame.draw.arc(screen, (20, 22, 28),
                        (cx - r, cy - r, r * 2, r * 2), end, start, 4)
        pygame.draw.arc(screen, col, (cx - r, cy - r, r * 2, r * 2), end, start, 2)

    # ============ 屏幕边缘危险氛围 ============
    def draw_vignette(self, screen, intensity: float) -> None:
        """被盯上时的红色边缘光：不遮挡视野，但强烈提示"你暴露了"。

        实现上刻意拆成 4 条边而不是一整张全屏 Surface：
        全屏 alpha blit 每帧要混合 1280x960 个像素（实测 ~2.5ms），
        只混合 4 条边（约占 45% 面积）能省下一半以上，视觉完全一样。
        """
        if intensity <= 0.02:
            return
        if self._vignette is None:
            self._vignette = self._build_vignette()
        a = int(255 * max(0.0, min(1.0, intensity)))
        for surf, pos in self._vignette:
            surf.set_alpha(a)
            screen.blit(surf, pos)

    @staticmethod
    def _build_vignette():
        """预渲染 4 条渐变边：越靠屏幕外沿越浓，往内淡出。"""
        color = (196, 26, 26)
        depth = 150
        W, H = C.WINDOW_W, C.WINDOW_H

        def gradient(w, h, vertical):
            s = pygame.Surface((w, h), pygame.SRCALPHA)
            span = h if vertical else w
            for i in range(span):
                fade = 1.0 - i / span
                alpha = int(86 * fade ** 2.2)
                if alpha <= 0:
                    continue
                if vertical:
                    s.fill((*color, alpha), (0, i, w, 1))
                else:
                    s.fill((*color, alpha), (i, 0, 1, h))
            return s

        top = gradient(W, depth, True)
        bottom = pygame.transform.flip(top, False, True)
        left = gradient(depth, H - depth * 2, False)
        right = pygame.transform.flip(left, True, False)
        return [
            (top, (0, 0)),
            (bottom, (0, H - depth)),
            (left, (0, depth)),
            (right, (W - depth, depth)),
        ]

    @staticmethod
    def _shade(color, factor: float):
        """按 factor 缩放颜色亮度（factor>1 变亮，<1 变暗），并截断到 0~255。"""
        return tuple(max(0, min(255, int(c * factor))) for c in color[:3])

    # ============ 目标点绘制 ============
    def draw_goal(self, screen, cx, cy, kind, pulse, has_data=False):
        """画数据终端 / 撤离点：发光底座 + 图标 + 漂浮粒子。

        kind: "terminal"（数据终端）或 "exit"（撤离点）
        pulse: 0~1 的脉动相位（由时间驱动）
        """
        glow = int(120 + 80 * pulse)

        # 发光底座（径向渐变用多层圆模拟）
        base_colors = (
            ((80, 220, 160), (30, 140, 100)) if kind == "terminal"
            else ((120, 200, 255), (40, 110, 160))
        )
        for i, radius in enumerate(range(26, 8, -6)):
            a = int(20 + 40 * (1 - i / 4) * pulse)
            col = (*base_colors[0], a)
            surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, col, (radius, radius), radius)
            screen.blit(surf, (cx - radius, cy - radius))

        # 核心图标
        if kind == "terminal":
            # 数据终端：一个带屏幕的小方块
            body = pygame.Rect(cx - 10, cy - 10, 20, 20)
            pygame.draw.rect(screen, (40, 50, 60), body, border_radius=3)
            pygame.draw.rect(screen, (60, 220, 170), (cx - 7, cy - 7, 14, 14),
                             border_radius=2)
            pygame.draw.line(screen, (200, 255, 240), (cx - 4, cy), (cx + 4, cy), 1)
        else:
            # 撤离点：向上的箭头（绿色出口）
            pygame.draw.polygon(screen, (60, 220, 120),
                                [(cx, cy - 10), (cx - 9, cy), (cx - 4, cy),
                                 (cx - 4, cy + 9), (cx + 4, cy + 9), (cx + 4, cy),
                                 (cx + 9, cy)])
        # 如果已拿数据，撤离点高亮
        if has_data and kind == "exit":
            pygame.draw.circle(screen, (255, 255, 255), (cx, cy), 24, 2)

        # 生成/更新漂浮粒子
        self._spawn_particle(cx, cy, base_colors[0])
        self._update_particles(screen)

    def _spawn_particle(self, cx, cy, color):
        if len(self._particles) < 120:
            ang = self._rng.uniform(0, 2 * math.pi)
            spd = self._rng.uniform(6, 16)
            self._particles.append({
                "x": cx, "y": cy,
                "vx": math.cos(ang) * spd,
                "vy": math.sin(ang) * spd - 4,
                "life": self._rng.uniform(0.4, 0.9),
                "age": 0.0,
                "color": color,
            })

    def _update_particles(self, screen):
        alive = []
        for p in self._particles:
            p["age"] += 1 / 60.0
            if p["age"] >= p["life"]:
                continue
            p["x"] += p["vx"] * (1 / 60.0)
            p["y"] += p["vy"] * (1 / 60.0)
            p["vy"] -= 3 * (1 / 60.0)  # 轻微上浮
            a = max(0, int(180 * (1 - p["age"] / p["life"])))
            r = 2
            surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (*p["color"], a), (r, r), r)
            screen.blit(surf, (p["x"] - r, p["y"] - r))
            alive.append(p)
        self._particles = alive
