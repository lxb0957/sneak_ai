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


def take_all(game):
    """把本关**所有**数据节点摸一遍。

    第 3 关有两个节点，只摸第一个不会进入撤离阶段 —— 所以凡是"我要拿到数据"
    的地方都走这个函数，而不是写死 `w.terminal`。
    """
    for _i, pt in list(game.world.remaining()):
        teleport(game, *pt)
        step(game, DT * 2)


def step(game, seconds):
    for _ in range(int(round(seconds / DT))):
        game.step(DT)


# ---------------------------------------------------------------- 用例
def test_clear_card_advances_to_next_level():
    """踩撤离点 → 过场 1.6 秒 → 自动进入第 2 关。"""
    g = fresh_game()
    w = g.world
    check("开局在第 1 关", g.level_index == 0 and w.level_name == L.LEVELS[0].name)

    take_all(g)
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
          (g.world.terminals, g.world.exfil) == (L.LEVELS[1].terminals,
                                                L.LEVELS[1].exfil))
    check("结算标记已复位（否则第 2 关一胜就静默跳过结算）", not g._scored)


def test_card_freezes_world():
    """过场期间世界冻结：敌人不动、用时也不涨。"""
    g = fresh_game()
    w = g.world
    take_all(g)
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
    take_all(g)
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
    #
    # 注意这里要**先把这一局打到最后一关**：关卡数是数据决定的（现在 3 关），
    # 写死"第 2 关就是末关"的断言会在加关卡那天变红 —— 上一版正是这么挂的。
    while g.level_index < len(L.LEVELS) - 1:
        g.world.state = C.STATE_VICTORY
        step(g, 2.0)
    check("连打穿过中间关", g.level_index == len(L.LEVELS) - 1,
          f"共 {len(L.LEVELS)} 关")
    check("每次过场后都回到可玩状态", g.world.state == C.STATE_INFILTRATE)

    # 存档是玩家数据，测试不许写它 —— 把提交接口换成空实现
    from sneak_ai.core import records
    real_submit = records.submit
    records.submit = lambda rec: False
    try:
        g.world.state = C.STATE_VICTORY
        step(g, 3.0)
    finally:
        records.submit = real_submit
    check("末关胜利停在结算页", g.level_index == len(L.LEVELS) - 1 and
          g._next_timer == 0 and g.world.state == C.STATE_VICTORY)


def test_defeat_retry_keeps_time():
    """失败重来：回到本关开头，但已耗时间留着（罚时间不罚进度）。"""
    g = fresh_game()
    take_all(g)
    step(g, DT * 2)
    step(g, 1.0)
    spent = g.world.elapsed
    g.world.state = C.STATE_DEFEAT
    g._retry_level()
    check("重来后仍在第 1 关", g.level_index == 0)
    check("重来后世界回到潜入阶段", g.world.state == C.STATE_INFILTRATE)
    check("已耗时间被罚掉（不是清零）", g.world.elapsed >= spent,
          f"{spent:.2f}s → {g.world.elapsed:.2f}s")


def test_start_level_switch():
    """`--level N` 只改**起点**：开局就在那关，重开一局也回那关，且不许越界。"""
    last = len(L.LEVELS) - 1
    g = Game(start_level=last)
    check("从指定关开局", g.level_index == last and
          g.world.level_name == L.LEVELS[last].name, f"{g.world.level_name}")
    g._restart_run()
    check("重开一局回到起点关，而不是被踢回第 1 关", g.level_index == last)

    g0 = Game(start_level=0)
    g0._restart_run()
    check("默认起点仍是第 1 关", g0.level_index == 0)

    gbig = Game(start_level=99)
    check("越界的起点被夹到最后一关", gbig.level_index == last)

    # 中途开局的这一局不许写最佳纪录（否则一条 35 秒的"三关"成绩会污染纪录表）
    from sneak_ai.core import records
    calls = []
    real_submit = records.submit
    records.submit = lambda rec: calls.append(rec) or False
    try:
        g2 = Game(start_level=last)
        g2._carry = {}
        take_all(g2)
        teleport(g2, *g2.world.exfil)
        step(g2, 3.0)
    finally:
        records.submit = real_submit
    check("--level 起局的那一局不写纪录", calls == [] and
          g2.world.state == C.STATE_VICTORY)


def main():
    print("W18 主循环 / 过关切关")
    test_clear_card_advances_to_next_level()
    test_card_freezes_world()
    test_no_double_advance_and_carry()
    test_defeat_retry_keeps_time()
    test_start_level_switch()
    print(f"\n全部通过（{len(PASSED)} 项断言）。")
    pygame.quit()


if __name__ == "__main__":
    main()
