"""W27：共享信念 → 分工搜索。

报警一直只传"有人在哪儿"，于是被喊醒的三个哨兵各自从同一个点出发、
各自用同一份藏身点候选，排出来的搜查计划几乎一模一样 —— 三份叠在同一片区域，
另外三面没人。这一层补的是两件事：

1. **情报带方向**：目击→报警这条链上多传一个"他往哪跑"，否则"扇形"无从谈起。
2. **搜索位分工**：跟丢之后由 World 把几个哨兵的落点沿方向铺开，
   一个人去信念点，其余去两侧和后方。

这个文件钉的是"分工确实发生了、而且不会退化成抖动"：

- 广播必须把位置和方向一起送到（只送位置 = 三个人搜同一格）
- 转述不能覆盖"我自己看见过"的记忆（否则会跟着谣言走）
- 扇形点互不相同、都可走、方向随 heading 翻转
- 一个人不值得分工；看得见人的不给搜索位
- 信念点没挪就不该重新分派（第一版每帧重分，一局刷出 1673 条“搜索位 →”）
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402


def _game(level: int = 0):
    g = Game(start_level=level)
    g.menu.active = False
    return g


def _walkable_cells(world, n=1):
    g = world.grid
    out = []
    for y in range(2, g.h - 2):
        for x in range(2, g.w - 2):
            if g.is_walkable(x, y) and not g.is_walkable(x + 1, y) and g.is_walkable(x, y + 1):
                out.append((x + 0.5, y + 0.5))
                if len(out) >= n:
                    return out
    return out


def test_broadcast_carries_position_and_heading():
    """目击者喊一声：附近的队友拿到的是"位置 + 方向"，不是一句"出事了"。"""
    g = _game()
    w, p = g.world, g.player
    p.x, p.y = _walkable_cells(w)[0]
    w.last_known = (p.x, p.y)
    w.last_known_age = 0.0
    w.belief_heading = (0.0, 1.0)

    witness = w.enemies[0]
    witness.sees_player = True
    witness.suspicion = 0.8
    near = w.enemies[1]
    near.x, near.y = witness.x, witness.y + 4.0     # 在 RADIO_RANGE 内
    near.suspicion = 0.0
    near._last_seen = None
    near._belief_heading = None
    w._alarm_cd = 0.0
    w._maybe_broadcast()

    assert near.suspicion >= C.SUSPICION_CURIOUS, "被喊到的队友没有起疑"
    assert near._last_seen is not None, "只喊了'出事了'，没给位置"
    assert math.hypot(near._last_seen[0] - p.x, near._last_seen[1] - p.y) < 1e-6, (
        f"传过去的位置不对：{near._last_seen} vs ({p.x},{p.y})")
    assert near._belief_heading == (0.0, 1.0), (
        f"方向没传过去：{near._belief_heading}（没有它，扇形不知道该往哪开）")
    print("[OK] 报警带方向：队友拿到位置 + 朝向")


def test_own_eyes_beat_rumor():
    """自己看见过的记忆，不会被队友的转述覆盖。"""
    g = _game()
    e = g.world.enemies[0]
    e._last_seen = (3.0, 3.0)
    e._seen_this_episode = True
    e.note_belief((9.0, 9.0), (1.0, 0.0))
    assert e._last_seen == (3.0, 3.0), f"谣言盖过了亲眼所见：{e._last_seen}"

    # 反过来：只有"听到"级别的记忆（没真看见）就该被更准的情报替换
    e2 = g.world.enemies[0]
    e2._last_seen = (3.0, 3.0)
    e2._seen_this_episode = False
    e2.note_belief((9.0, 9.0), (0.0, 1.0))
    assert e2._last_seen == (9.0, 9.0), "更准的情报没有替换掉自己听到的猜测"
    print("[OK] 转述不覆盖亲眼所见，但会替换自己的听觉猜测")


def test_fan_spreads_and_follows_heading():
    """扇形：点互不相同、都可走、方向跟着 heading 翻。"""
    g = _game()
    w = g.world
    anchor = _walkable_cells(w)[0]
    a = w._fan_slots(anchor, (1.0, 0.0), 4)
    assert len(a) == 4, f"要 4 个搜索位，只给了 {len(a)} 个"
    assert len(set(a)) == 4, f"搜索位有重复：{a}"
    for tx, ty in a:
        assert w.grid.is_walkable(tx, ty), f"搜索位落在墙里：{(tx, ty)}"
        d = math.hypot(tx - anchor[0], ty - anchor[1])
        assert d <= C.BELIEF_FAN_RADIUS + 6, f"搜索位离信念点太远：{d:.1f} 格"

    b = w._fan_slots(anchor, (-1.0, 0.0), 4)
    mx_a = sum(t[0] for t in a[1:]) / max(1, len(a) - 1)
    mx_b = sum(t[0] for t in b[1:]) / max(1, len(b) - 1)
    assert mx_a > mx_b + 1.0, (
        f"方向反了扇形没跟着翻：向东展开重心 {mx_a:.1f}，向西 {mx_b:.1f}")
    print(f"[OK] 扇形：4 个互不相同的可走点，朝东 {mx_a:.0f} / 朝西 {mx_b:.0f}")


def _prep_searchers(w, n: int):
    """把前 n 个哨兵摆成"正在搜"的状态，其余清干净。"""
    w.last_known = _walkable_cells(w)[0]
    w.last_known_age = 0.0
    w.belief_heading = (1.0, 0.0)
    w._slot_anchor = None
    w._slot_cd = 0.0
    for i, e in enumerate(w.enemies):
        e.searching = i < n
        e.sees_player = False
        e.search_slot = None
    return w.last_known


def test_single_searcher_gets_no_slot():
    """只有一个人搜的时候没有分工可分 —— 分给他一个"搜索位"只会让他多跑一趟。"""
    g = _game()
    w = g.world
    _prep_searchers(w, 1)
    w._assign_search_slots(1 / 60)
    assert w.enemies[0].search_slot is None, "一个人也被指派了搜索位"
    print("[OK] 只有一个人搜时不分工")


def test_seers_are_excluded():
    """看得见人的哨兵不该拿到"猜他去哪"的搜索位。"""
    g = _game()
    w = g.world
    _prep_searchers(w, 3)
    w.enemies[0].sees_player = True          # 眼睛盯着人
    w._assign_search_slots(1 / 60)
    assert w.enemies[0].search_slot is None, "看得见人还给他分了个搜索位"
    assert w.enemies[1].search_slot is not None, "没看见人的反而没分到"
    print("[OK] 看得见人的不分搜索位")


def test_slots_are_stable_until_belief_moves():
    """分工不是每帧投票：信念点没挪就不该重新分派。"""
    g = _game()
    w = g.world
    _prep_searchers(w, 3)
    before = len(w.trace.rows)
    w._assign_search_slots(1 / 60)
    first = [e.search_slot for e in w.enemies[:3]]
    assert all(s is not None for s in first), f"有人没分到：{first}"
    n_after_first = len(w.trace.rows) - before

    for _ in range(60):                       # 一秒之内反复跑
        w._assign_search_slots(1 / 60)
    assert [e.search_slot for e in w.enemies[:3]] == first, (
        f"信念点没动，搜索位却变了：{first} → {[e.search_slot for e in w.enemies[:3]]}")
    extra = len(w.trace.rows) - before - n_after_first
    assert extra == 0, f"没有重铺却记了 {extra} 条分派日志（面板会被刷屏）"

    # 信念点挪了 5 格：该重铺
    w.last_known = (w.last_known[0] + 5.0, w.last_known[1])
    w._slot_cd = 0.0
    w._assign_search_slots(1 / 60)
    assert any(r.kind == "role" and "搜索位" in r.text
               for r in list(w.trace.rows)[before + n_after_first:]), (
        "信念点挪了 5 格却没有重铺扇形")
    print(f"[OK] 分工稳定：1 秒 60 帧只分派一次，移动 5 格后才重铺")


def test_fixed_posts_do_not_take_intel():
    """固定岗不接转述、也不拿搜索位 —— 第 5 关的动词靠这一条活着。

    这一条是**实测逼出来的**：不加这个限制时，96 局里第 5 关从 51.0% 掉到 32.3%
    （区间 [24,42] 与改动前的 [41,61] 不重叠）。原因不是"AI 变强了"这么含糊，
    而是那一关的语法是"门里有个人，你只能骗他" —— 一旦他能接队友的坐标，
    他就自己走开了，五道闸变成五条空走廊，关卡也就不存在了。
    """
    g = _game(4)                                  # 第 5 关：5 固定岗 + 4 流动哨
    w = g.world
    posts = [e for e in w.enemies if e.is_fixed_post]
    rovers = [e for e in w.enemies if not e.is_fixed_post]
    assert len(posts) == 5, f"第 5 关应该有 5 个固定岗，识别到 {len(posts)} 个"

    p = posts[0]
    p._last_seen = None
    p._seen_this_episode = False
    p.note_belief((30.0, 18.0), (1.0, 0.0))
    assert p._last_seen is None, "固定岗接了队友的转述 —— 他会自己离开那道门"

    # 搜索位同样不该派给他：派了就会每帧 `_plan_is_stale`，计划空转
    for e in w.enemies:
        e.searching = True
        e.sees_player = False
        e.search_slot = None
    w.last_known = (30.0, 18.0)
    w.last_known_age = 0.0
    w.belief_heading = (1.0, 0.0)
    w._slot_anchor = None
    w._slot_cd = 0.0
    w._assign_search_slots(1 / 60)
    assert all(e.search_slot is None for e in posts), (
        f"固定岗拿到了搜索位：{[e.search_slot for e in posts]}")
    assert any(e.search_slot is not None for e in rovers), "流动哨反而一个都没分到"
    print("[OK] 固定岗不接转述、不拿搜索位（第 5 关的门还得靠骗）")


def main():
    print("=== W27：共享信念 → 分工搜索 ===")
    test_broadcast_carries_position_and_heading()
    test_own_eyes_beat_rumor()
    test_fan_spreads_and_follows_heading()
    test_single_searcher_gets_no_slot()
    test_seers_are_excluded()
    test_slots_are_stable_until_belief_moves()
    test_fixed_posts_do_not_take_intel()
    print("\nW27 全部通过：情报带方向，搜索会分工，且不会每帧改派。")


if __name__ == "__main__":
    main()
