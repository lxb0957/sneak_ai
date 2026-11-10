"""把界面上真正会出现的字符串抓下来，并按语言体检。

它不只是"清单工具"：`test_w39` 直接复用这里的场景驱动器 —— 同一套场景，
中文模式下验证 `t()` 恒等（画面不变），英文模式下扫"还有没有中文漏出来"。

## 为什么用场景驱动而不是扫源码

源码里的中文字面量有 199 条，但其中一多半是碎片：

    f"第 {lv.idx + 1} 关完成"   →   "第 " + " 关完成"

碎片翻不成英文（词序相反）。真正该翻的是**运行时拼好的整句**，
而整句只有跑起来才知道长什么样。所以这里把菜单、五关、结算、复盘、
全图、F1 调试视图各跑一遍，记录每一次经过 `t()` 的文本。

用法：

    python scripts/i18n_scan.py            # 抓字 + 体检，打印漏译
    python scripts/i18n_scan.py --dump     # 顺便把去重清单写到 i18n_strings.txt
"""

from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.core import i18n  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")


def has_cjk(text: str) -> bool:
    return bool(CJK.search(text))


def _best(cleared: int, when: str = "") -> dict:
    """造一份"打到第 cleared 关"的纪录，用来把菜单的三种门面都走一遍。"""
    per = {str(i): {"score": 900 + 40 * i, "time": 40.0 + i, "rank": "A"}
           for i in range(cleared)}
    return {"score": 1200, "time": 266.4, "rank": "A",
            "levels": cleared, "when": when, "per_level": per}


def _menu(g) -> None:
    """主菜单的每个选中项、辅助开/关、静音开/关、通关/未通关门面。"""
    for mute in (False, True):
        for assist in (False, True):
            g.sfx.muted = mute
            g.menu.assist = assist
            for best in ({}, _best(0), _best(3, "2026-11-01 21:07"), _best(5)):
                g.menu.best = best
                for sel in range(len(L.LEVELS)):
                    g.menu.sel = sel
                    g._render_menu()


def _frame(g, dt: float = 1 / 60.0, n: int = 1) -> None:
    for _ in range(n):
        g.step(dt)


def _level(g) -> None:
    """一关里能出现的画面：游戏中、F1 调试、全图、过关卡、胜利、失败、复盘。"""
    g.menu.active = False
    _frame(g, n=40)
    # 体力/暴露/资源各摆一次，把对应文案逼出来
    g.player.stamina = 1.0
    g.player.exposure = 0.95
    g.player.ammo = 0
    g.player.stones = 0
    _frame(g, n=20)
    g.debug.enabled = True
    _frame(g, n=20)
    # F1 面板的正文是"谁在什么时候改了什么主意"，只有真的走起来才会产生。
    # 用机器人驱动 30 秒，把"起疑 / 确认 / 跟丢 / 捡到补给"这些行逼出来 ——
    # 不驱动的话面板里只有表头一行，等于没测。
    try:
        import make_previews as mp
        mp._drive(g, 60 * 30)
        g._render(1 / 60)
    except Exception as exc:      # 机器人策略变了不该让抓字脚本整个挂掉
        print(f"  （第 {g.level_index + 1} 关驱动失败：{exc}）", file=sys.stderr)
    g.debug.enabled = False
    g.show_map = True
    _frame(g, n=3)
    g.show_map = False
    # 过关过场（不推进，只画）
    g._next_timer = 1.0
    g._render()
    g._next_timer = 0.0
    # 胜利 / 失败两张结束页
    g.world.state = C.STATE_VICTORY
    g.show_review = False
    g._render()
    g.show_review = True
    g._render()
    g.world.state = C.STATE_DEFEAT
    g.show_review = False
    g._render()
    g.show_review = True
    g._render()


def run_all(record: set) -> None:
    """跑遍所有界面。每一句上屏文本都会进 `record`。"""
    from sneak_ai.__main__ import Game
    from sneak_ai.core import settings

    # Game.__init__ 会按存档里的语言设一次；抓字时以脚本设定的为准。
    # **必须还回去**：这是模块级属性，改了不还原，后面所有读语言的地方
    # （包括测试自己）都会拿到抓字脚本的假值。
    original = settings.load_lang
    settings.load_lang = lambda: i18n.lang()
    i18n.RECORD = record
    try:
        g = Game()
        _menu(g)
        i18n.RECORD = record
        for idx in range(len(L.LEVELS)):
            g = Game(start_level=idx)
            i18n.RECORD = record
            _level(g)
    finally:
        settings.load_lang = original
        i18n.RECORD = None


def collect(lang: str) -> set:
    seen: set = set()
    i18n.set_lang(lang)
    run_all(seen)
    i18n.set_lang(i18n.ZH)
    return seen


def main(argv: list[str]) -> int:
    pygame.init()
    seen = collect(i18n.ZH)
    missing = sorted(s for s in collect(i18n.EN) if has_cjk(s))
    print(f"中文模式下上屏文本 {len(seen)} 条（去重）")
    if missing:
        print(f"英文模式下仍有 {len(missing)} 条含中文：")
        for s in missing:
            print("   ", repr(s))
    else:
        print("英文模式下没有中文漏出来")

    if "--dump" in argv:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "i18n_strings.txt")
        with open(path, "w", encoding="utf-8") as f:
            for s in sorted(seen):
                f.write(repr(s) + "\n")
        print("清单写到", path)
    pygame.quit()
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
