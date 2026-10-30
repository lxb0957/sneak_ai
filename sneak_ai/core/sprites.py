"""程序化像素精灵：把"火柴人"换成有装备的战术小人。

为什么要单独一个模块、而不是继续在 renderer 里画几何图形：

1. **细节量**：一个能看出是"人"的像素小人需要头盔、护目镜、战术背心、
   弹匣袋、腰带、手臂、持枪、靴子 —— 40 多个绘制指令。
   每帧对 15 个角色都跑一遍，pygame 的 draw 调用会成为瓶颈。
2. **动画帧**：3 个朝向（左右镜像）× 5 帧走路 + 蹲姿，几十张图，每次重画是浪费。

所以做法是"离线烘焙 + 运行时只 blit"：
用 18×24 的美术像素网格画一次（真正的像素块，不是缩放出来的几何图形），
`pygame.transform.scale` 按整数倍放大（最近邻，边缘保持硬朗），存进字典缓存。
运行时每帧每个角色只花一次 blit。

三个让它"像人"而不是"像方块"的细节：
- **1px 深色描边**：暗色地板上没有描边，人物会和背景糊在一起；
- **分层配色**：护甲（亮）/ 背心（暗）/ 裤腿（更暗）/ 靴子（近黑），
  一眼能分出头盔、躯干、腿三段；
- **朝向线索**：护目镜的亮色反光只在正面/侧面出现，背面是背包和背带，
  玩家不用看标签就知道哨兵朝哪边。

调色板由主色（护甲色）推导，所以玩家和不同角色的敌人共用同一套画法。
"""

import pygame

# 美术像素画布：18 宽 × 24 高，放大 2 倍 = 屏幕 36×48（格子 32，人物略高一档）
ART_W, ART_H = 16, 24
SCALE = 2
PAD = 1            # 描边留白
ANCHOR_X = 8
ANCHOR_Y = 23

OUTLINE = (12, 14, 18, 235)

# 不随主色变化的中性色
GUN_DARK = (44, 48, 56)
GUN_LIGHT = (110, 116, 128)
BOOT = (30, 32, 38)
BOOT_LIGHT = (62, 66, 76)
STRAP = (26, 28, 34)
VISOR = (24, 32, 44)


def _shade(color, factor: float):
    return tuple(max(0, min(255, int(c * factor))) for c in color[:3])


class SpriteBank:
    """像素小人精灵库：按 (配色, 朝向, 帧, 蹲姿) 缓存。"""

    def __init__(self):
        self._cache = {}
        self._anim_frame = 0

    # ---------------- 对外 ----------------
    def sprite(self, body_color, facing: float, moving: bool,
               crouch: bool = False, accent=None):
        """取一张精灵。

        facing 用弧度，内部量化成 3 个朝向（左右镜像合并）。
        accent 是护目镜的亮色：玩家用冷色、哨兵用暖色、追击时变红，
        这样"谁在看你"不用看标签也能分辨。
        """
        accent = accent or (150, 228, 255)
        group, mirror = self._facing_group(facing)
        frame = self._next_frame(moving)
        key = (tuple(body_color), group, frame, crouch, accent)
        surf = self._cache.get(key)
        if surf is None:
            surf = self._bake(body_color, group, frame, crouch, accent)
            self._cache[key] = surf
        return pygame.transform.flip(surf, True, False) if mirror else surf

    @property
    def anchor(self):
        """精灵内的脚底锚点（屏幕像素）。"""
        return ((ANCHOR_X + PAD) * SCALE, (ANCHOR_Y + PAD) * SCALE)

    # ---------------- 内部：朝向与帧 ----------------
    @staticmethod
    def _facing_group(facing: float):
        """把 8 方向折叠成 3 组：front / back / side（side 靠镜像覆盖左右）。"""
        v = pygame.math.Vector2(1, 0).rotate_rad(facing)
        if abs(v.x) > abs(v.y) * 1.05:
            return "side", v.x < 0
        return ("front" if v.y >= 0 else "back"), False

    def _next_frame(self, moving: bool) -> int:
        """行走动画：4 帧循环；站着不动时回到站姿（第 0 帧）。"""
        if not moving:
            return 0
        self._anim_frame += 1
        return 1 + (self._anim_frame // 8) % 4

    # ---------------- 内部：烘焙 ----------------
    def _bake(self, body_color, group, frame, crouch, alert_glow):
        art = pygame.Surface((ART_W, ART_H), pygame.SRCALPHA)
        painter = _CharPainter(art, body_color, frame, crouch, alert_glow)
        if group == "front":
            painter.front()
        elif group == "back":
            painter.back()
        else:
            painter.side()

        # 描边：把剪影往 4 个方向各偏移 1px 涂成深色，再盖上本体
        wide = pygame.Surface((ART_W + PAD * 2, ART_H + PAD * 2), pygame.SRCALPHA)
        sil = pygame.mask.from_surface(art).to_surface(
            setcolor=OUTLINE, unsetcolor=(0, 0, 0, 0)
        )
        for dx, dy in ((0, 1), (2, 1), (1, 0), (1, 2)):
            wide.blit(sil, (dx, dy))
        wide.blit(art, (PAD, PAD))
        return pygame.transform.scale(
            wide, (wide.get_width() * SCALE, wide.get_height() * SCALE)
        )


class _CharPainter:
    """在 18×24 的美术网格上画一个战术小人。

    坐标约定：x 0..17，y 0..23；脚底在 y=23，身体中轴在 x=9。
    站立时总高 23 像素：头盔 2..7、躯干 8..15、腿 16..22。
    蹲姿只把上半身整体下移 3 像素、腿折叠，不改变锚点。
    """

    def __init__(self, surf, body, frame, crouch, alert_glow):
        self.s = surf
        self.base = body
        self.light = _shade(body, 1.18)
        self.hi = _shade(body, 1.38)
        self.dark = _shade(body, 0.66)
        self.darker = _shade(body, 0.46)
        self.vest = _shade(body, 0.55)
        self.pants = _shade(body, 0.58)
        self.frame = frame
        self.crouch = crouch
        self.alert = alert_glow
        self.dy = 3 if crouch else 0

    # ---- 绘图小工具（dy = 蹲姿整体下移） ----
    def r(self, x, y, w, h, col):
        if w > 0 and h > 0:
            pygame.draw.rect(self.s, col, (x, y + self.dy, w, h))

    def r0(self, x, y, w, h, col):
        """不受蹲姿位移影响（脚、地面附近用）。"""
        if w > 0 and h > 0:
            pygame.draw.rect(self.s, col, (x, y, w, h))

    def px(self, x, y, col):
        self.s.set_at((x, y + self.dy), col)

    # ================= 共通部件 =================
    # 比例（美术像素）：头 6 宽、肩 8 宽、躯干 6 宽、腿 2 宽。
    # 头不能和肩一样宽 —— 那是机器人的比例，不是人的比例。
    def _legs_front(self):
        """正面/背面双腿：抬腿那条短一格。"""
        lift_l = 1 if self.frame == 1 else 0
        lift_r = 1 if self.frame == 3 else 0
        for x, lift in ((5, lift_l), (9, lift_r)):
            if self.crouch:
                self.r0(x, 18, 2, 2, self.pants)
                self.r0(x - 1, 20, 4, 2, BOOT)
                self.r0(x - 1, 20, 4, 1, BOOT_LIGHT)
            else:
                self.r(x, 16, 2, 5 - lift, self.pants)
                self.r(x, 16, 1, 4, _shade(self.pants, 1.24))
                self.r0(x - 1, 21 - lift, 4, 2, BOOT)
                self.r0(x - 1, 21 - lift, 4, 1, BOOT_LIGHT)

    def _torso_front(self):
        """正面躯干：肩 → 护甲板 → 弹匣袋 → 腰带。"""
        self.r(4, 8, 8, 1, self.light)       # 肩线
        self.r(5, 9, 6, 1, self.light)
        self.r(5, 10, 6, 5, self.base)       # 躯干
        self.r(6, 10, 4, 4, self.light)      # 护甲板（受光面）
        self.r(6, 10, 4, 1, self.hi)         # 上沿高光
        self.r(6, 12, 1, 2, self.dark)       # 弹匣袋
        self.r(9, 12, 1, 2, self.dark)
        self.r(5, 15, 6, 1, STRAP)           # 腰带
        self.r(7, 15, 1, 1, GUN_LIGHT)       # 带扣

    def _arms_front(self):
        self.r(3, 9, 2, 5, self.dark)
        self.r(11, 9, 2, 5, self.dark)
        self.r(3, 13, 2, 1, self.darker)
        self.r(11, 13, 2, 1, self.darker)

    def _rifle_front(self):
        """正面抱枪：从右腰斜向下，末端枪口提亮。

        横在腰前会被读成腰带；斜放 + 亮枪口才能在 32px 下读出"手里有枪"。
        """
        # 正面不画"抱在腰前的枪"：3 像素的空间里只会糊成一团。
        # 只保留大腿外侧的枪套 + 露出的枪柄，远看是"这人带着武器"。
        self.r(10, 13, 1, 3, GUN_DARK)     # 枪套
        self.px(10, 12, GUN_LIGHT)         # 枪柄

    def _helmet(self, visor: bool):
        """正面/背面头盔：盔顶高光 → 盔体 → 护目镜 → 下颌/护颈。"""
        self.r(7, 1, 2, 1, self.hi)          # 盔顶高光
        self.r(6, 2, 4, 1, self.dark)        # 盔顶
        self.r(5, 3, 6, 2, self.dark)        # 盔体（最宽 6）
        self.r(5, 5, 6, 1, self.dark)        # 盔沿（压在护目镜上沿，把"脸"分出来）
        if visor:
            self.r(6, 5, 4, 1, self.alert or (160, 232, 255))   # 护目镜（一条亮带）
            self.r(6, 6, 4, 1, VISOR)                            # 镜片下沿
            self.r(6, 7, 4, 1, self.darker)                      # 面罩/下颌
            self.px(6, 6, _shade(VISOR, 1.6))                    # 镜片反光
            self.px(9, 6, _shade(VISOR, 1.6))
        else:
            self.r(6, 6, 4, 1, _shade(self.base, 0.9))
            self.r(7, 5, 2, 2, self.darker)  # 盔体接缝
            self.r(6, 7, 4, 1, self.darker)  # 护颈

    # ================= 正面 =================
    def front(self):
        self._legs_front()
        self._torso_front()
        self._arms_front()
        self._helmet(visor=True)
        self._rifle_front()

    # ================= 背面 =================
    def back(self):
        self._legs_front()
        self.r(4, 8, 8, 1, self.light)       # 肩
        self.r(5, 9, 6, 1, self.light)
        self.r(5, 10, 6, 5, self.base)
        # 背包：比护甲更暗，靠两条背带分割
        self.r(6, 10, 4, 4, self.darker)
        self.r(6, 10, 4, 1, self.dark)
        self.r(6, 11, 1, 3, STRAP)
        self.r(9, 11, 1, 3, STRAP)
        self.r(5, 15, 6, 1, STRAP)
        self.r(3, 9, 2, 5, self.dark)
        self.r(11, 9, 2, 5, self.dark)
        self.r(3, 13, 2, 1, self.darker)
        self.r(11, 13, 2, 1, self.darker)
        self._helmet(visor=False)

    # ================= 侧面（朝右；朝左靠镜像） =================
    def side(self):
        fwd, back = 0, 0
        if self.frame == 1:
            fwd, back = 1, -1
        elif self.frame == 3:
            fwd, back = -1, 1
        if self.crouch:
            self.r0(6, 18, 2, 2, self.pants)
            self.r0(9, 18, 2, 2, _shade(self.pants, 0.8))
            self.r0(5, 20, 4, 2, BOOT)
            self.r0(9, 20, 4, 2, BOOT)
        else:
            self.r0(9 + fwd, 16, 2, 6, self.pants)
            self.r0(8 + back, 16, 2, 6, _shade(self.pants, 0.78))
            self.r0(9 + fwd, 21, 4, 2, BOOT)
            self.r0(9 + fwd, 21, 4, 1, BOOT_LIGHT)
            self.r0(8 + back, 21, 3, 2, BOOT)
        # 背包（背后）
        self.r(3, 9, 2, 4, self.darker)
        # 躯干侧影
        self.r(4, 8, 7, 1, self.light)
        self.r(4, 9, 7, 1, self.light)
        self.r(4, 10, 7, 5, self.base)
        self.r(8, 10, 3, 4, self.dark)       # 胸前护甲
        self.r(8, 10, 2, 1, self.light)
        self.r(4, 15, 7, 1, STRAP)
        # 头盔侧影：盔顶 → 前檐 → 护目镜
        self.r(6, 1, 3, 1, self.hi)
        self.r(5, 2, 4, 1, self.dark)
        self.r(4, 3, 6, 2, self.dark)
        self.r(4, 5, 7, 1, self.darker)
        self.r(9, 4, 2, 1, self.dark)        # 前檐
        self.r(8, 5, 3, 1, self.alert or (160, 232, 255))   # 护目镜侧条
        self.r(5, 7, 3, 1, self.darker)      # 后颈
        # 手臂（前伸托枪）
        self.r(9, 10, 2, 3, self.dark)
        self.r(10, 12, 1, 1, self.darker)
        # 步枪：枪托在后、枪身 2px 厚、枪口朝前
        self.r(7, 12, 6, 2, GUN_DARK)
        self.r(8, 12, 4, 1, _shade(GUN_DARK, 1.3))
        self.r(12, 12, 2, 1, GUN_LIGHT)      # 枪口
        self.r(6, 11, 2, 2, GUN_LIGHT)       # 枪托
        self.r(9, 11, 1, 1, _shade(GUN_DARK, 1.55))   # 拉机柄
        self.r(10, 14, 1, 1, GUN_DARK)       # 弹匣底
