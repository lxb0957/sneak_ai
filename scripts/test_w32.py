"""W32：辅助模式 —— 能玩完，但成绩不算数。

## 为什么需要这个

第 4 关机器人胜率 25%（95% 区间 17~35%），是全作唯一的断崖：
4.5 的追兵比玩家走路（4.4）还快，被看见就等于死。有人卡三四十局之后
就不玩了 —— 而这一版真正想让人看到的是第 5 关。

辅助模式调三个旋钮：视野 ×0.82、追兵 ×0.88、封锁 ×0.60。三个都只动
"敌人的能力"，不碰玩家的速度/体力/弹药 —— 关掉辅助不需要重新学走路。

## 这组测试守五件事

1. **偏好按键合并**：开辅助不能顺手把玩家关掉的音效打开（settings.json
   里已经不止一个键了，整份覆盖是会静默丢偏好的）；
2. **三个旋钮真的落到敌人身上**，而且第 4 关的追兵 4.5 × 0.88 = 3.96 < 4.4，
   也就是"被看见之后真的跑得掉"—— 这是这个模式存在的理由；
3. **关掉之后一个数都不差**：辅助不是"减一点再减回来"，是原样；
4. **辅助局不写任何纪录**（整局榜 + 单关榜），但复盘照记 ——
   排名和"我自己看"是两件事；
5. **玩家看得见**：菜单、HUD、结算页三处都写着"不算纪录"。
   只在代码里不计、界面上不说，等于让玩家以为游戏吞了他的成绩。

用法：
    python scripts/test_w32.py
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
from sneak_ai.core import records, settings  # noqa: E402


class _Tmp:
    """把存档和设置都指到临时目录：不碰玩家真实的 records/settings。"""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sneak_w32_"))
        self.old = (settings.DATA_DIR, settings.SETTINGS_FILE,
                    records.DATA_DIR, records.RECORD_FILE)
        settings.DATA_DIR, settings.SETTINGS_FILE = self.dir, None
        records.DATA_DIR, records.RECORD_FILE = self.dir, None
        return self

    def __exit__(self, *exc):
        (settings.DATA_DIR, settings.SETTINGS_FILE,
         records.DATA_DIR, records.RECORD_FILE) = self.old
        return False


def _game(level: int = 0, assist: bool = False):
    from sneak_ai.__main__ import Game

    g = Game(start_level=level)
    g.menu.sel = level
    g.menu.assist = assist
    g._on_menu_start()
    return g


# ---------------------------------------------------------------- 1. 偏好
def test_prefs_merge_instead_of_overwrite():
    """先关音效、再开辅助，音效必须还是关着的。"""
    with _Tmp():
        # 用 True 当"被踩掉"的标记：False 是缺省值，丢了也读成 False，
        # 那样这条断言会在整份覆盖下**仍然通过** —— 假绿。
        settings.save(True)                  # 旧调用形式：位置参数 = muted
        settings.save(assist=True)
        assert settings.load_muted() is True, "开辅助把音效开关踩掉了"
        assert settings.load_assist() is True

        settings.save(assist=False)
        assert settings.load_muted() is True, "关辅助又顺手改了音效"
        assert settings.load_assist() is False

        # 漏传参数不该清空存档
        settings.save()
        assert settings.load_assist() is False
    print("[OK] 偏好按键合并：开/关辅助都不动音效开关")


# ---------------------------------------------------------------- 2. 生效
def test_assist_scales_only_enemy_abilities():
    """三个系数分别落在视野、追兵、封锁上，玩家侧一个都不动。"""
    with _Tmp():
        g = _game(0, assist=True)
        lv = L.LEVELS[0]
        e = g.world.enemies[0]

        assert abs(g.world.view_mul - C.ASSIST_VIEW) < 1e-9, g.world.view_mul
        assert abs(e.speed_chase - lv.chase * C.ASSIST_CHASE) < 1e-9, e.speed_chase
        assert abs(e.alert_view - lv.alert_view * C.ASSIST_VIEW) < 1e-9, e.alert_view
        assert abs(g.world.alert_duration - lv.alert_duration * C.ASSIST_ALERT) < 1e-9

        # 视野系数必须同时进"判定"和"绘制"：vis_scale 由 World 每帧下发，
        # 黑灯时再乘一次 —— 两个系数是同一个入口，不能各走各的。
        g.world.update(0.0)
        assert abs(e.vis_scale - C.ASSIST_VIEW) < 1e-9, e.vis_scale
        g.world.blackout_left = 1.0
        g.world.update(0.0)
        assert abs(e.vis_scale - C.ASSIST_VIEW * g.world.blackout_view) < 1e-9

        # 玩家侧：三个速度、体力、弹药都是原值 —— 辅助不碰手感
        from sneak_ai.entity.player import Player

        p, fresh = g.player, Player(0.0, 0.0)
        assert (p.speed_walk, p.speed_sneak, p.speed_sprint) == (
            fresh.speed_walk, fresh.speed_sneak, fresh.speed_sprint)
        assert abs(p.stamina - fresh.stamina) < 1e-9
        assert (p.ammo, p.stones) == (fresh.ammo, fresh.stones), (p.ammo, p.stones)
    print("[OK] 辅助只削敌人：视野 / 追兵 / 封锁，玩家侧原样")


def test_assist_makes_level4_chase_runnable():
    """第 4 关：追兵 4.5 > 走路 4.4（原来跑不掉），辅助后 3.96 < 4.4。"""
    with _Tmp():
        lv = L.LEVELS[3]
        assert lv.chase > C.PLAYER_WALK_SPEED, \
            f"第 4 关追兵 {lv.chase} 应该比走路快，否则这个测试没有意义"
        g = _game(3, assist=True)
        e = g.world.enemies[0]
        assert e.speed_chase < C.PLAYER_WALK_SPEED, e.speed_chase
    print(f"[OK] 第 4 关追兵 {lv.chase} → {C.ASSIST_CHASE * lv.chase:.2f}"
          f"（走路 {C.PLAYER_WALK_SPEED}），被看见之后跑得掉了")


def test_assist_off_is_exactly_vanilla():
    """关掉辅助，所有参数回到关卡原值 —— 不是"差不多"。"""
    with _Tmp():
        g = _game(0, assist=False)
        lv = L.LEVELS[0]
        e = g.world.enemies[0]
        assert g.world.view_mul == 1.0 and not g.world.assist
        assert e.speed_chase == lv.chase, e.speed_chase
        assert e.alert_view == lv.alert_view, e.alert_view
        assert g.world.alert_duration == lv.alert_duration
    print("[OK] 关掉辅助：视野/追兵/封锁全部回到关卡原值")


# ---------------------------------------------------------------- 3. 纪录
def _win(g) -> None:
    g.level_index = len(L.LEVELS) - 1
    g._new_world()
    g.world.state = C.STATE_VICTORY
    g.world.elapsed = 100.0
    g._handle_scoring()


def test_assist_run_writes_no_records():
    """辅助局：整局榜和单关榜都不写，但复盘那一行照记。"""
    with _Tmp():
        g = _game(4, assist=True)
        _win(g)
        assert not records.load().get("score"), \
            f"辅助局写进了整局纪录：{records.load()}"
        assert not records.load().get(records.PER_LEVEL_KEY), \
            "辅助局写进了单关纪录"
        assert len(g.level_log) == 1, "复盘那一行不该被吞掉"
    print("[OK] 辅助局不写纪录（整局榜 + 单关榜），复盘照记")


def test_normal_run_still_writes_records():
    """对照组：同样是通关，不开辅助就必须写进去，否则上面那条测不出东西。"""
    with _Tmp():
        g = _game(0, assist=False)
        _win(g)
        best = records.load()
        assert best.get("score"), f"正常局没有写纪录：{best}"
        assert best.get("levels"), best
    print("[OK] 对照组：正常局照常写纪录")


# ---------------------------------------------------------------- 4. 可见
def test_player_can_see_assist_is_on():
    """菜单底栏、HUD 目标行、结算页三处都要写出来。"""
    from sneak_ai.core.hud import HUD, END_DESIGN

    with _Tmp():
        g = _game(0, assist=True)
        # 菜单
        assert "A 辅助" in g.menu.foot_text(False), g.menu.foot_text(False)
        assert "不计纪录" in g.menu.foot_text(False) or \
               "不记纪录" in g.menu.foot_text(False), g.menu.foot_text(False)

        # 运行中的 HUD：文本走的 TextCache，缓存里存的是渲染源字符串
        screen = pygame.display.set_mode((1280, 960))
        hud = HUD()
        hud.draw(screen, g.world, g.player, 0.0, 1 / 60, False)
        goal = hud.tc._cache["goal"][0][0]
        assert "辅助" in goal, f"HUD 目标行没写辅助：{goal!r}"

        # 结算页：第一行统计就是"辅助模式 / 开 · 不计纪录"
        hud2 = HUD()
        screen = pygame.display.set_mode(END_DESIGN)
        hud2.draw_end(screen, g.world, {}, False, 0.0, "最佳纪录")
        label = hud2.tc._cache["stat_l0"][0][0]
        value = hud2.tc._cache["stat_v0"][0][0]
        assert label == "辅助模式", f"结算页第一行是 {label!r}"
        assert "不计纪录" in value, f"结算页没说明不算纪录：{value!r}"
    print("[OK] 菜单 / HUD / 结算页三处都写明「辅助模式 · 不计纪录」")


def test_menu_toggles_with_a():
    """按 A 切开关，并且开关状态跟着菜单对象走（开局时才落盘）。"""
    with _Tmp():
        from sneak_ai.core.menu import Menu

        class _Sfx:
            def __init__(self):
                self.muted = False

            def play(self, *a, **k):
                pass

            def toggle_mute(self):
                self.muted = not self.muted

        m = Menu({}, 0, assist=False)
        m.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a), _Sfx())
        assert m.assist is True
        m.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a), _Sfx())
        assert m.assist is False

        # 开局才写盘：菜单自己不碰存档
        g = _game(0, assist=False)
        g.menu.assist = True
        g._on_menu_start()
        assert settings.load_assist() is True
        assert g.assist is True and g.world.view_mul == C.ASSIST_VIEW
    print("[OK] A 键切开关，开局时才落盘并生效")


def main() -> int:
    test_prefs_merge_instead_of_overwrite()
    test_assist_scales_only_enemy_abilities()
    test_assist_makes_level4_chase_runnable()
    test_assist_off_is_exactly_vanilla()
    test_assist_run_writes_no_records()
    test_normal_run_still_writes_records()
    test_player_can_see_assist_is_on()
    test_menu_toggles_with_a()
    print("\nW32 全部通过：辅助模式削的是敌人，说的是实话")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
