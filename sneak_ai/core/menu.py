"""主菜单：一启动就进战场，对第一次玩的人来说太粗暴了。

GitHub 上点进来试玩的人，前 10 秒决定他关不关标签页 —— 他需要的是
"这是个什么游戏 / 我要按什么 / 从哪开始"，而不是立刻被 21 个哨兵包夹。

菜单只有一屏、三个选项，但每一行都在回答一个问题：
- 标题 + 一句话 → 这是什么游戏；
- 最佳纪录 → "有人打通过"，给目标感；
- 操作摘要 + 回车提示 → 我现在该按什么。
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


class Menu:
    """主菜单画面与输入。self.active 为 False 时进入游戏。"""

    def __init__(self, best: dict):
        self.active = True
        self.tc = TextCache()
        self.best = best

    def handle_event(self, ev, sfx) -> None:
        if ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                self.active = False
            elif ev.key == pygame.K_m:
                sfx.toggle_mute()
                from . import settings
                settings.save(sfx.muted)
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            self.active = False

    def draw(self, screen, muted: bool, t: float) -> None:
        W, H = screen.get_width(), screen.get_height()
        screen.fill(_BG)

        # 顶部细线 + 标题：只做排版，不堆特效
        pygame.draw.line(screen, (52, 58, 68), (W // 2 - 180, H // 2 - 170),
                         (W // 2 + 180, H // 2 - 170), 1)
        title = self.tc.get("title", "潜 行 协 议", 52, (238, 226, 198), bold=True)
        screen.blit(title, title.get_rect(center=(W // 2, H // 2 - 110)))
        sub = self.tc.get("sub", "STEALTH PROTOCOL", 17, _FAINT)
        screen.blit(sub, sub.get_rect(center=(W // 2, H // 2 - 74)))

        # 关卡数从注册表里读，不写死 —— 加第三关时这里不该改。
        n = len(L.LEVELS)
        desc = self.tc.get("desc", f"连续潜入 {n} 处设施，拿到数据，活着回来。",
                           19, _DIM)
        screen.blit(desc, desc.get_rect(center=(W // 2, H // 2 - 30)))

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
            best = self.tc.get("best", line, 18, (255, 215, 140))
            screen.blit(best, best.get_rect(center=(W // 2, H // 2 + 6)))

        rows = [
            "WASD 移动 · Shift 潜行 · Space 冲刺",
            "左键 射击 · Q / 右键 投石 · F1 AI 视图",
            "沿途可捡弹药 / 石子 / 护盾（六边形能挡一次抓捕）",
        ]
        y = H // 2 + 52
        for line in rows:
            s = self.tc.get(f"ctrl_{y}", line, 16, _DIM)
            screen.blit(s, s.get_rect(center=(W // 2, y)))
            y += 26

        # 回车提示随呼吸微亮：菜单里唯一该动的东西
        pulse = 150 + 90 * (0.5 + 0.5 * math.sin(t * 2.4))
        go = self.tc.get("go", "回车 开始潜入", 24, (120, 240, 190), bold=True)
        go = go.copy()
        go.set_alpha(int(pulse))
        screen.blit(go, go.get_rect(center=(W // 2, H // 2 + 150)))

        mute = self.tc.get("mute", f"M 音效：{'开' if not muted else '关'}    ESC 退出",
                           15, _FAINT)
        screen.blit(mute, mute.get_rect(center=(W // 2, H - 40)))
