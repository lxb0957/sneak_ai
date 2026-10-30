"""玩家实体：WASD 移动、碰撞检测、三档移动（潜行 / 走路 / 冲刺）与体力。

三档移动是潜行游戏的核心张力来源（风险—收益）：
- Shift 潜行：最慢，但噪音只有走路的 0.3，几乎不会被听见
- 默认走路：速度与噪音均衡
- Space 冲刺：最快，能甩开追击，但噪音 1.85 倍（听觉半径接近翻倍），
  而且消耗体力 —— 想一直跑是不行的
"""

import math

import pygame

from .. import constants as C
from ..core.grid import GridMap


class Player:
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y
        self.radius = 0.35
        self.speed_walk = C.PLAYER_WALK_SPEED
        self.speed_sneak = C.PLAYER_SNEAK_SPEED
        self.speed_sprint = C.PLAYER_SPRINT_SPEED

        self.sneaking = False
        self.sprinting = False
        self.moving = False
        self.stamina = C.STAMINA_MAX

        # 投石：有限次数的主动干扰手段（Q 键 / 鼠标右键）
        self.stones = C.STONE_COUNT
        self.stone_cd = 0.0

        # 消音手枪：弹药极度有限，一发换一个 5 秒的空窗（左键 / 鼠标左键）
        self.ammo = C.WEAPON_AMMO
        self.fire_cd = 0.0

        # 护盾：沿途捡的，最多一层。被抓住时消耗掉，把那个人当场击倒。
        # 注意它**不是**多一条命：代价是记一次击倒、留尸体、惊动附近的人。
        self.shield = 0

        self.facing = 0.0  # 朝向（弧度），移动时更新
        # 事件驱动按键状态，比 get_pressed() 更稳（不受窗口焦点抖动影响）
        self._keys = set()

    @property
    def pos(self) -> tuple:
        return (self.x, self.y)

    @property
    def noise(self) -> float:
        """当前噪音系数（0~2），给敌人听觉用。"""
        if not self.moving:
            return C.NOISE_STILL
        if self.sprinting:
            return C.NOISE_SPRINT
        if self.sneaking:
            return C.NOISE_SNEAK
        return C.NOISE_WALK

    def noise_radius(self) -> float:
        """当前能被听见的半径（格）—— 与 Perception.can_hear 用同一个公式。

        这个值直接画成角色周围的一圈虚线。玩家以前只能靠"被发现/没被发现"
        反推听觉机制；把半径画出来之后，"冲刺为什么等于报点"一眼就懂。
        """
        if self.noise <= C.NOISE_STILL:
            return 0.0
        return C.HEAR_RANGE * min(1.5, 0.55 + 0.6 * self.noise)

    def can_throw(self) -> bool:
        return self.stones > 0 and self.stone_cd <= 0.0

    def consume_stone(self) -> None:
        self.stones = max(0, self.stones - 1)
        self.stone_cd = C.STONE_COOLDOWN

    def can_fire(self) -> bool:
        return self.ammo > 0 and self.fire_cd <= 0.0

    def consume_ammo(self) -> None:
        self.ammo = max(0, self.ammo - 1)
        self.fire_cd = C.WEAPON_COOLDOWN

    @property
    def speed(self) -> float:
        if self.sprinting:
            return self.speed_sprint
        if self.sneaking:
            return self.speed_sneak
        return self.speed_walk

    def handle_key(self, key: int, pressed: bool) -> None:
        """由主循环 KEYDOWN/KEYUP 事件调用，记录按下状态。"""
        if pressed:
            self._keys.add(key)
        else:
            self._keys.discard(key)

    def clear_keys(self) -> None:
        """结算/重开时清空按键，避免"还按着 W"直接导致新一局开跑。"""
        self._keys.clear()

    def update(self, dt: float, world) -> None:
        self.stone_cd = max(0.0, self.stone_cd - dt)
        self.fire_cd = max(0.0, self.fire_cd - dt)
        dx = (pygame.K_d in self._keys) - (pygame.K_a in self._keys)
        dy = (pygame.K_s in self._keys) - (pygame.K_w in self._keys)
        self.sneaking = bool(
            pygame.K_LSHIFT in self._keys or pygame.K_RSHIFT in self._keys
        )
        self.moving = bool(dx or dy)

        # 冲刺：按住 Space；体力耗尽自动停，喘够 STAMINA_MIN_START 才能再起跑
        want_sprint = pygame.K_SPACE in self._keys
        can_start = self.stamina >= C.STAMINA_MIN_START
        self.sprinting = (
            want_sprint and self.moving and (self.sprinting or can_start)
            and self.stamina > 0
        )

        # 体力：冲刺消耗，否则恢复
        if self.sprinting:
            self.stamina = max(0.0, self.stamina - C.STAMINA_DRAIN * dt)
            if self.stamina <= 0.0:
                self.sprinting = False
        else:
            self.stamina = min(C.STAMINA_MAX, self.stamina + C.STAMINA_REGEN * dt)

        speed = self.speed

        # 归一化，避免斜向移动更快
        length = (dx * dx + dy * dy) ** 0.5
        if length > 0:
            dx /= length
            dy /= length
            self.facing = math.atan2(dy, dx)

        nx = self.x + dx * speed * dt
        ny = self.y + dy * speed * dt
        self._try_move(nx, ny, world.grid)

    def _try_move(self, nx: float, ny: float, grid: GridMap) -> None:
        # 分开 X/Y 轴移动，贴着墙也能滑动
        r = self.radius
        if not self._collides(nx, self.y, r, grid):
            self.x = nx
        if not self._collides(self.x, ny, r, grid):
            self.y = ny

    @staticmethod
    def _collides(x: float, y: float, r: float, grid: GridMap) -> bool:
        # 圆 vs 墙格碰撞：用圆心到墙格最近点距离，避免四角包围盒的卡墙角 bug
        cx, cy = int(x), int(y)
        for tx in (cx - 1, cx, cx + 1):
            for ty in (cy - 1, cy, cy + 1):
                if not grid.is_wall(tx, ty):
                    continue
                closest_x = max(tx, min(x, tx + 1))
                closest_y = max(ty, min(y, ty + 1))
                dx = x - closest_x
                dy = y - closest_y
                if dx * dx + dy * dy < r * r:
                    return True
        return False
