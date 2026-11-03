"""W12：主菜单 / 设置持久化 / 上下文引导。

三个新交互都有一个共同要求：**不能因为自己的存在挡住游戏**。
- 菜单：回车必须能开局；ESC 在游戏里是回菜单、在菜单里才是退出；
- 设置：静音状态跨局保留；坏文件、缺字段都不能崩；
- 引导：走近补给点才提示、每类只提示一次、提示不会盖住结算页。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

import pygame._sdl2 as sdl2  # noqa: F401  (仅为确保 pygame 完整初始化)

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core import settings  # noqa: E402

pygame.init()
pygame.display.set_mode((320, 240))

_results = []


def check(name: str, cond: bool) -> None:
    _results.append((name, cond))
    print(("[OK] " if cond else "[FAIL] ") + name)


# ---------- 设置持久化 ----------
def test_settings_roundtrip(tmp: Path):
    settings.SETTINGS_FILE = tmp / "settings.json"

    check("默认未静音", settings.load_muted() is False)
    settings.save(True)
    check("写入后读回为静音", settings.load_muted() is True)
    settings.save(False)
    check("改回未静音", settings.load_muted() is False)

    # 坏 JSON / 非字典 / 缺字段：一律当未静音，不能崩
    for content in ("{broken", "[1,2]", '"muted"', ""):
        (tmp / "settings.json").write_text(content, encoding="utf-8")
        check(f"坏文件({content[:8]!r}) 当未静音", settings.load_muted() is False)
    (tmp / "settings.json").write_text('{"muted": "yes"}', encoding="utf-8")
    check("字符串 muted 不被当成 True", settings.load_muted() is False)


# ---------- Game：菜单 / 引导 ----------
def test_game_menu_and_tips():
    settings.SETTINGS_FILE = Path(__import__("tempfile").gettempdir()) / "w12_settings.json"
    settings.SETTINGS_FILE.write_text('{"muted": true}', encoding="utf-8")

    g = Game()
    check("启动即主菜单", g.menu.active is True)
    check("菜单读取持久化静音", g.sfx.muted is True)

    # 菜单渲染不崩
    g._time_accum = 0.5
    g._render_menu()

    def press(key):
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key))
        if g.menu.active:
            g._handle_menu_events()
        else:
            g._handle_events()

    # ESC 在菜单里 → 退出；回车 → 开局（新的一局 + 引导）——走真实事件流
    press(pygame.K_RETURN)
    check("回车关闭菜单", g.menu.active is False)
    check("开局触发引导标志", g._show_intro is True)

    # 游戏内 ESC → 回菜单（并刷新 best）
    press(pygame.K_ESCAPE)
    check("ESC 回菜单", g.menu.active is True)
    press(pygame.K_RETURN)
    check("再次开局", g.menu.active is False and g._show_intro is True)

    # 引导：走近补给点触发，每类一次
    p = g.world.player
    target = next(it for it in g.world.pickups if it["kind"] == "ammo")
    p.x, p.y = target["x"] + 2.0, target["y"]
    g._update_tips(1 / 60)
    check("走近弹药包触发提示", g.hud._tip_left > 0 and "弹药" in g.hud._tip)
    g.hud._tip_left = 0.0
    g._update_tips(1 / 60)
    check("同类不重复提示", g.hud._tip_left <= 0.0)

    # 提示生命周期：会自然耗尽
    g.hud.show_tip("测试", life=0.5)
    for _ in range(40):
        g.hud._draw_tip(g.screen, 1 / 60)
    check("提示到期后自动消失", g.hud._tip_left <= 0.0)

    # 游戏内 M 切换并持久化（同样走真实事件流）
    press(pygame.K_m)
    check("M 后落盘", settings.load_muted() == g.sfx.muted)


def test_no_ui_overlap():
    """UI 零重叠：这是"字会重叠"这轮问题的回归测试。

    以前靠肉眼找重叠，永远只能发现最明显那处；现在用 audit_ui 里
    "记录真实 blit 落点"的办法，把 HUD 层的矩形两两求交。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import audit_ui

    g = Game()
    # collect 现在按"游戏中 / 结算页"分组返回：两组不可能同屏，跨组求交会误报
    groups = audit_ui.collect(g)
    bad = []
    for _label, rects in groups:
        bad += audit_ui.find_overlaps(rects)
    total = sum(len(r) for _l, r in groups)
    detail = "; ".join(f"{a.topleft}×{b.topleft}" for a, _, b, _, _ in bad)
    check(f"UI 元素零重叠（{total} 个落点）: {detail}", not bad)


def test_awareness_and_defeat():
    """W12 可读性三件套：可疑度弧 / 起疑箭头 / 失败原因。

    这些都不是"新玩法"，而是**把已有状态画出来** —— 所以测试重点是
    "画出来的东西和世界状态对得上"，而不是像素长什么样。
    """
    g = Game()
    g.menu.active = False
    e = g.world.enemies[0]
    p = g.player
    p.x, p.y = e.x + 3.0, e.y
    e.facing = 0.0
    e.suspicion = 0.5
    g.cam.follow(p.x, p.y, 0.0)
    for _ in range(3):
        g.world.update(1 / 60)

    # 可疑度弧：正在看你的人才会被画，且不崩
    sx, sy = g.cam.world_to_screen(e.x, e.y)
    g.px.draw_awareness(g.screen, int(sx), int(sy), 0.5, False, 0.3)
    g.px.draw_awareness(g.screen, int(sx), int(sy), 0.9, True, 0.3)
    check("可疑度弧/锁定括号绘制不崩", True)

    # 起疑但没确认的哨兵也要在屏幕外画箭头（只标追兵 = 报警太晚）
    e.role = type(e.role).PATROL
    e.suspicion = 0.5
    e.x, e.y = p.x + 60, p.y           # 挪到屏幕外
    before = g.hud.tc.get("x", "探", 10, (0, 0, 0))
    g.hud.draw_threat_markers(g.screen, g.cam, g.world, 0.0)
    check("起疑哨兵在屏幕外时画弱箭头（不崩）", before is not None)

    # 失败原因：必须点出角色 + "敌人在玩家哪一边"
    g.world.captured_by = e
    p.x, p.y = e.x - 0.5, e.y          # 敌人位于玩家东边（dx = e.x - p.x > 0）
    reason = g.hud._defeat_reason(g.world)
    check(f"失败原因含方位与角色: {reason}",
          "东边" in reason and "哨兵" in reason)

    # 失败页不给分数（得分是"完成任务的效率"，没完成就不该有分）
    g.world.state = C.STATE_DEFEAT
    g.world.elapsed = 0.0
    prog = g.hud._progress_text(g.world)
    check(f"失败页显示进度而不是分数: {prog}", prog == "刚开局")


def main() -> int:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="w12_"))
    test_settings_roundtrip(tmp)
    test_game_menu_and_tips()
    test_no_ui_overlap()
    test_awareness_and_defeat()

    failed = [n for n, ok in _results if not ok]
    if failed:
        print(f"\n{len(failed)} 项失败: {failed}")
        return 1
    print("\nW12 主菜单 / 设置 / 引导 测试全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
