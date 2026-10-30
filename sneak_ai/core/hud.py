"""HUD：把"玩家看不到但 AI 知道的信息"翻译成可读的界面。

设计原则（潜行游戏的关键）：
1. 玩家必须能判断"我现在安全吗"—— 所以有暴露度条 + 状态文字 + 屏幕边缘红光；
2. 玩家必须能判断"敌人在想什么"—— 所以敌人头顶有 ? / ! 气泡；
3. 信息不能多到变成雷达 —— 所以只给"状态"，不给敌人的规划路径。
"""

import math

import pygame

from .. import constants as C
from ..ai.coordination import Role
from .ui import TextCache, draw_bar, draw_panel, render


class HUD:
    def __init__(self):
        self.tc = TextCache()
        self.fps_smooth = 60.0
        self._overlay = None
        self._tip: str = ""
        self._tip_left: float = 0.0
        self._tip_life: float = 1.0
        self._tip_age: float = 0.0

    def show_tip(self, text: str, life: float = 4.0) -> None:
        """引导提示：一次只显示一条，新的直接顶掉旧的。"""
        self._tip = text
        self._tip_life = life
        self._tip_left = life
        self._tip_age = 0.0

    # ============ 主 HUD ============
    def draw(self, screen, world, player, t: float, dt: float, muted: bool) -> None:
        self.fps_smooth += (1.0 / max(dt, 1e-6) - self.fps_smooth) * 0.05
        self._draw_status_panel(screen, world)
        self._draw_exposure(screen, world, t)
        self._draw_stamina(screen, player)
        self._draw_ammo(screen, player)
        self._draw_stones(screen, player)
        self._draw_shield(screen, player)
        self._draw_center_notice(screen, world, t, muted)
        self._draw_tip(screen, dt)
        self._draw_hint(screen)

    def _draw_status_panel(self, screen, world) -> None:
        box = pygame.Rect(12, 12, 322, 128)
        draw_panel(screen, box, alpha=185)

        if world.state == C.STATE_INFILTRATE:
            goal, gcol = "目标：潜入数据终端", C.COL_TERMINAL
        else:
            goal, gcol = "目标：返回撤离点", C.COL_EXFIL
        screen.blit(
            self.tc.get("goal", goal, 20, gcol, bold=True), (26, 22)
        )

        line = f"用时 {world.elapsed:6.1f}s    被发现 {world.spotted} 次"
        screen.blit(self.tc.get("time", line, 18, C.COL_TEXT), (26, 52))

        chasing = sum(1 for e in world.enemies if e.alive and e.role != Role.PATROL)
        alive = sum(1 for e in world.enemies if e.alive)
        downed = len(world.enemies) - alive
        # "在岗 18/21" 而不是"哨兵 21"：玩家真正关心的是"还有几个人挡着路"，
        # 总数只在开局有意义，打起来之后它是废话。
        line2 = (f"在岗 {alive}/{len(world.enemies)}    追击/包抄 {chasing}"
                 + (f"    倒地 {downed}" if downed else ""))
        screen.blit(self.tc.get("count", line2, 18, C.COL_TEXT_DIM), (26, 78))

        # 尸体被同僚发现：这是"开枪"的即时代价，必须写出来，
        # 否则玩家只会感觉"杀完之后突然到处都有人"，学不到"要躲着杀"。
        if world.bodies_found:
            extra = f"尸体已暴露 {world.bodies_found} 次"
            screen.blit(self.tc.get("body", extra, 16, (255, 122, 110)), (26, 104))

        # 报警状态角标（带倒计时：让玩家知道封锁还剩多久，能决定"等"还是"冲"）
        if world.alert:
            left = world.alert_left
            text = f"全图警觉 {left:.0f}s"
            color = (255, 120, 100) if left > 5 else (255, 190, 90)
            tag = self.tc.get("alert_tag", text, 16, color, bold=True)
            # 贴在面板右缘内侧、与目标行同高：原来放在 (right-14, 24) 会和
            # 顶部操作提示打架（两者都在第一行高度上抢同一片像素）
            x = box.right - tag.get_width() - 14
            y = 22 if x > 26 + 200 else 46      # 目标行太长时让到第二行
            screen.blit(tag, (x, y))

    def _draw_exposure(self, screen, world, t: float) -> None:
        """暴露度条：全场最高可疑度。这是玩家判断"能不能动"的唯一依据。"""
        box = pygame.Rect(12, 140, 322, 52)
        draw_panel(screen, box, alpha=170)

        threat = world.threat
        if threat < C.SUSPICION_CURIOUS:
            color, label = C.COL_SAFE, "暴露"
        elif threat < C.SUSPICION_CONFIRM:
            color, label = C.COL_SUSPECT, "起疑"
        else:
            color, label = C.COL_DANGER, "已被发现"

        # 标签宽度随字体/文案变化，所以进度条位置按实际文字宽度算，
        # 否则换个字体（或"已被发现"这种长标签）就会跟条子叠在一起。
        # 16px 而非 17px：这块面板只有 52px 高，要塞下"标签 + 进度条 + 状态行"
        # 三行，17px 的标签行高 23 会把状态行顶出面板底边。
        label_surf = self.tc.get("exp_label", label, 16, color, bold=True)
        screen.blit(label_surf, (26, 148))
        bar_x = 26 + label_surf.get_width() + 10
        bar = pygame.Rect(bar_x, 151, max(60, box.right - 14 - bar_x), 12)
        draw_bar(screen, bar, threat, color)

        # 阈值刻度：0.35 起疑 / 0.70 确认 —— 让玩家知道"还差多少被发现"
        for ratio in (C.SUSPICION_CURIOUS, C.SUSPICION_CONFIRM):
            x = bar.x + int(bar.width * ratio)
            pygame.draw.line(screen, (16, 18, 22), (x, bar.y), (x, bar.bottom), 1)

        if world.seeing:
            # 带上复数的处理：1 个说"哨兵"，多个说"哨兵们"，中文不该出现"1 个哨兵们"
            n = world.seeing
            note, ncol = f"{n} 个哨兵正看着你" if n > 1 else "有哨兵正看着你", C.COL_DANGER
        elif threat >= C.SUSPICION_CURIOUS:
            note, ncol = "有哨兵在找你", C.COL_SUSPECT
        else:
            note, ncol = "未被察觉", C.COL_TEXT_DIM
        # 13px + y=172：16px 粗体标签占 148~170，13px 行高 18，
        # 172+18=190 正好收在面板底边 192 之内。整块面板的纵向预算 =
        # 6(上边距) + 22(标签) + 2(间隙) + 18(状态行) + 4(下边距) = 52。
        screen.blit(self.tc.get("seeing", note, 13, ncol), (26, 172))

    def _draw_stamina(self, screen, player) -> None:
        box = pygame.Rect(12, C.WINDOW_H - 62, 232, 50)
        draw_panel(screen, box, alpha=170)
        ratio = player.stamina / C.STAMINA_MAX
        if ratio > 0.5:
            color = (110, 200, 255)
        elif ratio > 0.2:
            color = C.COL_SUSPECT
        else:
            color = C.COL_DANGER
        screen.blit(self.tc.get("stam_label", "体力", 16, C.COL_TEXT), (26, C.WINDOW_H - 56))
        draw_bar(screen, pygame.Rect(76, C.WINDOW_H - 53, 152, 12), ratio, color)
        if player.sprinting:
            # 13px + y-32：16px 标签占 H-56~H-34，13px 行高 18，
            # H-32+18 = H-14，稳稳收在面板底边 H-12 之内。
            # （之前写 H-28 时 932+18=950，溢出面板底 2px）
            screen.blit(self.tc.get("sprint", "冲刺中 · 噪音大", 13, C.COL_SUSPECT),
                        (26, C.WINDOW_H - 32))
        elif ratio < 0.05:
            screen.blit(self.tc.get("tired", "力竭 · 停下恢复", 13, C.COL_DANGER),
                        (26, C.WINDOW_H - 32))

    def _draw_ammo(self, screen, player) -> None:
        """弹药：5 发画成 5 颗子弹。

        和石子用同一种"实体计数"而不是数字 —— 玩家在紧张时要的是
        "还剩两发"这个量，不是"2"这个符号。打空时整块面板变红。
        """
        # 槽位数必须按**上限**画（AMMO_CAP），不是按初始携带量：
        # 沿途能补给到 8 发，面板还只画 5 格的话，玩家捡满了也看不出来。
        slots = max(C.WEAPON_AMMO, C.AMMO_CAP)
        box = pygame.Rect(252, C.WINDOW_H - 62, 156, 50)
        draw_panel(screen, box, alpha=170)
        left = player.ammo
        color = C.COL_TEXT if left > 0 else C.COL_DANGER
        screen.blit(self.tc.get(f"ammo_label_{left > 0}", "弹药" if left else "空仓",
                                17, color), (266, C.WINDOW_H - 42))
        for i in range(slots):
            cx = 320 + i * 11
            cy = C.WINDOW_H - 37
            rect = pygame.Rect(cx, cy - 7, 5, 15)
            if i < left:
                pygame.draw.rect(screen, (206, 176, 104), rect, border_radius=2)
                pygame.draw.rect(screen, (246, 226, 170),
                                 (cx, cy - 7, 5, 4), border_radius=2)
            else:
                pygame.draw.rect(screen, (58, 62, 70), rect, 1, border_radius=2)

    def _draw_stones(self, screen, player) -> None:
        """石子余量：把"还剩几次主动干扰"直接画成实体圆点。

        不用数字而用圆点：玩家在紧张时需要的是"还有 2 个"这种一眼即得的量，
        而不是读一个数字。用完时标签变红，同样是一眼。
        """
        # 同样按上限（STONE_CAP）画槽位，能补给到 5 颗就得看得见 5 格
        slots = max(C.STONE_COUNT, C.STONE_CAP)
        box = pygame.Rect(412, C.WINDOW_H - 62, 176, 50)
        draw_panel(screen, box, alpha=170)
        left = player.stones
        label = "投石" if left > 0 else "用尽"
        color = C.COL_TEXT if left > 0 else C.COL_DANGER
        screen.blit(self.tc.get(f"stone_label_{left > 0}", label, 17, color),
                    (426, C.WINDOW_H - 46))
        # 圆点起点从 468 改到 482：满值 5 颗时第一个圆点左缘正好贴住"投石"
        # 标签（468-6=462 = 标签右缘），中间至少留 12px 空隙
        for i in range(slots):
            cx = 482 + i * 20
            cy = C.WINDOW_H - 37
            if i < left:
                pygame.draw.circle(screen, (196, 188, 170), (cx, cy), 6)
                pygame.draw.circle(screen, (236, 230, 216), (cx - 2, cy - 2), 2)
            else:
                pygame.draw.circle(screen, (60, 65, 74), (cx, cy), 6, 1)

    def _draw_shield(self, screen, player) -> None:
        """护盾：用一个六边形 + 文字，而不是"盾 1"这种抽象数字。

        护盾只在被抓住的瞬间生效，是整局最关键的一张牌 ——
        它必须是玩家余光里一直在的状态，不能藏在菜单里。
        没拿到时画成暗的轮廓：让玩家知道"地图上有这个东西"，而不是完全没有概念。
        """
        box = pygame.Rect(596, C.WINDOW_H - 62, 150, 50)
        draw_panel(screen, box, alpha=170)
        held = getattr(player, "shield", 0) > 0
        color = C.COL_PICKUP_SHIELD if held else (96, 104, 116)
        screen.blit(
            self.tc.get(f"shield_label_{held}", "护盾" if held else "无护盾",
                        17, color), (610, C.WINDOW_H - 46)
        )
        cx, cy = 706, C.WINDOW_H - 37
        pts = []
        for i in range(6):
            a = math.pi / 6 + i * math.pi / 3
            pts.append((cx + math.cos(a) * 11, cy + math.sin(a) * 11))
        if held:
            pygame.draw.polygon(screen, C.COL_PICKUP_SHIELD, pts, 2)
            pygame.draw.circle(screen, C.COL_PICKUP_SHIELD, (cx, cy), 4, 1)
        else:
            pygame.draw.polygon(screen, (80, 86, 96), pts, 1)

    def _draw_center_notice(self, screen, world, t: float, muted: bool) -> None:
        """屏幕顶部中央：报警横幅 / 当前处境。"""
        if world._alert_timer > 0:
            a = min(1.0, world._alert_timer / 1.0)
            txt = self.tc.get("alert_banner", "警报 · 敌人已警觉", 38, (255, 118, 96), bold=True)
            txt.set_alpha(int(255 * a))
            rect = txt.get_rect(center=(C.WINDOW_W // 2, 86))
            # 加一块深色底板：纯红字压在深灰地板上对比度太低，远处根本读不出来
            plate = rect.inflate(52, 20)
            bg = pygame.Surface(plate.size, pygame.SRCALPHA)
            bg.fill((12, 14, 18, int(150 * a)))
            screen.blit(bg, plate.topleft)
            pygame.draw.rect(screen, (150, 40, 34), plate, 1, border_radius=4)
            screen.blit(txt, rect)
            return

        if world.state != C.STATE_INFILTRATE and world.state != C.STATE_EXFILTRATE:
            return

        # 持续的处境文字（随暴露度变色 + 轻微呼吸）
        # 不能贴着底边居中：W11 加了护盾面板（x 596~746），居中在 640 的文字
        # 正好压住"护盾"标签。抬到底栏上方，与面板错开。
        threat = world.threat
        if threat >= C.SUSPICION_CONFIRM:
            text, color = "暴露！快找掩体", C.COL_DANGER
            alpha = 200 + int(55 * (0.5 + 0.5 * math.sin(t * 9)))
        elif threat >= C.SUSPICION_CURIOUS:
            text, color = "有人在起疑…", C.COL_SUSPECT
            alpha = 220
        else:
            text, color = "潜行中", (150, 215, 188)
            # 130 太淡：压在深色地板上几乎看不见。有底板兜底后
            # 200 已经足够"低调"，同时保证一眼读得到。
            alpha = 200
        surf = self.tc.get("notice", text, 22, color, bold=True)
        surf = surf.copy()
        surf.set_alpha(alpha)
        rect = surf.get_rect(center=(C.WINDOW_W // 2, C.WINDOW_H - 96))
        # 底部这行字直接压在地板上，"潜行中"那种低饱和绿在暖色地板上几乎消失。
        # 底板透明度**不跟着文字走**：文字可以淡（它是状态，不该抢戏），
        # 但底板必须够实，否则"淡淡的一层灰"叠在深色地板上等于没画。
        plate = rect.inflate(26, 10)
        bg = pygame.Surface(plate.size, pygame.SRCALPHA)
        bg.fill((10, 12, 16, 165))
        screen.blit(bg, plate.topleft)
        screen.blit(surf, rect)

        if muted:
            m = self.tc.get("muted", "已静音 (M)", 15, C.COL_TEXT_DIM)
            screen.blit(m, (C.WINDOW_W - m.get_width() - 18, C.WINDOW_H - 26))

    def _tip_rect(self):
        """引导气泡的落点。结算页要用它来避让，所以单独抽出来算。

        两边必须走同一个函数：以前结算页用 18px 量"最佳纪录"的宽度，
        浮动气泡却是 16px 画的，估算就永远对不上，避让也就永远差一点。
        """
        if not self._tip:
            return None
        surf = self.tc.get("tip", self._tip, 16, (208, 220, 234))
        return surf.get_rect(center=(C.WINDOW_W // 2, C.WINDOW_H - 138))

    def _draw_tip(self, screen, dt: float) -> None:
        """上下文引导提示：资源不是白送的，走近了才教你怎么用。

        位置在处境文字上方，避开底栏面板；前 0.3 秒淡入、最后 0.6 秒淡出。
        """
        if self._tip_left <= 0.0 or not self._tip:
            return
        self._tip_left -= dt
        # 用独立的"已显示时长"计时，不要从剩余时长反推：
        # 之前写 age = life - left，第一帧 age≈0.017s，正好落进"淡入 0.3 秒"
        # 分支，alpha 被算成 14 —— 文字几乎全透明，玩家只看到一块底板。
        self._tip_age += dt
        alpha = 255
        if self._tip_age < 0.3:
            alpha = int(255 * self._tip_age / 0.3)
        elif self._tip_left < 0.6:
            alpha = int(255 * max(0.0, self._tip_left) / 0.6)
        surf = self.tc.get("tip", self._tip, 16, (208, 220, 234))
        surf = surf.copy()
        surf.set_alpha(max(0, min(255, alpha)))
        rect = self._tip_rect()
        plate = rect.inflate(24, 10)
        bg = pygame.Surface(plate.size, pygame.SRCALPHA)
        # 底板不随淡入淡出变透明（理由同处境文字）：文字可以淡，
        # 但底板一淡就等于没画。用固定 190 让提示在出现期间始终读得清。
        bg.fill((10, 12, 16, 190))
        pygame.draw.rect(bg, (86, 100, 122, 220), bg.get_rect(),
                         width=1, border_radius=5)
        screen.blit(bg, plate.topleft)
        screen.blit(surf, rect)

    def _draw_hint(self, screen) -> None:
        """常驻快捷键提示：**不画**。

        试过三个位置都失败：顶部居中压状态面板、顶部右对齐压小地图、
        底部右侧压引导提示。根本原因是它"永远是最大的一块文字"，
        而屏幕上任何一条横带都塞不下它。

        教训是：**常驻的说明书本身就是噪声**。键位在主菜单第一屏就写全了，
        玩家真忘了可以 ESC 回菜单看一眼 —— 不需要在战斗画面上
        长期占一条 500px 宽的横带。
        """
        return


    # ============ 屏幕边缘危险指示 ============
    def draw_threat_markers(self, screen, cam, world, t: float) -> None:
        """追击中的敌人不在视野内时，在屏幕边缘画一个指向它的箭头。

        这是"被包抄"这种协同 AI 行为的可感知化 —— 否则玩家只会觉得
        "怎么突然有人从背后出现"，而不是"他们在包围我"。

        W12 扩展：**起疑但还没确认**的哨兵也画，但用较小的弱箭头。
        原因：玩家最难判断的就是"我已经被瞄上了、但还没被锁定"这一档，
        而它恰恰是最该马上躲的时刻。只标追兵等于"火烧起来才报警"。
        """
        cx, cy = C.WINDOW_W / 2, C.WINDOW_H / 2
        margin = 40
        for e in world.enemies:
            if not e.alive:
                continue
            confirmed = e.role != Role.PATROL
            curious = e.suspicion >= C.SUSPICION_CURIOUS
            if not (confirmed or curious):
                continue
            sx, sy = cam.world_to_screen(e.x, e.y)
            if 0 <= sx <= C.WINDOW_W and 0 <= sy <= C.WINDOW_H:
                continue
            dx, dy = sx - cx, sy - cy
            if dx == 0 and dy == 0:
                continue
            ang = math.atan2(dy, dx)
            hw = C.WINDOW_W / 2 - margin
            hh = C.WINDOW_H / 2 - margin
            kx = hw / abs(dx) if abs(dx) > 1e-6 else 1e9
            ky = hh / abs(dy) if abs(dy) > 1e-6 else 1e9
            k = min(kx, ky)
            px, py = cx + dx * k, cy + dy * k

            if confirmed:
                color = C.COL_ROLE.get(e.role.value, C.COL_ENEMY)
                size = 13
                base_alpha = 90
                span = 120
            else:
                # 起疑：暗黄、小一号、更淡 —— 明确区分"在找你"和"冲你来"
                color = C.COL_SUSPECT
                size = 9
                base_alpha = 45
                span = 80
            pulse = 0.55 + 0.45 * math.sin(t * 6.0)
            tip = (px + math.cos(ang) * size, py + math.sin(ang) * size)
            left = (px + math.cos(ang + 2.4) * size, py + math.sin(ang + 2.4) * size)
            right = (px + math.cos(ang - 2.4) * size, py + math.sin(ang - 2.4) * size)
            arrow = pygame.Surface((int(size * 3), int(size * 3)), pygame.SRCALPHA)
            local = [(x - px + size * 1.5, y - py + size * 1.5) for x, y in (tip, left, right)]
            pygame.draw.polygon(arrow, (*color, int(base_alpha + span * pulse)), local)
            screen.blit(arrow, (px - size * 1.5, py - size * 1.5))

    # ============ 结算页 ============
    def _defeat_reason(self, world) -> str:
        """把"被抓"翻译成一句能学到东西的话。

        三档信息，优先给最有指导性的那个：
        1. 谁抓的（角色）—— 巡逻兵/追兵/包抄，区别直接说明"你踩的是巡逻线还是已被锁定"；
        2. 从哪个方向来 —— 告诉玩家该回头看；
        3. 抓住你时它有多近 —— 0.6 格，等于贴着。
        全部拿不到就返回空串，不硬编一句废话。
        """
        e = getattr(world, "captured_by", None)
        p = world.player
        if e is None or p is None:
            return ""
        role_name = {"patrol": "巡逻哨兵", "chase": "追兵", "flank": "包抄手",
                     "search": "搜索队"}.get(e.role.value, "哨兵")
        dx, dy = e.x - p.x, e.y - p.y
        # 方位必须写成"它在你的哪一边"，不能写成"从哪侧抓住你"：
        # 后者主语含糊，玩家会理解反（以为是自己从东边走过去）。
        if abs(dx) > abs(dy):
            where = "东边" if dx > 0 else "西边"
        else:
            where = "南边" if dy > 0 else "北边"
        if e.role.value == "patrol" and e.suspicion < C.SUSPICION_CONFIRM:
            why = "它还没确认你的位置，是你在它眼皮底下走过去了"
        else:
            why = "它已经盯着你，直到把你堵住"
        return f"{role_name}从你{where}贴上来抓住了你 —— {why}"

    def _progress_text(self, world) -> str:
        """失败时的"你走到哪了"：比一个 0 分更能告诉玩家"就差一点"。"""
        if getattr(world, "has_data", False):
            return "已拿到数据 · 回撤途中"
        if world.state == C.STATE_DEFEAT and world.elapsed <= 0.1:
            return "刚开局"
        return "潜入途中"

    def draw_end(self, screen, world, best: dict, new_record: bool, t: float) -> None:
        """结算页是静态的（只有"新纪录"在闪），所以遮罩和文字全部走缓存，
        不要在停留的这几秒里每帧重新渲染一遍。"""
        if self._overlay is None:
            self._overlay = pygame.Surface((C.WINDOW_W, C.WINDOW_H), pygame.SRCALPHA)
            self._overlay.fill((0, 0, 0, 185))
        screen.blit(self._overlay, (0, 0))

        cx, cy = C.WINDOW_W // 2, C.WINDOW_H // 2

        if world.state == C.STATE_VICTORY:
            title, tcol = "任务完成", (120, 240, 190)
            rank = world.rank()
            rcol = {
                "S": (255, 215, 0), "A": (120, 240, 190),
                "B": (120, 200, 255), "C": (200, 200, 200),
            }[rank]
        else:
            title, tcol = "任务失败", (255, 84, 64)
            rank, rcol = "-", (200, 200, 200)

        # ---- 第一段：顶部固定件（标题 / 失败原因 / 评级章） ----
        # 66px 的标题行高 88，占 cy-194 ~ cy-106；评级章上沿要在 cy-215 之下
        surf = self.tc.get("end_title", title, 62, tcol, bold=True)
        screen.blit(surf, surf.get_rect(center=(cx, cy - 258)))

        # 失败时写清"谁抓的·从哪来·当时多近"：没有这句，失败画面只是
        # 一句"任务失败"，玩家学不到任何可迁移的教训。
        if world.state == C.STATE_DEFEAT:
            reason = self._defeat_reason(world)
            if reason:
                r = self.tc.get("end_reason", reason, 17, (255, 170, 150))
                screen.blit(r, r.get_rect(center=(cx, cy - 196)))

        if world.state == C.STATE_VICTORY:
            # 93px 的评级字，行高 127 —— 上沿必须落在 cy-215 才不压下面的统计表
            badge = self.tc.get("end_rank", rank, 93, rcol, bold=True)
            screen.blit(badge, badge.get_rect(center=(cx, cy - 150)))
            sub = self.tc.get("end_rank_label", "评级", 17, C.COL_TEXT_DIM)
            screen.blit(sub, sub.get_rect(center=(cx, cy - 78)))

        # ---- 第二段：先算清楚每一行占多高，再决定画在哪 ----
        # 结算页是纵向堆叠、行数还随内容变（拿到几项风格分就有几行），
        # 边算边画的话，一遇到"多一行"就会撞上下面已经画好的东西。
        # 所以先把所有候选行排成一张待画清单（带真实行高），
        # 定完版式再统一 blit —— 布局和绘制彻底分开。
        stats = [
            ("用时", f"{world.elapsed:.1f} 秒"),
            ("被发现", f"{world.spotted} 次"),
            ("击倒", f"{world.kills} 人"),
        ]
        # 只在真发生过时才占一行：S 档玩家不该在结算页看到"尸体暴露 0 次"
        if world.bodies_found:
            stats.append(("尸体暴露", f"{world.bodies_found} 次"))
        # 得分只在通关时显示。失败也给分是错的：得分衡量的是"完成任务的效率"，
        # 任务没完成就没有效率可言 —— 而且"被秒抓却拿 880 分"比 0 分更像 bug。
        if world.state == C.STATE_VICTORY:
            stats.append(("得分", str(world.score())))
        else:
            stats.append(("进度", self._progress_text(world)))
        # 行距必须 ≥ 行高：22px 字号的实际行高是 31（含上下留白），
        # 写 30 会每行蹭掉 1px —— 单看一处像"没对齐"，连着四行就是"糊"。
        stat_rows = []
        for i, (label, value) in enumerate(stats):
            l = self.tc.get(f"stat_l{i}", label, 19, C.COL_TEXT_DIM)
            v = self.tc.get(f"stat_v{i}", value, 22, C.COL_TEXT, bold=True)
            stat_rows.append((max(l.get_height(), v.get_height()), l, v))

        # ---- 第三段：底部先钉死，中间的空白交给风格分去填 ----
        # 提示/新纪录/最佳纪录都在底部，位置固定；中间那块的高度是靠
        # 剩下的空白算出来的，放不下就整组缩字号 —— 而不是硬挤上去叠字。
        hint_surf = self.tc.get("end_hint", "R 再来一局    ·    ESC 回主菜单", 20,
                                (255, 255, 120))
        hint_y = C.WINDOW_H - 44
        if new_record and world.state == C.STATE_VICTORY:
            nr_y = hint_y - 52
            best_y = nr_y - 46
        else:
            nr_y = None
            best_y = hint_y - 44

        if best:
            line = (f"最佳纪录  {best.get('score', 0)} 分  ·  "
                    f"{best.get('time', 0)} 秒  ·  {best.get('rank', '-')} 评级")
            b = self.tc.get("end_best", line, 18, (255, 215, 140))
        else:
            # 不能用 "暂无最佳纪录"：这里要是把"暂无最佳纪录"当整行显示，
            # 外层再拼一次"最佳纪录"就成了"最佳纪录 暂无最佳纪录 · A 评级"。
            b = self.tc.get("end_best", "最佳纪录  暂无", 18, C.COL_TEXT_DIM)
        b_rect = b.get_rect(center=(cx, best_y))

        # 结算页浮在 HUD 之上，但 HUD 的底栏**没有**让位（体力/弹药/石子/护盾
        # 仍然画着）。上半版式是钉死的，动不了，所以避让只能由底部完成：
        # 如果"最佳纪录"这一行会压到底栏或引导气泡，就把整套底部提示
        # 整体上移到气泡上方 —— 这是唯一不会破坏纵向节奏的解法。
        tip_rect = self._tip_rect()
        bottom_box = pygame.Rect(0, C.WINDOW_H - 152, C.WINDOW_W, 152)
        nudge = 0
        if b_rect.colliderect(bottom_box) or (
                tip_rect is not None and b_rect.colliderect(tip_rect)):
            top = tip_rect.top if tip_rect is not None else C.WINDOW_H - 152
            nudge = best_y - (top - 26)        # > 0 表示要往上挪这么多
        if nudge > 0:
            hint_y -= nudge
            best_y -= nudge
            if nr_y is not None:
                nr_y -= nudge
            b_rect = b.get_rect(center=(cx, best_y))
        # "★ 新纪录 ★" 是 26px 粗体、行高 35，而它到"最佳纪录"之间只留了 46px
        # 的**中心距** —— 两个中心一相减就只剩 11px，字身会啃到上面那行的边。
        # 这个间距也跟着一起挪，等于没挪。所以整体上移之后要把间隔重新撑开。
        if nudge > 0 and nr_y is not None:
            nr_h = self.tc.get("end_new", "★ 新纪录 ★", 26, (255, 226, 120),
                               bold=True).get_height()
            nr_y = min(nr_y, b_rect.top - 6 - nr_h // 2)

        # 统计表：紧跟在评级章下方，行距按真实行高推
        y = cy + 4
        for h, l, v in stat_rows:
            step = max(32, h + 4)
            screen.blit(l, l.get_rect(midright=(cx - 16, y)))
            screen.blit(v, v.get_rect(midleft=(cx + 16, y)))
            y += step

        screen.blit(b, b_rect)

        if nr_y is not None:
            pulse = 0.6 + 0.4 * math.sin(t * 5)
            nr = self.tc.get("end_new", "★ 新纪录 ★", 26, (255, 226, 120), bold=True)
            nr = nr.copy()
            nr.set_alpha(int(140 + 115 * pulse))
            screen.blit(nr, nr.get_rect(center=(cx, nr_y)))

        screen.blit(hint_surf, hint_surf.get_rect(center=(cx, hint_y)))

        # ---- 风格分：填在统计表和"最佳纪录"之间，越界就整组缩小 ----
        # 只列真的拿到的项 —— 没拿到什么都不显示，塞一排"未达成"只是在提醒失败。
        bonus = world.style_bonus() if world.state == C.STATE_VICTORY else []
        if not bonus:
            return
        penalised = world.style_total() < sum(v for _, v in bonus)
        rows = len(bonus) + (1 if penalised else 0)
        room = (b_rect.top - 14) - (y + 16)          # 可用高度
        for size, head, step, gap in ((16, 15, 22, 8), (15, 14, 20, 6),
                                      (14, 13, 18, 4), (13, 12, 17, 2)):
            need = gap + step + rows * step
            if need <= room:
                break
        else:
            return                                # 实在放不下：宁可不显示
        y += gap
        head = self.tc.get(f"style_head_{size}", "风格分", head, (140, 150, 165))
        screen.blit(head, head.get_rect(center=(cx, y)))
        y += step
        for i, (name, val) in enumerate(bonus):
            row = self.tc.get(f"style_{i}_{size}", f"{name}  +{val}", size,
                              (150, 220, 190))
            screen.blit(row, row.get_rect(center=(cx, y)))
            y += step
        if penalised:
            # 出现了反向扣分（反复被同一人发现）：单独说清楚，
            # 否则玩家会算不明白"分项加起来对不上总分"
            pen = self.tc.get(f"style_pen_{size}", "反复被发现  −40", size,
                              (235, 130, 120))
            screen.blit(pen, pen.get_rect(center=(cx, y)))
