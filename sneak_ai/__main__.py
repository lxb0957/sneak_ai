"""主入口：游戏循环、事件处理、渲染、通关结算。

运行：python -m sneak_ai
操作：主菜单回车开局 / WASD 移动 / Shift 潜行 / Space 冲刺 / 左键 射击 /
      Q 或鼠标右键 投石 / F1 AI 调试视图 / M 静音 / R 重开 / ESC 回主菜单

玩法闭环：
1. 潜入：前往数据终端（绿色发光标记），一路避开 21 个哨兵的视线与听觉。
2. 逃脱：拿到数据后全图警觉，返回撤离点（蓝色箭头）。
3. 结算：抵达撤离点=胜利（评级 S/A/B/C + 得分 + 最佳纪录），被抓住=失败。

手里有 3 颗石子 + 5 发消音手枪弹：
- 石子扔到空地可以把附近哨兵引过去查看（只能制造"起疑"，不会暴露位置）；
- 手枪能打倒地一个哨兵，但它 5 秒后会在自己的巡逻网里最远的位置重新上岗。
  枪声（9.5 格）会招来一片"过去看看"的人；如果尸体被别人看见，
  这片区域会进入 12 秒警戒（原地张望，不堵门，但很难再混过去）。

  所以开枪不是清场，是买时间 —— 而且最好"躲着开"。
"""

import math
import os
import sys
import time
import traceback
from pathlib import Path

import pygame

from . import constants as C
from .ai.coordination import Coordinator
from .audio.sfx import SFX
from .core import records
from .core import levels as L
from .core.camera import Camera
from .core.hud import HUD
from .core.mapview import MapView
from .core.minimap import FullMap, Minimap
from .core.menu import Menu
from .core import settings
from .core.renderer import PixelRenderer
from .core.world import (World, build_demo_map, build_pickups,
                         build_spawn_and_patrols)
from .debug.debug_renderer import DebugRenderer
from .entity.enemy import Enemy
from .entity.player import Player
from .perception.perception import Perception


def _create_window() -> pygame.Surface:
    """创建窗口：放不下就按比例降级，而不是让异常一路炸出去。

    以前这里是一行裸 `set_mode((1280, 960))`。笔记本 1366×768、远程桌面、
    没接显示器这三种常见情况都会抛 `pygame.error`，异常冒泡到 main() 之后
    进程直接死 —— 用户看到的就是"黑窗口一闪就没了"，没有任何线索。

    现在改成"先问屏幕有多大，能撑多大就多大"，并把**实际**尺寸写回常量：
    小地图的视野框是按 VIEW_W/VIEW_H 画的，不同步就会框偏。

    注意 `pygame.SCALED`：高 DPI 屏上 SDL 会把窗口放大到物理像素，
    逻辑分辨率不变，代码里完全不用关心缩放系数。
    """
    sizes = []
    # 无头驱动（SDL_VIDEODRIVER=dummy）报的是 1024x768 这个假尺寸，
    # 按它"降级"会把所有截图、预览图和 benchmark 的分辨率悄悄改小。
    # 自动化测试没有真实屏幕可言，一律用标准尺寸。
    headless = os.environ.get("SDL_VIDEODRIVER", "").strip().lower() == "dummy"
    if not headless:
        try:
            # display 模块必须先 init，否则 Info() 直接抛
            # "video system not initialized" —— 被 except 一吞，
            # 这套适配就永远不生效，等于白写。
            pygame.display.init()
            info = pygame.display.Info()
            sw, sh = info.current_w, info.current_h
            if sw > 0 and sh > 0:
                # 标题栏 + 任务栏还要吃掉几十像素，留 8% 余量
                room_w, room_h = sw * 0.92, sh * 0.92
                # 用固定的 4:3 候选表而不是按比例乘算：
                # 乘算后取整会把比例切歪（1280x960 打成 921x691 就不是 4:3 了），
                # 候选表的每一档天生比例正确，挑第一个放得下的即可。
                sizes = [(w, h) for (w, h) in (
                    (1280, 960), (1152, 864), (1024, 768), (960, 720),
                    (896, 672), (800, 600), (640, 480),
                ) if w <= room_w and h <= room_h] or [(640, 480)]
        except Exception:
            pass
    sizes.append((C.WINDOW_W, C.WINDOW_H))
    sizes.append((1024, 768))
    sizes.append((800, 600))

    last_err = None
    for w, h in sizes:
        for flags in (0, pygame.SCALED):
            try:
                surf = pygame.display.set_mode((w, h), flags)
            except pygame.error as exc:
                last_err = exc
                continue
            if surf is not None:
                C.WINDOW_W, C.WINDOW_H = int(w), int(h)
                C.VIEW_W = C.WINDOW_W // C.TILE_SIZE
                C.VIEW_H = C.WINDOW_H // C.TILE_SIZE
                return surf
    raise last_err if last_err is not None else pygame.error("没有可用的显示设备")


class Game:
    def __init__(self, start_level: int = 0):
        pygame.init()
        self.screen = _create_window()
        pygame.display.set_caption("潜行协议 Stealth Protocol")
        self.clock = pygame.time.Clock()

        self.sfx = SFX()
        # 音效开关要跨局记住：玩家上次按了 M，这次开局就别再响
        self.sfx.muted = settings.load_muted()
        self.cam = Camera()
        self.perception = Perception()
        self.debug = DebugRenderer()
        self.px = PixelRenderer()
        self.hud = HUD()
        self.best = records.load()
        self.menu = Menu(self.best)

        self._time_accum = 0.0  # 驱动脉动/动画相位
        self._step_acc = 0.0    # 脚步音效的移动距离累积
        self._scored = False    # 本局是否已提交成绩
        self.new_record = False
        self._prev_stamina = C.STAMINA_MAX
        self._tips_shown: set = set()   # 本会话已提示过的资源类型
        self._power_tip_cd = 0.0        # 总闸提示的冷却（别每帧刷屏）
        self._show_intro = False        # 从菜单开局的第一条引导
        self.show_map = False           # Tab 全图：默认关，进关卡时重置

        # ---- 关卡推进 ----
        # start_level 只由命令行 `--level N` 决定（调试/给朋友演示第三关用）。
        # 正常开局是 0，从菜单重开也回 0 —— 但"重开一局"回到的是**起点**，
        # 所以 _restart_run 用的是 _start_level，不是硬编码的 0。
        self._start_level = max(0, min(start_level, len(L.LEVELS) - 1))
        self.level_index = self._start_level
        self._carry: dict = {}       # 跨关延续的统计（见 _snapshot_carry）
        self._next_timer = 0.0       # 过关过场剩余时间（>0 表示正在过场）
        self._clear_veil = None      # 过场遮罩（惰性创建，别每帧分配全屏 Surface）

        self._new_world()
        self.running = True

    # ============ 世界构建 ============
    def _new_world(self) -> None:
        """按当前关卡重建世界：地图、玩家、敌人、静态地图烘焙。

        多关之后这里多了两件事：
        1. 目标点 / 评级基准从关卡数据里读，不再用全局常量；
        2. 把上一关带过来的统计（用时、被发现、击倒、剩余弹药）灌进新世界。
           不灌的话，第二关的结算页会显示"用时 40 秒"——只算了第二关，
           而玩家明明已经打了两关。
        """
        lv = L.LEVELS[self.level_index]
        grid = lv.build()
        self.world = World(grid)
        self.world.level_index = lv.idx
        self.world.level_name = lv.name
        self.world.terminals = lv.terminals
        self.world.exfil = lv.exfil
        # 评级基准按"累计"算：跑到第 2 关时，允许的时间是两关之和。
        # 只比较单关用时的话，越往后越容易拿 S，评级就没有意义了。
        self.world.par = L.cumulative_par(self.level_index)
        self.world.coordinator = Coordinator(self.world.grid)
        self.mapview = MapView(self.world.grid)
        self.minimap = Minimap(self.world.grid)
        # 全图按**当前关卡**的尺寸重建：第 4 关是 92x42 的长条，
        # 沿用上一关的缩放档位会把图糊成一团
        self.fullmap = FullMap(self.world.grid)
        self.show_map = False           # 换关时收起，别把上一关的图带过来

        player_spawn, enemy_cfg = L.spawn_and_patrols(lv, self.world.grid)
        self.player = Player(player_spawn[0], player_spawn[1])
        self.world.player = self.player
        self.world.enemies = [
            Enemy(x, y, patrol=pts, perception=self.perception,
                  chase_speed=lv.chase,
                  alert_speed=lv.alert_speed, alert_view=lv.alert_view)
            for (x, y, pts) in enemy_cfg
        ]
        self.world.alert_duration = lv.alert_duration
        # 总闸：坐标已经是连续的格中心（关卡里就按坐标写），不再 +0.5。
        self.world.power = [
            {"x": px, "y": py, "used": False} for (px, py) in lv.power
        ]
        _dur, _view, _noise = lv.blackout
        self.world.blackout_duration = _dur
        self.world.blackout_view = _view
        self.world.blackout_noise = _noise
        self.world.pickups = L.level_pickups(lv, self.world.grid)
        self._apply_carry()
        self.cam.follow(self.player.x, self.player.y)  # 开局直接对准，不要平滑漂移

    # ---- 跨关延续 ----
    # 只带"这一局已经付出去的代价"，不带"这一关的现场状态"。
    # 弹药/石子要带：它是玩家前一关省下来的资源，不带就等于每关重置，
    # "为下一关留两发子弹"这个决策会直接消失。
    _CARRY_COUNTERS = ("spotted", "kills", "bodies_found", "decoy_hits",
                       "shield_kills")

    def _apply_carry(self) -> None:
        c = self._carry
        if not c:
            return
        self.world.elapsed = c.get("elapsed", 0.0)
        for name in self._CARRY_COUNTERS:
            setattr(self.world, name, c.get(name, 0))
        self.world.peak_threat = c.get("peak_threat", 0.0)
        self.world.spots_by_enemy = dict(c.get("spots_by_enemy", {}))
        self.player.ammo = c.get("ammo", self.player.ammo)
        self.player.stones = c.get("stones", self.player.stones)
        self.player.shield = c.get("shield", 0)

    def _snapshot_carry(self) -> dict:
        w = self.world
        c = {
            "elapsed": w.elapsed,
            "peak_threat": w.peak_threat,
            "spots_by_enemy": dict(w.spots_by_enemy),
            "ammo": self.player.ammo,
            "stones": self.player.stones,
            "shield": self.player.shield,
        }
        for name in self._CARRY_COUNTERS:
            c[name] = getattr(w, name, 0)
        return c

    def _advance_level(self) -> None:
        """过关过场结束 → 进下一关。"""
        self._next_timer = 0.0
        self._carry = self._snapshot_carry()
        self.level_index += 1
        self._new_world()
        self._scored = False
        self._step_acc = 0.0
        self._prev_stamina = C.STAMINA_MAX
        self._tips_shown = set()
        self._power_tip_cd = 0.0
        self._show_intro = True

    def run(self) -> None:
        while self.running:
            dt = min(self.clock.tick(C.FPS) / 1000.0, 0.05)
            if self.menu.active:
                self._handle_menu_events()
                self._render_menu()
                continue
            self.step(dt)
        pygame.quit()

    def step(self, dt: float) -> None:
        """推进一帧游戏（不含菜单）。

        单独抽出来是为了可测。过场计时这类"只在主循环里走"的状态机最容易漏：
        第一版把 _next_timer 设成 1.6 却**没有任何地方递减它**，通关卡片就永久
        停住、输入被吞、永远进不了第二关 —— 而机器人测试只在"胜利"那一刻断言，
        根本走不到这段代码。现在测试可以直接喂 dt 驱动它。
        """
        self._time_accum += dt
        self._handle_events()
        # 过场期间世界冻结：卡片上那 1.6 秒不该算进玩家用时
        # 全图（Tab）同理：站在长廊里摊开地图看路线，不该被巡逻兵顺手摸掉。
        # 这张图只有静态信息、不含敌人，所以暂停不构成信息优势；
        # world.elapsed 一起冻住，也就没有"暂停拖时间"的漏洞。
        if self._next_timer <= 0 and not self.show_map:
            self.world.update(dt)
            self._update_tips(dt)
            self._handle_audio(dt)
            self._handle_scoring()
        if self._next_timer > 0:
            self._next_timer -= dt
            if self._next_timer <= 0:
                self._advance_level()
        self._render(dt)

    # ============ 主菜单 ============
    def _handle_menu_events(self) -> None:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            else:
                self.menu.handle_event(ev, self.sfx)
                if not self.menu.active:      # 回车 / 点击 → 开局
                    self._on_menu_start()

    def _to_menu(self) -> None:
        """回主菜单：刷新最佳纪录，下次开局是一局新的。

        中途 ESC 退出等于放弃这一局，所以关卡进度一起清掉 ——
        否则从菜单再开局会接着打第 2 关，而分数和时间对不上。
        """
        self.menu.best = records.load()
        self.menu.active = True
        self.hud._tip_left = 0.0
        self.level_index = 0
        self._carry = {}
        self._next_timer = 0.0

    def _restart_run(self) -> None:
        """整局重来：回到起点关（默认第 1 关），跨关统计清零。"""
        self.level_index = self._start_level
        self._carry = {}
        self._restart()

    def _on_menu_start(self) -> None:
        """从菜单进入游戏：一局新的，并给开局引导。"""
        self._restart_run()
        self._tips_shown = set()
        self._power_tip_cd = 0.0
        self._show_intro = True

    def _render_menu(self) -> None:
        self.menu.draw(self.screen, self.sfx.muted, self._time_accum)
        pygame.display.flip()

    # ============ 引导提示 ============
    def _update_tips(self, dt: float) -> None:
        """走近补给点才提示用法：教学跟着时机走，不是开局倒一堆字。"""
        if self.world.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            return
        if self._show_intro:
            self._show_intro = False
            if self.level_index == 0:
                msg = "前往绿色标记的数据终端 —— 蹲下（Shift）移动更安静"
            else:
                # 第二关起不重复基础教学，改成一句"这张图想让你紧张什么"。
                lv = L.LEVELS[self.level_index]
                msg = f"第 {lv.idx + 1} 关 · {lv.name}　{lv.subtitle}"
            self.hud.show_tip(msg, life=5.0)
        p = self.world.player
        TIPS = {
            "ammo": "弹药包：走近自动补 2 发（上限 8）",
            "stone": "石子堆：走近捡 2 颗，Q / 右键扔出去引开哨兵",
            "shield": "护盾：捡起后能挡下一次抓捕，碰到你的哨兵会倒下",
        }
        for it in self.world.pickups:
            if it["taken"] or it["kind"] in self._tips_shown:
                continue
            if math.hypot(it["x"] - p.x, it["y"] - p.y) < 3.5:
                self._tips_shown.add(it["kind"])
                self.hud.show_tip(TIPS[it["kind"]], life=4.0)
                break

        # 总闸：站到跟前才提示。这个键不写在任何地方，玩家不可能自己发现 ——
        # 但也不能开局就写在屏幕上，那样"发现总闸"这个信息就白给了。
        if self.world.nearby_power() is not None and not self._power_tip_cd:
            self._power_tip_cd = 6.0
            self.hud.show_tip("E：拉下总闸 —— 全图黑灯 14 秒，代价是他们都会听见", life=5.0)
        if self._power_tip_cd > 0:
            self._power_tip_cd = max(0.0, self._power_tip_cd - dt)

    # ============ 事件 ============
    def _handle_events(self) -> None:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    # 游戏中 ESC → 回主菜单（战绩照常结算过的不受影响）；
                    # 菜单里 ESC 才退出程序
                    self._to_menu()
                elif ev.key == pygame.K_F1:
                    self.debug.toggle()
                elif ev.key == pygame.K_m:
                    self.sfx.toggle_mute()
                    settings.save(self.sfx.muted)
                # 结算画面：R 重开。失败 = 重来本关（罚时间不罚进度），
                # 通关 = 整局重来（回到第 1 关）。
                if self.world.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
                    if self._next_timer > 0:
                        continue          # 过关过场中不接受输入
                    if ev.key == pygame.K_r:
                        if self.world.state == C.STATE_DEFEAT:
                            self._retry_level()
                        else:
                            self._restart_run()
                    continue
                # Tab：整张关卡俯视图。只画静态信息，不显示敌人
                if ev.key == pygame.K_TAB:
                    self.show_map = not self.show_map
                    # 打开时清空按住的方向键，否则"按着 W 打开地图"会在暂停
                    # 结束后继续往前走 —— 松开的事件已经被这次暂停吃掉了
                    self.player.clear_keys()
                    continue
                # 投石：Q 朝当前朝向扔（鼠标右键可以瞄准具体位置）
                if ev.key == pygame.K_q:
                    self._throw(None)
                    continue
                # 拉闸：E（只在总闸旁边有效，且一关只有一次）
                if ev.key == pygame.K_e:
                    self._use_power()
                    continue
                self.player.handle_key(ev.key, True)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                # 左键 = 开枪。瞄哪打哪，射线判定，墙上会打出火花
                if self.world.state not in (C.STATE_VICTORY, C.STATE_DEFEAT):
                    wx, wy = self.cam.screen_to_world(*ev.pos)
                    self._fire((wx, wy))
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
                if self.world.state not in (C.STATE_VICTORY, C.STATE_DEFEAT):
                    wx, wy = self.cam.screen_to_world(*ev.pos)
                    self._throw((wx, wy))
            elif ev.type == pygame.KEYUP:
                if self.world.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
                    continue
                self.player.handle_key(ev.key, False)

    def _use_power(self) -> None:
        """按 E 拉闸。

        三种失败都要有反馈，不然玩家会以为"这个键坏了"：
        - far：不在闸旁边 —— 但只有这一关有闸，前三关按 E 什么都不该发生（静默）；
        - used：这一关的闸已经烧了；
        - blackout：灯还黑着，再按没有意义。
        """
        if not self.world.power:
            return
        res = self.world.activate_power()
        if res == "ok":
            self.hud.show_tip(
                f"总闸已拉下 —— 全图照明只剩 {self.world.blackout_duration:.0f} 秒，"
                "但他们都听见了", life=4.0)
        elif res in ("used", "blackout", "far"):
            self.sfx.play("empty", cooldown=0.4)

    def _throw(self, target) -> None:
        """扔石子。没石子时给一个明确的失败反馈，别让玩家以为是按键坏了。"""
        if self.player.stones <= 0:
            self.sfx.play("empty", cooldown=0.4)
            return
        self.world.throw_stone(target)

    def _fire(self, target) -> None:
        """开枪。没子弹 / 冷却中给空仓音，别让玩家以为是鼠标坏了。"""
        if self.world.shoot(target) == "empty":
            self.sfx.play("gun_empty", cooldown=0.25)

    def _restart(self) -> None:
        """重开一局：重建世界、玩家、敌人，保留渲染器和调试设置。"""
        self._new_world()
        self._step_acc = 0.0
        self._scored = False
        self.new_record = False
        self._prev_stamina = C.STAMINA_MAX
        self._next_timer = 0.0

    def _retry_level(self) -> None:
        """失败后重来：从**本关开头**重开，但把已经耗掉的时间留着。

        为什么不整局重来：三关连着打，死在最后一关前面就被踢回第一关，
        玩家只会关掉游戏。为什么不连时间一起回滚：那样"死一次"没有任何代价，
        慢慢磨就能拿 S，"潜行"就退化成"试错"。
        所以罚时间、不罚进度 —— 这是唯一同时满足这两条的解法。
        """
        self._carry = dict(self._carry)
        self._carry["elapsed"] = self.world.elapsed
        self._restart()

    # ============ 音效 ============
    def _handle_audio(self, dt: float) -> None:
        """把世界事件翻译成音效；脚步声按"移动距离"触发，跑得越快越密。"""
        for ev in self.world.events:
            if ev == "spotted":
                self.sfx.play("spotted")
            elif ev == "radio":
                self.sfx.play("radio", cooldown=0.8, volume=0.8)
            elif ev == "pickup":
                self.sfx.play("pickup")
            elif ev == "victory":
                self.sfx.play("victory")
            elif ev == "defeat":
                self.sfx.play("defeat")
            elif ev == "power_cut":
                self.sfx.play("power_cut", volume=0.9)
            elif ev == "power_back":
                self.sfx.play("power_back", volume=0.7)
            elif ev == "stone_throw":
                self.sfx.play("throw", cooldown=0.1, volume=0.9)
            elif ev == "stone_land":
                self.sfx.play("stone_land", volume=0.9)
            elif ev == "shot":
                self.sfx.play("shot", cooldown=0.05)
            elif ev == "hit":
                self.sfx.play("hit", cooldown=0.08)
            elif ev == "respawn":
                self.sfx.play("respawn", cooldown=0.3, volume=0.7)
            elif ev == "body_found":
                # 尸体被看见：一句短的警报，比枪声更"严重"
                self.sfx.play("body_found", cooldown=0.4)
            elif ev in ("pickup_ammo", "pickup_stone"):
                self.sfx.play("pickup", cooldown=0.15, volume=0.85)
            elif ev == "pickup_shield":
                self.sfx.play("pickup_shield", cooldown=0.15)
            elif ev == "shield_break":
                self.sfx.play("shield_break")

        player = self.player
        if player.moving:
            # 走 0.62 格触发一次脚步：潜行时几乎听不见（音量低 + 间隔长）
            self._step_acc += player.speed * dt
            if self._step_acc >= 0.62:
                self._step_acc = 0.0
                self.sfx.play("step", cooldown=0.12,
                              volume=0.22 + 0.22 * min(player.noise, 1.6))
        else:
            self._step_acc = min(self._step_acc, 0.3)

        # 体力刚好耗尽时给一个短促提示音
        if player.stamina <= 0.0 < self._prev_stamina:
            self.sfx.play("stamina_out")
        self._prev_stamina = player.stamina

    # ============ 结算 ============
    def _handle_scoring(self) -> None:
        if self.world.state != C.STATE_VICTORY or self._scored:
            return
        self._scored = True
        self.player.clear_keys()
        # 还有下一关：只放一段过场，不结算。结算页是"一局结束"的界面，
        # 半路弹出来会把"三关是一次行动"切碎成三段互不相干的小局。
        if self.level_index + 1 < len(L.LEVELS):
            self._next_timer = 1.6
            return
        # 用 `--level N` 中途开局的这一局不计入最佳纪录：
        # 从第 3 关起步只要 35 秒就能"打完三关"，交了就是一条永远刷不掉的假纪录 ——
        # 存档是玩家唯一的数据，宁可这一局不记，也不能让它被作弊值污染。
        if self._start_level > 0:
            return
        self.new_record = records.submit(self.world.record())
        self.best = records.load()

    # ============ 渲染 ============
    def _render(self, dt: float = 1 / 60.0) -> None:
        screen = self.screen
        self.cam.follow(self.player.x, self.player.y, dt)
        # 地图预渲染图铺满整个视口，不需要先 fill 背景（省一次全屏填充）
        self.mapview.draw(screen, self.cam)
        self._draw_goals()
        # 调试视图里已经有精确视野锥了，就别再叠一层淡显的（会糊成一片）
        if not self.debug.enabled:
            self.px.draw_vision_cones(screen, self.cam, self.world)
        # 噪音圈 / 落点声波画在角色下面：它是"地面上的信息"，不该盖住人
        self.px.draw_noise(screen, self.cam, self.world)
        # 尸体和重生预警也在地面层：活人要压在它们上面
        self.px.draw_downed(screen, self.cam, self.world, self._time_accum)
        # 补给点在地面层：它是"地上的东西"，被角色压住才符合空间直觉
        self.px.draw_pickups(screen, self.cam, self.world, self._time_accum)
        # 总闸和补给同一层：它是"场景里的东西"，不该盖住角色
        self.px.draw_power(screen, self.cam, self.world, self._time_accum)
        self._draw_enemies()
        self._draw_player()
        self.px.draw_stones(screen, self.cam, self.world)
        # 弹道在最上层：开枪的反馈必须盖住一切，否则玩家会以为没打出去
        self.px.draw_shots(screen, self.cam, self.world)
        # 命中记号比曳光还靠上：它只活 0.22 秒，被任何东西盖住就等于没画
        self.px.draw_hitmarks(screen, self.cam, self.world)
        self.debug.draw(screen, self.cam, self.world, self.perception, self.player)

        # 黑灯压在所有角色之上、UI 之下：这是"光照"，不是"界面"
        self.px.draw_blackout(screen, self.cam, self.world, self.player)
        self.px.draw_vignette(screen, self._danger_level())
        self.minimap.draw(screen, self.world, self.cam, self._pulse())
        self.hud.draw_threat_markers(screen, self.cam, self.world, self._time_accum)
        self.hud.draw(screen, self.world, self.player, self._time_accum, dt,
                      self.sfx.muted)

        # 全图盖在 HUD 之上、结算页之下：它是"摊开地图看一眼"，不是游戏状态
        if self.show_map and self.world.state not in (C.STATE_VICTORY, C.STATE_DEFEAT):
            self.fullmap.draw(screen, self.world, self.cam)

        # 过关过场优先于结算页：多关模式下，"一关打完"不是"一局打完"，
        # 半路弹出结算页会把一次连续行动切成三段互不相干的小局。
        if self._next_timer > 0:
            self._draw_level_clear(screen)
        elif self.world.state in (C.STATE_VICTORY, C.STATE_DEFEAT):
            self.hud.draw_end(screen, self.world, self.best, self.new_record,
                              self._time_accum)
        pygame.display.flip()

    def _draw_level_clear(self, screen) -> None:
        """过关过场：1.6 秒的静态卡片。

        刻意不做"结算页的简化版"——没有分数、没有评级，只有"你过了"和
        "下一关是什么"。分数是整局结束才算的账，中途算账会让人以为在打小关。
        """
        if self._clear_veil is None:
            self._clear_veil = pygame.Surface((C.WINDOW_W, C.WINDOW_H))
            self._clear_veil.fill((6, 8, 12))
        t = max(0.0, 1.6 - self._next_timer)
        # 淡入 0.3 秒：硬切会让玩家以为程序卡了一下
        self._clear_veil.set_alpha(int(200 * min(1.0, t / 0.3)))
        screen.blit(self._clear_veil, (0, 0))

        lv = L.LEVELS[self.level_index]
        cx, cy = C.WINDOW_W // 2, C.WINDOW_H // 2
        title = self.hud.tc.get("lv_done", f"第 {lv.idx + 1} 关完成",
                                46, (120, 240, 190), bold=True)
        screen.blit(title, title.get_rect(center=(cx, cy - 54)))

        if self.level_index + 1 < len(L.LEVELS):
            nxt = L.LEVELS[self.level_index + 1]
            sub = self.hud.tc.get("lv_next", f"下一关 · {nxt.name}",
                                  26, C.COL_TEXT, bold=True)
            screen.blit(sub, sub.get_rect(center=(cx, cy + 6)))
            note = self.hud.tc.get("lv_note", nxt.subtitle, 17, C.COL_TEXT_DIM)
            screen.blit(note, note.get_rect(center=(cx, cy + 42)))

        line = (f"累计用时 {self.world.elapsed:.1f} 秒    "
                f"被发现 {self.world.spotted} 次    "
                f"弹药 {self.player.ammo} · 石子 {self.player.stones}")
        tail = self.hud.tc.get("lv_time", line, 17, (255, 215, 140))
        screen.blit(tail, tail.get_rect(center=(cx, cy + 84)))

    def _pulse(self) -> float:
        return 0.5 + 0.5 * math.sin(self._time_accum * 2.5)

    def _danger_level(self) -> float:
        """屏幕红光强度：有人正看着你 > 有人在追 > 有人起疑。"""
        if self.world.seeing:
            return 0.45 + 0.25 * (0.5 + 0.5 * math.sin(self._time_accum * 7.0))
        if self.world.engaged:
            return 0.30
        if self.world.threat >= C.SUSPICION_CURIOUS:
            return 0.16
        return 0.0

    # ============ 目标点 ============
    def _draw_goals(self) -> None:
        pulse = self._pulse()
        # 数据节点：仅潜入阶段显示。坐标随关卡走（world.terminals）。
        # 已经拿到的节点不再画 —— 玩家需要一眼看出"还剩几个、在哪"，
        # 而不是对着两个一模一样的方块猜哪个还没摸。
        if self.world.state == C.STATE_INFILTRATE:
            for i, (tx, ty) in self.world.remaining():
                sx, sy = self.cam.world_to_screen(tx, ty)
                self.px.draw_goal(self.screen, int(sx), int(sy), "terminal", pulse)
        # 撤离点：逃脱阶段显示（拿数据后高亮）
        if self.world.state == C.STATE_EXFILTRATE:
            tx, ty = self.world.exfil
            sx, sy = self.cam.world_to_screen(tx, ty)
            self.px.draw_goal(self.screen, int(sx), int(sy), "exit", pulse,
                              has_data=self.world.has_data)

    # ============ 角色 ============
    def _draw_player(self) -> None:
        sx, sy = self.cam.world_to_screen(self.player.x, self.player.y)
        self.px.draw_character(
            self.screen, int(sx), int(sy), self.player.facing,
            C.COL_PLAYER_BODY, C.COL_PLAYER_HEAD, is_player=True,
            moving=self.player.moving, sneaking=self.player.sneaking,
        )
        # 护盾光圈画在人身上：这是"你还剩一次免死"的唯一可见提示
        self.px.draw_shield_aura(self.screen, self.cam, self.player,
                                 self._time_accum)

    def _draw_enemies(self) -> None:
        # 瞄准目标算一次，整帧复用：它决定"准星画在谁身上"
        aim = None
        if self.world.state not in (C.STATE_VICTORY, C.STATE_DEFEAT):
            aim = self.world.aim_target(
                self.cam.screen_to_world(*pygame.mouse.get_pos())
            )

        for e in self.world.enemies:
            # 阵亡的哨兵由 draw_downed 负责画成尸体，这里跳过
            if not e.alive:
                continue
            sx, sy = self.cam.world_to_screen(e.x, e.y)
            if not (-64 < sx < C.WINDOW_W + 64 and -64 < sy < C.WINDOW_H + 64):
                continue
            role_name = e.role.value
            color = C.COL_ROLE.get(role_name, C.COL_ENEMY)
            self.px.draw_character(
                self.screen, int(sx), int(sy), e.facing, color,
                is_player=False, moving=e.moving,
                alert=(role_name != "patrol"),
            )
            # 准星画在角色之后：它必须压在最上层才读得清
            if e is aim:
                self.px.draw_aim_reticle(
                    self.screen, int(sx), int(sy), self.player.ammo > 0
                )
            # 头顶状态气泡：? 起疑调查 / ! 已锁定
            self.px.draw_status_icon(self.screen, int(sx), int(sy), e.icon,
                                    self._time_accum)
            # 正在看着我的哨兵：脚下画可疑度弧 / 确认后画锁定括号。
            # 只画"看得见我"的人 —— 别人在别处起疑不该在屏幕上抢注意力。
            if e.sees_player:
                self.px.draw_awareness(
                    self.screen, int(sx), int(sy), e.suspicion,
                    e.suspicion >= C.SUSPICION_CONFIRM, self._time_accum,
                )


def _frozen_data_dir():
    """打包成 exe 后，数据目录要落在**可执行文件旁边**，不是临时解包目录。

    PyInstaller 的 onefile 会把所有代码解到 `sys._MEIPASS`，那个目录
    每次启动都换、退出即删。records.py / settings.py 算路径用的是
    `Path(__file__).parents[n]` —— 打包后 `__file__` 就在那个临时目录里，
    于是"打完一局、退出、纪录没了"，而且没有任何报错。

    所以入口第一件事是把路径锚点换掉。没打包时返回 None，什么都不动。
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def _dump_crash(exc: BaseException):
    """把崩溃现场追加到项目根目录的 crash.log。

    闪退之所以难修，是因为它什么都不告诉你：窗口先消失，控制台跟着关，
    连"是哪一行挂的"都拿不到。所以第一步不是修 bug，是先让 bug 能被看见。
    """
    try:
        anchored = _frozen_data_dir()
        root = anchored if anchored is not None else Path(__file__).resolve().parents[1]
        log = root / "crash.log"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write(f"python {sys.version.split()[0]}  pygame {pygame.version.ver}\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
            f.write("\n")
        return log
    except OSError:
        return None


def _show_crash(exc: BaseException, log) -> None:
    """窗口还活着时把错误画上去停 8 秒；直接黑屏退出就等于"闪退"。"""
    try:
        if not pygame.get_init() or pygame.display.get_surface() is None:
            return
        from .core.ui import font

        screen = pygame.display.get_surface()
        W, H = screen.get_width(), screen.get_height()
        screen.fill((16, 16, 20))
        rows = [
            (font(24, bold=True), "游戏出错了，堆栈已写入 crash.log", (255, 96, 88)),
            (font(16), f"{type(exc).__name__}: {exc}", (226, 226, 232)),
            (font(15), "把这个文件发给开发者就能定位；按任意键或关闭窗口退出。",
             (150, 158, 170)),
        ]
        if log is not None:
            rows.append((font(14), str(log), (110, 120, 132)))
        y = H // 2 - 70
        for f, text, color in rows:
            surf = f.render(text, True, color)
            screen.blit(surf, ((W - surf.get_width()) // 2, y))
            y += surf.get_height() + 14
        pygame.display.flip()

        deadline = time.time() + 8.0
        while time.time() < deadline:
            for ev in pygame.event.get():
                if ev.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                    return
            pygame.time.wait(50)
    except Exception:
        pass


def _selfcheck(frames: int = 240) -> int:
    """无窗口自检：跳过菜单，直接开一局真实对局跑几百帧。

    为什么需要它：onefile 打出来的 exe 一旦在**运行时**缺模块或资源，
    表现就是双击后一闪而过，什么线索都不留。而最容易被漏掉的恰恰是
    "进了游戏才会走到"的那条路径 —— 地图烘焙、21 个敌人的 AI、整帧渲染。
    菜单能显示不代表游戏能玩。

    所以这里不复用任何测试替身：真实 Game、真实按键、真实 update、
    真实 _render（dummy 驱动下所有绘制代码照样会走一遍）。
    打包后 `潜行协议.exe --selfcheck` 就能验一次，不用靠人盯着屏幕看。
    """
    # 必须赶在 Game() 里的 pygame.init() 之前设：video/audio 驱动是初始化时读的
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    print(f"[自检] 无窗口自检：{frames} 帧（≈{frames / 60:.1f}s 游戏时间）")
    try:
        g = Game()
        g.menu.active = False          # 跳过主菜单，直接进局
        g._show_intro = False
        p, w = g.player, g.world
        start_p = (p.x, p.y)
        enemy_start = [(e.x, e.y) for e in w.enemies]
        print(f"[自检] 窗口 {C.WINDOW_W}x{C.WINDOW_H}  敌人 {len(w.enemies)}  "
              f"补给 {len(w.pickups)}")

        dt = 1.0 / 60.0
        # 一直往右走：给移动/碰撞/体力的整条链路施加真实输入，
        # 而不是让世界"自己空转"——空转测不出输入到渲染的那一段。
        p._keys = {pygame.K_d, pygame.K_LSHIFT}
        try:
            for _ in range(frames):
                w.update(dt)
                g._time_accum += dt
                g._render(dt)
        finally:
            p._keys = set()

        moved = math.hypot(p.x - start_p[0], p.y - start_p[1])
        enemy_moved = sum(
            1 for e, s in zip(w.enemies, enemy_start)
            if math.hypot(e.x - s[0], e.y - s[1]) > 0.05
        )
        print(f"[自检] 玩家移动 {moved:.2f} 格 / 敌人动起来 {enemy_moved} 个 / "
              f"状态 {w.state}")

        fails = []
        if moved < 0.5:
            fails.append(f"玩家几乎没动（{moved:.2f} 格）：输入→移动链路断了")
        if enemy_moved == 0:
            fails.append("没有一个敌人移动过：AI 更新没跑起来")
        if fails:
            for msg in fails:
                print(f"[自检] [FAIL] {msg}")
            return 1
        # 这里刻意不用 ✅ / ❌：Windows 控制台默认 GBK，emoji 编不出来会抛
        # UnicodeEncodeError —— 自检本身因此"失败"，比不打印还误导人。
        print("[自检] [OK] 通过：导入、地图烘焙、AI 更新、整帧渲染都正常")
        return 0
    except BaseException as exc:      # noqa: BLE001 —— 自检要把锅全接下来
        traceback.print_exc()
        print(f"[自检] [FAIL] 崩溃：{type(exc).__name__}: {exc}")
        return 1
    finally:
        try:
            pygame.quit()
        except Exception:
            pass


def main() -> int:
    """入口：正常退出返回 0，崩溃返回 1 并留下 crash.log。"""
    # 自检要放在最前面：它故意跳过菜单和存档，也不该被数据目录重定向影响
    if "--selfcheck" in sys.argv:
        return _selfcheck()
    start = 0
    if "--level" in sys.argv:
        try:
            start = int(sys.argv[sys.argv.index("--level") + 1]) - 1
        except (IndexError, ValueError):
            print("用法：潜行协议.exe --level 3   （从第 3 关开始）", file=sys.stderr)
            return 2
        if not 0 <= start < len(L.LEVELS):
            print(f"--level 只能是 1~{len(L.LEVELS)}", file=sys.stderr)
            return 2
    anchored = _frozen_data_dir()
    if anchored is not None:
        settings.DATA_DIR = anchored
        records.DATA_DIR = anchored
    try:
        Game(start_level=start).run()
    except KeyboardInterrupt:
        print("\n已手动中断。")
        return 0
    except BaseException as exc:      # noqa: BLE001 —— 崩溃现场必须留下来
        log = _dump_crash(exc)
        print(f"[崩溃] {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        if log is not None:
            print(f"完整堆栈已写入：{log}", file=sys.stderr)
        _show_crash(exc, log)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
