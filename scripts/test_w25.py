"""W25：主菜单的选关，和"选关之后这局算不算成绩"。

加选关不是为了做难度菜单，是为了让第一次点开 exe 的人能在三十秒内看到第 5 关 ——
第 4 关的机器人胜率只有 25%，"必须先打通四关"等于把这一版真正做的东西
藏在一个大多数人都到不了的位置后面。

但这会踩到一个已经存在的规则：`_handle_scoring` 里写着"用 `--level N`
中途开局的这一局不计入最佳纪录"，理由是"从第 3 关起步只要 35 秒就能打完，
交了就是一条永远刷不掉的假纪录"。选关必须走**同一条**规则，不能另开一份。

所以这组测试守四件事：

1. **选择被夹住**：只会停在 1..N，不会因为长按跑到第 0 关或第 6 关；
2. **锁着的关卡也能进**（否则评审还是看不到第 5 关），但文案必须说清
   "这一局不计纪录"——不许默默把成绩吞掉；
3. **菜单选关 = `_start_level`**，并且从第 2 关起步的胜利局真的不写存档；
4. **菜单布局不互相压**：三档窗口下每块文字都在屏内、两两不相交。
   菜单是唯一没被审计过的界面，而它刚被查出"STEALTH PROTOCOL"压在
   "潜行协议"的下沿上压了四个版本。

用法：
    python scripts/test_w25.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core import records  # noqa: E402
from sneak_ai.core.menu import Menu  # noqa: E402


class _Sfx:
    """菜单只用到 play / toggle_mute 两个方法，别为测它去初始化混音器。"""

    def __init__(self):
        self.muted = False
        self.played = []

    def play(self, name, cooldown=0.0, volume=1.0):
        self.played.append(name)

    def toggle_mute(self):
        self.muted = not self.muted


def _key(k):
    return pygame.event.Event(pygame.KEYDOWN, key=k)


def test_selection_is_clamped():
    """左右各按 N 次，落点必须停在 1..N，而且一次只走一格。"""
    n = len(L.LEVELS)
    m = Menu({}, sel=0)
    sfx = _Sfx()
    for _ in range(n + 3):
        m.handle_event(_key(pygame.K_LEFT), sfx)
    assert m.sel == 0, f"按到底了还往下走：sel={m.sel}"
    seen = []
    for _ in range(n + 3):
        m.handle_event(_key(pygame.K_RIGHT), sfx)
        seen.append(m.sel)
    assert m.sel == n - 1, f"越过了最后一关：sel={m.sel} / {n - 1}"
    assert seen[:n - 1] == list(range(1, n)), f"没有一格一格地走：{seen[:n - 1]}"
    assert seen[n - 1:] == [n - 1] * 4, f"到顶之后还在动：{seen[n - 1:]}"
    # 从中间往回走同样只走一格
    m.sel = 2
    m.handle_event(_key(pygame.K_LEFT), sfx)
    assert m.sel == 1, f"往回也不是一格：{m.sel}"
    print(f"[OK] 选关夹在 1~{n}，每次一格（左按到底 / 右按到顶都不越界）")


def test_locked_level_is_still_selectable_and_says_so():
    """没通关的关卡也必须能进 —— 但界面要写明"这局不计纪录"。"""
    n = len(L.LEVELS)
    m = Menu({"score": 900, "levels": 2}, sel=n - 1)   # 只通了前两关
    assert m.cleared == 2, f"已通关数读错：{m.cleared}"
    txt = m.status_text()
    assert "不计纪录" in txt, f"未通关关卡没有说明不计纪录：{txt!r}"
    assert "未通关" in txt, f"没标出「未通关」：{txt!r}"

    m.sel = 1                                           # 第 2 关，已通关
    assert "已通关" in m.status_text(), m.status_text()
    assert "不计纪录" in m.status_text(), "已通关的关卡也不是正式开局，却没说不计纪录"

    m.sel = 0                                           # 第 1 关 = 正式开局
    assert "正式开局" in m.status_text(), m.status_text()
    print("[OK] 选关不设路障；文案分三种：正式开局 / 已通关 / 未通关，"
          "且跳关都标了不计纪录")


def test_menu_choice_becomes_start_level():
    """菜单点谁就进谁：写回 `_start_level`，重开一局也回这一关。"""
    n = len(L.LEVELS)
    g = Game()
    g.menu.sel = n - 1
    g._on_menu_start()
    assert g._start_level == n - 1, f"_start_level 没跟上菜单：{g._start_level}"
    assert g.level_index == n - 1, f"开局关卡不对：{g.level_index}"
    assert g.world.level_index == n - 1, "世界没按选中的关卡重建"
    # 死一次重开：回到同一关，而不是第 1 关
    g.level_index = 0
    g._restart_run()
    assert g.level_index == n - 1, f"重开跑回第 {g.level_index + 1} 关了"
    print(f"[OK] 菜单选第 {n} 关 → 开局就是第 {n} 关，死后重开也还在第 {n} 关")


def test_jump_run_does_not_touch_the_record():
    """从第 2 关起步打通的这一局不能写存档 —— 那是刷得出来的假纪录。"""
    tmp = Path(tempfile.mkdtemp(prefix="sneak_w25_"))
    old_dir, old_file = records.DATA_DIR, records.RECORD_FILE
    records.DATA_DIR, records.RECORD_FILE = tmp, None
    try:
        path = records.path()
        path.write_text(json.dumps({"score": 5000, "time": 120, "rank": "S",
                                    "levels": len(L.LEVELS)}), encoding="utf-8")
        before = path.read_text(encoding="utf-8")

        g = Game()
        g.menu.sel = 1                    # 第 2 关起步
        g._on_menu_start()
        g.level_index = len(L.LEVELS) - 1  # 直接摆到最后一关
        g._new_world()
        g.world.state = C.STATE_VICTORY
        g._scored = False
        g._next_timer = 0.0
        g._handle_scoring()
        assert path.read_text(encoding="utf-8") == before, "跳关的胜利局把存档改了"

        # 反证：从第 1 关起步的同一局**应该**写进去，否则上面那条什么都证明不了
        g2 = Game()
        g2.menu.sel = 0
        g2._on_menu_start()
        g2.level_index = len(L.LEVELS) - 1
        g2._new_world()
        g2.world.state = C.STATE_VICTORY
        g2.world.score = lambda: 99999        # 一定超过存档里的 5000
        g2._scored = False
        g2._next_timer = 0.0
        g2._handle_scoring()
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["score"] == 99999, f"正常开局的成绩没写进去：{after}"
    finally:
        records.DATA_DIR, records.RECORD_FILE = old_dir, old_file
    print("[OK] 跳关的胜利局不写存档；同一局从第 1 关起步就写（反证成立）")


def test_menu_layout_has_no_overlap():
    """三档窗口下：每块文字都在屏内，两两不相交。"""
    sizes = [(1280, 960), (1024, 768), (800, 600)]
    best = {"score": 1284, "time": 210, "rank": "A", "levels": 5,
            "when": "11-03 21:40"}
    for (w, h) in sizes:
        surf = pygame.display.set_mode((w, h))
        m = Menu(best, sel=len(L.LEVELS) - 1)
        m.draw(surf, False, 0.3)
        screen_rect = pygame.Rect(0, 0, w, h)
        assert len(m.rects) >= 10, f"{w}x{h} 只画了 {len(m.rects)} 块文字"
        for name, rect in m.rects:
            assert screen_rect.contains(rect), \
                f"{w}x{h} 的 {name} 出界了：{tuple(rect)}"
        for i in range(len(m.rects)):
            for j in range(i + 1, len(m.rects)):
                a, b = m.rects[i], m.rects[j]
                assert not a[1].colliderect(b[1]), \
                    f"{w}x{h}：{a[0]} 压住了 {b[0]}（{tuple(a[1])} vs {tuple(b[1])}）"
        # 箭头也得在屏内，否则"能点换关"是个假承诺
        for rect, step in m._arrows:
            assert screen_rect.contains(rect), f"{w}x{h} 的箭头出界：{tuple(rect)}"
    print(f"[OK] 菜单在 {' / '.join(f'{w}x{h}' for w, h in sizes)} 下零重叠、零出界")


def main():
    print("=== W25：主菜单与选关 ===")
    test_selection_is_clamped()
    test_locked_level_is_still_selectable_and_says_so()
    test_menu_choice_becomes_start_level()
    test_jump_run_does_not_touch_the_record()
    test_menu_layout_has_no_overlap()
    print("\nW25 全部通过：选关进得去、说得清、写不进假纪录、也不压字。")


if __name__ == "__main__":
    main()
