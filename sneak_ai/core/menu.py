"""主菜单：一启动就进战场，对第一次玩的人来说太粗暴了。

GitHub 上点进来试玩的人，前 10 秒决定他关不关标签页 —— 他需要的是
"这是个什么游戏 / 我要按什么 / 从哪开始"，而不是立刻被 21 个哨兵包夹。

菜单只有一屏，每一行都在回答一个问题：
- 标题 + 一句话 → 这是什么游戏；
- 最佳纪录 → "有人打通过"，给目标感；
- 操作摘要 → 我现在该按什么；
- 关卡选择 → 我想看的是哪一关。

**关卡选择是给评审开的一条路，不是难度菜单。** 五关串起来是一条行动，
但在第 4 关只有 25% 胜率的情况下，"必须先打通四关才能看到第 5 关"等于
让绝大多数第一次点开的人永远看不到这一版真正做的东西。所以每一关都能直接进，
只是从第 2 关起步的局不写进最佳纪录 —— 存档是玩家唯一的数据，
不能让"跳关 35 秒打完"变成一条刷不掉的假纪录（这条规则原本就写在
`_handle_scoring` 里，给 `--level` 用的，这里只是把它搬到界面上）。
"""

import math

import pygame

from .. import constants as C
from . import records
from . import levels as L
from .ui import TextCache, font

_BG = (14, 16, 20)
_DIM = (140, 150, 162)
_FAINT = (86, 94, 106)
_TEXT = (226, 232, 240)


class Menu:
    """主菜单画面与输入。self.active 为 False 时进入游戏。"""

    def __init__(self, best: dict, sel: int = 0):
        self.active = True
        self.tc = TextCache()
        self.best = best
        self.sel = max(0, min(int(sel), len(L.LEVELS) - 1))
        # draw() 把每块文字的落点记下来。菜单是唯一没人审计的界面，
        # 而它恰好被"多加一行就顶到回车提示上"坑过一次（见下面 rows 的注释）。
        self.rects = []
        # 左右箭头的可点区域：(rect, ±1)。第一帧之前点鼠标也不能崩。
        self._arrows = []

    # ---- 关卡选择 ----
    @property
    def cleared(self) -> int:
        """最佳纪录里打到过第几关（0 = 一关都没通）。"""
        return max(0, min(int(self.best.get("levels") or 0), len(L.LEVELS)))

    def move(self, step: int, sfx) -> None:
        n = len(L.LEVELS)
        if n < 2:
            return
        new = max(0, min(self.sel + step, n - 1))
        if new != self.sel:
            self.sel = new
            sfx.play("pickup", cooldown=0.03, volume=0.28)

    def status_text(self) -> str:
        """选关行右边那句小字：这一关通关没有、本关最佳多少。

        跳关现在**会**记本关成绩（以前是整局都不记，练十遍没反馈），
        但整局总榜仍然只认从第 1 关起步的那一条 —— 这两件事不一样，
        所以这里只说"本关"，"不计总榜"那句放在底部，别把中间这行撑爆。
        """
        row = records.level_best(self.best, self.sel)
        best = f"本关最佳 {row['score']} 分" if row else ""
        if self.sel == 0:
            return f"正式开局 · {best}" if best else "正式开局"
        if self.sel < self.cleared:
            return f"已通关 · {best}" if best else "已通关"
        return f"未通关 · {best}" if best else "未通关 · 可试玩"

    def handle_event(self, ev, sfx) -> None:
        if ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                self.active = False
            elif ev.key == pygame.K_LEFT:
                self.move(-1, sfx)
            elif ev.key == pygame.K_RIGHT:
                self.move(1, sfx)
            elif ev.key == pygame.K_m:
                sfx.toggle_mute()
                from . import settings
                settings.save(sfx.muted)
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            # 点在箭头附近 = 换关；点别处 = 开局。箭头被画成可点的样子，
            # 就不能点下去反而直接进游戏 —— 那是最容易让人骂一句的交互。
            for rect, step in self._arrows:
                if rect.collidepoint(ev.pos):
                    self.move(step, sfx)
                    return
            self.active = False

    def foot_text(self, muted: bool) -> str:
        """底部那行小字：音效开关、退出，以及跳关时的成绩范围说明。

        单独抽出来是为了能被测试断言。菜单的文案规则和选关规则一样，
        都属于"说错了玩家就误解"的东西，不该只活在 draw() 里的一个 f-string 中。
        """
        foot = f"M 音效：{'开' if not muted else '关'}    ESC 退出"
        if self.sel > 0:
            foot += "    · 跳关只记本关成绩，不进总榜"
        return foot

    def draw(self, screen, muted: bool, t: float) -> None:
        W, H = screen.get_width(), screen.get_height()
        screen.fill(_BG)
        self.rects = []

        def blit(surf, center, name):
            rect = surf.get_rect(center=center)
            screen.blit(surf, rect)
            self.rects.append((name, rect))
            return rect

        # 顶部细线 + 标题：只做排版，不堆特效
        pygame.draw.line(screen, (52, 58, 68), (W // 2 - 180, H // 2 - 170),
                         (W // 2 + 180, H // 2 - 170), 1)
        # 标题两行之间原来只有 36 像素，而 52 号字的包围盒有 69 高 ——
        # "STEALTH PROTOCOL" 一直是压在"潜行协议"的下沿上的。菜单没人审计，
        # 所以这处重叠摆了四个版本；现在用 draw() 记下来的 rect 守着（test_w25）。
        blit(self.tc.get("title", "潜 行 协 议", 52, (238, 226, 198), bold=True),
             (W // 2, H // 2 - 116), "title")
        blit(self.tc.get("sub", "STEALTH PROTOCOL", 17, _FAINT),
             (W // 2, H // 2 - 66), "sub")

        # 关卡数从注册表里读，不写死 —— 加第三关时这里不该改。
        n = len(L.LEVELS)
        blit(self.tc.get("desc", f"连续潜入 {n} 处设施，拿到数据，活着回来。",
                         19, _DIM), (W // 2, H // 2 - 30), "desc")

        # 最佳纪录：有就展示，给"能通关"的暗示；没有就不占地方
        if self.best.get("score"):
            line = (f"最佳纪录  {self.best.get('score', 0)} 分 · "
                    f"{self.best.get('time', 0)} 秒 · "
                    f"{self.best.get('rank', '-')} 评级")
            when = records.when_label(self.best)
            if when:
                line += f"  ·  {when}"
            legacy = records.legacy_label(self.best, n)
            if legacy:
                line += f"  ·  {legacy}"
            blit(self.tc.get("best", line, 18, (255, 215, 140)),
                 (W // 2, H // 2 + 6), "best")

        rows = [
            "WASD 移动 · Shift 潜行 · Space 冲刺 · 左键 射击",
            "Q / 右键 投石 · Tab 整张地图 · F1 AI 视图",
            "E 拉总闸（第 4 关） · 可捡弹药 / 石子 / 护盾",
        ]
        # 三行操作摘要（原先是四行）。多出来的这一行是留给选关的：
        # 四行版本在 1024x768 下最后一行正好顶到"回车开始潜入"上，
        # 所以这次不是"再塞一行"，是"腾一行"。
        y = H // 2 + 40
        for line in rows:
            blit(self.tc.get(f"ctrl_{y}", line, 16, _DIM), (W // 2, y), "ctrl")
            y += 24

        # ---- 关卡选择 ----
        # 左右各一个箭头图形，比"← →"两个字更像可以按的东西
        lv = L.LEVELS[self.sel]
        label = f"第 {self.sel + 1} 关 · {lv.name}"
        s1 = self.tc.get(("sel", self.sel), label, 19, _TEXT, bold=True)
        s2 = self.tc.get(("sel_st", self.sel, self.cleared),
                         self.status_text(), 15, _FAINT)
        # pad 是箭头和文字之间的空隙。原来两者是贴着画的（箭头占 16px，
        # 文字从 x+16 开始），文字短的时候看不出来，加了"本关最佳 XXX 分"
        # 之后整行变长，贴在一起就显得挤。左右各留 8px，两边对称。
        gap, arrow_w, pad = 14, 16, 8
        total = (arrow_w * 2 + pad * 2
                 + s1.get_width() + gap + s2.get_width())
        cy = H // 2 + 124
        x = (W - total) // 2
        self._arrows = []
        if n > 1:
            for sign in (-1, 1):
                ax = x if sign < 0 else x + total - arrow_w
                tip = ax + (6 if sign < 0 else 10)
                back = ax + (12 if sign < 0 else 4)
                pygame.draw.polygon(screen, (168, 178, 192), [
                    (tip, cy), (back, cy - 8), (back, cy + 8)])
                self._arrows.append(
                    (pygame.Rect(ax - 6, cy - 14, arrow_w + 12, 28), sign))
        r1 = s1.get_rect(midleft=(x + arrow_w + pad, cy))
        screen.blit(s1, r1)
        self.rects.append(("sel_label", r1))
        r2 = s2.get_rect(midleft=(r1.right + gap, cy))
        screen.blit(s2, r2)
        self.rects.append(("sel_status", r2))

        # 回车提示随呼吸微亮：菜单里唯一该动的东西
        pulse = 150 + 90 * (0.5 + 0.5 * math.sin(t * 2.4))
        go = self.tc.get(("go", self.sel),
                         f"回车 潜入第 {self.sel + 1} 关", 24, (120, 240, 190),
                         bold=True).copy()
        go.set_alpha(int(pulse))
        blit(go, (W // 2, H // 2 + 158), "go")

        # 跳关时补一句"只记本关"：中间那行已经写了"本关最佳 X 分"，
        # 不说清楚范围的话，玩家会以为这条成绩进了总榜（实际不会）。
        blit(self.tc.get("mute", self.foot_text(muted), 15, _FAINT),
             (W // 2, H - 40), "mute")
