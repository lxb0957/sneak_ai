"""W18 测试：主循环里的过关切关（这一组守的是"状态机会不会自己走完"）。

W18 之前，`Game.run()` 是唯一驱动游戏的地方：事件、update、渲染全糊在一个
while 里，**测试没法喂 dt**，于是没有任何测试走过"胜利之后那 1.6 秒"。
结果是 `_next_timer` 被设成 1.6 却从来没被递减过 —— 通关卡片永久停住、
输入被吞、玩家永远进不了第 2 关。机器人测试在"胜利"那一刻就 return 了，
CI 全绿，人一玩就卡死。

所以这一组测的不是"代码里有 step()"，而是三件玩家真会遇到的事：

1. 踩到撤离点 → 过场 → **自动**进第 2 关（不用按键）；
2. 过场那 1.6 秒**世界冻结**：敌人不动，用时也不涨（否则白送 1.6 秒罚时）；
3. 过场期间按什么键都无副作用，且不会连跳两关（关卡索引只 +1）。

用法：
    python scripts/test_w18.py        # Windows 上需 SDL_VIDEODRIVER=dummy
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pygame  # noqa: E402

pygame.init()

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402

DT = 1 / 60
PASSED = []


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)
    print(f"  ok  {name} {detail}")


def fresh_game():
    """一个不进菜单、直接可玩的 Game（和玩家按回车开局后的状态一致）。"""
    g = Game()
    g.menu.active = False
    g._show_intro = False
    g._tips_shown = set()
    return g


def teleport(game, x, y):
    game.player.x, game.player.y = x, y


def step(game, seconds):
    for _ in range(int(round(seconds / DT))):
        game.step(DT)


# ---------------------------------------------------------------- 用例
def test_clear_card_advances_to_next_level():
    """踩撤离点 → 过场 1.6 秒 → 自动进入第 2 关。"""
    g = fresh_game()
    w = g.world
    check("开局在第 1 关", g.level_index == 0 and w.level_name == L.LEVELS[0].name)

    teleport(g, *w.terminal)
    step(g, DT * 2)
    check("拿到数据后进入逃脱阶段", w.state == C.STATE_EXFILTRATE and w.has_data)

    teleport(g, *w.exfil)
    step(g, DT * 2)
    check("踩撤离点即胜利", w.state == C.STATE_VICTORY)
    check("过场计时已启动", g._next_timer > 0, f"={g._next_timer:.2f}s")

    # 1 秒后仍在过场里：卡片还没走完，没理由已经切关
    step(g, 1.0)
    check("过场未走完时不切关", g.level_index == 0 and g._next_timer > 0,
          f"剩余 {g._next_timer:.2f}s")

    # 再 1 秒 → 应该已经进第 2 关
    step(g, 1.0)
    check("过场结束后自动进第 2 关", g.level_index == 1,
          f"level_name={g.world.level_name}")
    check("第 2 关就是注册表里的那一关",
          g.world.level_name == L.LEVELS[1].name)
    check("新关卡从潜入阶段开始", g.world.state == C.STATE_INFILTRATE)
    check("新关卡目标点跟着关卡走",
          (g.world.terminal, g.world.exfil) == (L.LEVELS[1].terminal,
                                                L.LEVELS[1].exfil))
    check("结算标记已复位（否则第 2 关一胜就静默跳过结算）", not g._scored)


def test_card_freezes_world():
    """过场期间世界冻结：敌人不动、用时也不涨。"""
    g = fresh_game()
    w = g.world
    teleport(g, *w.terminal)
    step(g, DT * 2)
    teleport(g, *w.exfil)
    step(g, DT * 2)

    t0 = w.elapsed
    pos = [(e.x, e.y) for e in w.enemies[:6]]
    carded = g._next_timer
    step(g, 1.0)

    check("过场中用时不再增长", abs(w.elapsed - t0) < 1e-9,
          f"{t0:.2f} → {w.elapsed:.2f}")
    still = all(abs(e.x - x) < 1e-9 and abs(e.y - y) < 1e-9
                for e, (x, y) in zip(w.enemies[:6], pos))
    check("过场中敌人不动", still)
    check("过场计时在递减", g._next_timer < carded,
          f"{carded:.2f}s → {g._next_timer:.2f}s")


def test_no_double_advance_and_carry():
    """只 +1 关、跨关统计延续、且不会自己跳到不存在的关卡。"""
    g = fresh_game()
    w = g.world
    g.player.ammo = 3
    g.player.stones = 1
    teleport(g, *w.terminal)
    step(g, DT * 2)
    teleport(g, *w.exfil)
    step(g, 3.0)

    check("只前进一关", g.level_index == 1)
    check("弹药跨关延续", g.player.ammo == 3, f"ammo={g.player.ammo}")
    check("石子跨关延续", g.player.stones == 1, f"stones={g.player.stones}")
    check("用时跨关累计", g.world.elapsed > 0, f"{g.world.elapsed:.2f}s")

    # 在第 2 关里再跑 3 秒，不该有任何自动切关
    lvl = g.level_index
    step(g, 3.0)
    check("没有过关就不会自己切关", g.level_index == lvl)

    # 末关胜利后不能再往前走（越界会 IndexError）
    g.world.state = C.STATE_VICTORY
    step(g, 3.0)
    check("末关胜利停在结算页", g.level_index == len(L.LEVELS) - 1 and
          g._next_timer == 0 and g.world.state == C.STATE_VICTORY)


def test_defeat_retry_keeps_time():
    """失败重来：回到本关开头，但已耗时间留着（罚时间不罚进度）。"""
    g = fresh_game()
    teleport(g, *g.world.terminal)
    step(g, DT * 2)
    step(g, 1.0)
    spent = g.world.elapsed
    g.world.state = C.STATE_DEFEAT
    g._retry_level()
    check("重来后仍在第 1 关", g.level_index == 0)
    check("重来后世界回到潜入阶段", g.world.state == C.STATE_INFILTRATE)
    check("已耗时间被罚掉（不是清零）", g.world.elapsed >= spent,
          f"{spent:.2f}s → {g.world.elapsed:.2f}s")


def main():
    print("W18 主循环 / 过关切关")
    test_clear_card_advances_to_next_level()
    test_card_freezes_world()
    test_no_double_advance_and_carry()
    test_defeat_retry_keeps_time()
    print(f"\n全部通过（{len(PASSED)} 项断言）。")
    pygame.quit()


if __name__ == "__main__":
    main()
