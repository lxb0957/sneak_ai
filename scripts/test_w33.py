"""W33：新手引导 —— 该点的键要在该说的时候说出来，并且只说一次。

## 为什么需要这个

这一版的玩法教学全靠"走到哪教到哪"：走近补给点才说它能干嘛，站到总闸前
才说按 E。这条原则没问题，问题是**它只覆盖了捡到的东西**，没覆盖"这一关
需要你想起来做的那件事"：

- 第 4 关要玩家自己想到"去把灯关掉"。字幕里的"走不动的时候，去把灯关掉"
  是设计意图，不是操作说明 —— 第一次来的人读完仍然不知道按什么；
- 第 5 关曾经踩过同一个坑（开场不点名 Q，玩家在第一道 1 格闸前站到超时，
  以为关坏了），后来补了一句。第 4 关当时没补上；
- 开枪、蹲下躲视线、体力耗尽这些**通用**动词，游戏里从来没有说过 ——
  它们只写在菜单的三行小字里，而菜单正是玩家按得最快的那一屏。

## 这组测试守四件事

1. 每一关的开场句都点名"这一关的新动词"（E / Q / 两个舱），并且短到能单行显示；
2. 一次性提示按条件触发：被看见、体力见底、拿到第一份数据还没拿完；
3. 说过的话进存档，第二局不再说（同一个提示每局都弹，等于教玩家无视它）；
4. 与 Game 的接线是真的：开局第一帧屏幕上出现的确实是 tutorial 写的那句。

用法：
    python scripts/test_w33.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core import records, settings, tutorial  # noqa: E402


class _Tmp:
    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sneak_w33_"))
        self.old = (settings.DATA_DIR, settings.SETTINGS_FILE,
                    records.DATA_DIR, records.RECORD_FILE)
        settings.DATA_DIR, settings.SETTINGS_FILE = self.dir, None
        records.DATA_DIR, records.RECORD_FILE = self.dir, None
        return self

    def __exit__(self, *exc):
        (settings.DATA_DIR, settings.SETTINGS_FILE,
         records.DATA_DIR, records.RECORD_FILE) = self.old
        return False


class _W:
    """只带 once_tips 要读的字段。"""

    def __init__(self, **kw):
        self.spotted = 0
        self.has_data = False
        self.taken = []
        self.terminals = (C.DATA_TERMINAL,)
        for k, v in kw.items():
            setattr(self, k, v)

    def remaining(self):
        return [(i, t) for i, t in enumerate(self.terminals) if i not in self.taken]


class _P:
    def __init__(self, stamina=C.STAMINA_MAX):
        self.stamina = stamina


# ---------------------------------------------------------------- 1. 开场句
def test_intro_names_the_new_verb():
    """每关的开场句必须点名一个新东西，而且短到单行放得下。"""
    for lv in L.LEVELS:
        msg = tutorial.intro_for(lv)
        assert msg, f"第 {lv.idx + 1} 关没有开场句"
        assert len(msg) <= 56, (
            f"第 {lv.idx + 1} 关开场句 {len(msg)} 字，"
            f"HUD 底部是单行 16px，1024 宽的窗口会画出去：{msg!r}")

    assert "终端" in tutorial.intro_for(L.LEVELS[0]), "第 1 关没说去哪"
    # 第 3 关：不说"两个舱"，玩家摸完第一个会以为该撤了
    assert "两个舱" in tutorial.intro_for(L.LEVELS[2])
    # 第 4 关：总闸是这一关唯一的解法，必须点名 E
    assert "E" in tutorial.intro_for(L.LEVELS[3]) and \
           "总闸" in tutorial.intro_for(L.LEVELS[3])
    # 第 5 关：门里的岗不会走开，必须点名 Q
    assert "Q" in tutorial.intro_for(L.LEVELS[4])
    print(f"[OK] 五关开场句各点名一个新动词，最长 "
          f"{max(len(tutorial.intro_for(lv)) for lv in L.LEVELS)} 字")


def test_intro_only_teaches_what_is_new():
    """没有总闸的关卡不许提 E —— 说了玩家会去找一个不存在的东西。"""
    for lv in L.LEVELS:
        msg = tutorial.intro_for(lv)
        if not lv.power:
            assert "E " not in msg and "拉闸" not in msg, (lv.name, msg)
        if not lv.posts:
            assert "Q" not in msg, (lv.name, msg)
    print("[OK] 没有总闸/固定岗的关卡不提 E / Q")


# ---------------------------------------------------------------- 2. 条件
def test_once_tips_fire_on_condition():
    """三个一次性提示各自的条件，以及"没到时候不说话"。"""
    # 什么都没发生：一条都不该有
    assert tutorial.once_tips(_W(), _P(), set()) == []

    # 被看见
    got = tutorial.once_tips(_W(spotted=1), _P(), set())
    assert [k for k, _ in got] == [tutorial.SPOTTED], got

    # 体力见底
    got = tutorial.once_tips(_W(), _P(stamina=0.0), set())
    assert [k for k, _ in got] == [tutorial.STAMINA], got

    # 拿到第一份数据、但还有节点没摸（第 3 关）：该提醒"警报响了，继续走"
    w = _W(has_data=True, terminals=("a", "b"), taken=[0])
    got = tutorial.once_tips(w, _P(), set())
    assert [k for k, _ in got] == [tutorial.OBJ_DONE], got

    # 只有一个节点、已经拿完（第 1 关）：那不是"还没拿完"，不该弹
    w1 = _W(has_data=True, terminals=("a",), taken=[0])
    assert tutorial.once_tips(w1, _P(), set()) == []

    # 已经说过：全部闭嘴
    taught = {tutorial.SPOTTED, tutorial.STAMINA, tutorial.OBJ_DONE}
    assert tutorial.once_tips(_W(spotted=3), _P(stamina=0.0), taught) == []
    print("[OK] 三条一次性提示各按条件触发，说过就不再提")


# ---------------------------------------------------------------- 3. 存档
def test_taught_persists_without_clobbering_other_prefs():
    with _Tmp():
        settings.save(muted=True, assist=True)
        settings.mark_taught(tutorial.SPOTTED)
        settings.mark_taught(tutorial.STAMINA)

        assert settings.load_taught() == {tutorial.SPOTTED, tutorial.STAMINA}
        # 记一条提示不能把静音/辅助踩掉（同一个文件，按键合并）
        assert settings.load_muted() is True
        assert settings.load_assist() is True

        # 重复记不重复（集合语义），坏数据当没说过
        settings.mark_taught(tutorial.SPOTTED)
        assert len(settings.load_taught()) == 2
        settings.path().write_text('{"taught": "spotted"}', encoding="utf-8")
        assert settings.load_taught() == set()
    print("[OK] taught 落盘与静音/辅助共存，重复记不重复，坏数据不炸")


# ---------------------------------------------------------------- 4. 接线
def _game(level: int):
    from sneak_ai.__main__ import Game

    g = Game(start_level=level)
    g.menu.sel = level
    g.menu.assist = False
    g._on_menu_start()
    return g


def test_game_shows_the_intro_on_first_frame():
    with _Tmp():
        g = _game(3)                      # 第 4 关
        assert g._show_intro is True
        g._update_tips(1 / 60)
        tip = g.hud._tip
        assert tip == tutorial.intro_for(L.LEVELS[3]), tip
        assert "E" in tip, f"第 4 关开局没说 E：{tip!r}"
        assert g._show_intro is False, "开场句没有一次性"
    print("[OK] 第 4 关开局第一帧贴出 tutorial 写的那句（含 E）")


def test_game_teaches_once_then_stops():
    with _Tmp():
        g = _game(0)
        g._show_intro = False
        g.world.spotted = 1
        g._update_tips(1 / 60)
        assert g.hud._tip == tutorial.TEXT[tutorial.SPOTTED], g.hud._tip
        assert tutorial.SPOTTED in g._taught
        assert tutorial.SPOTTED in settings.load_taught(), "没写进存档"

        # 下一局同一条件再来：不再弹
        g._restart_run()
        g._show_intro = False
        g.hud._tip = ""
        g.world.spotted = 2
        g._update_tips(1 / 60)
        assert g.hud._tip == "", f"第二次还在说：{g.hud._tip!r}"
    print("[OK] 一次性提示说过就进存档，下一局不再弹")


def main() -> int:
    test_intro_names_the_new_verb()
    test_intro_only_teaches_what_is_new()
    test_once_tips_fire_on_condition()
    test_taught_persists_without_clobbering_other_prefs()
    test_game_shows_the_intro_on_first_frame()
    test_game_teaches_once_then_stops()
    print("\nW33 全部通过：该点的键在该说的时候说了，而且只说一次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
