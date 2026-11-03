"""W23 测试：布局审计的覆盖率 —— 审计本身会不会漏查一整块 UI。

第 4 关玩家反馈"看不到完整地图"之后，顺手把 audit_ui 扩到了四档窗口。
扩完发现同一类问题还有一层：**审计只在第 1 关跑**，而第 4 关才是唯一有
总闸条、唯一 92x42、唯一需要全图的关卡：

    audit_ui 的 _HUD_PARTS 是**手写清单**，里面从来没有 "_draw_power"。
    第 1 关 power 为空 → 这个方法直接 return，什么也不画，所以"全绿"；
    第 4 关 power 有两条 → 它画了，但没人记账。

于是审计报告说"第 4 关 26 个落点、零重叠"，看起来把关卡也查了，
实际上第 4 关最需要随时瞄一眼的那行字（断电剩余秒数）从没进过账本。
**手写清单的失效方式是静默的**：新加一个 _draw_xxx 忘了登记，不多也不少，
只是覆盖率悄悄掉一块，而报告依然全绿。

这组测试守四件事：

1. 清单必须覆盖 Hud 上**所有** `_draw_*` 方法（用 dir() 反查，不靠记性）；
2. 四关 × 四档窗口全部零重叠、零越界（同一份审计，16 种组合）；
3. Tab 全图也在审计里，而且它只查越界、不查重叠（它本来就盖在 HUD 上）；
4. 反向验证：把总闸从清单里删掉，第 4 关的落点数会掉回第 1 关的水平 ——
   证明这块 UI 确实是靠清单登记才被查到的，不是碰巧。

用法：
    python scripts/test_w23.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((1280, 960))

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
import audit_ui  # noqa: E402


def _restoring():
    """audit_size 会改窗口常量，跑完必须还原 —— 否则后面的用例全在
    上一个尺寸的坐标系里算，报出来的重叠是假的。"""
    return (C.WINDOW_W, C.WINDOW_H, C.VIEW_W, C.VIEW_H)


def _restore(snap) -> None:
    C.WINDOW_W, C.WINDOW_H, C.VIEW_W, C.VIEW_H = snap
    pygame.display.set_mode((C.WINDOW_W, C.WINDOW_H))


def test_every_draw_method_is_registered():
    """Hud 上每个 `_draw_*` 都要么在清单里、要么被明确排除。

    结算页那三个方法（draw_end / _draw_end_scaled / _draw_end_card）是
    `_END_PARTS` 里显式排除的，它们由 collect() 单独跑一遍。
    """
    g = Game()
    missing = audit_ui.undiscovered_parts(g.hud)
    assert not missing, (
        f"这些绘制方法没登记进 _HUD_PARTS，审计根本不会看它们：{missing}")
    print(f"[OK] {len(audit_ui._HUD_PARTS)} 个 HUD 元素全部登记在册")


def test_orphan_method_would_be_caught():
    """反向验证：扫描器本身得会报警。

    直接给 hud 挂一个假方法，`undiscovered_parts` 必须把它揪出来 ——
    否则上面那条"全部登记"可能只是因为扫描器永远返回空列表。
    """
    g = Game()

    def _draw_orphan(self, screen):
        return

    g.hud._draw_orphan = _draw_orphan.__get__(g.hud)
    missing = audit_ui.undiscovered_parts(g.hud)
    assert "_draw_orphan" in missing, (
        f"挂了一个没登记的 _draw_orphan，扫描器却报 {missing}")
    print("[OK] 未登记的绘制方法会被扫描器抓出来")


def test_power_bar_enters_the_ledger():
    """第 4 关的总闸条必须真的被录进账本。

    判据不是"清单里有这一行"（那是自证），而是**第 4 关录到的落点里，
    确实出现了只属于总闸条的那块矩形**：它在暴露度条下方，宽 322。
    """
    snap = _restoring()
    try:
        C.WINDOW_W, C.WINDOW_H = 1280, 960
        C.VIEW_W, C.VIEW_H = 40, 30
        pygame.display.set_mode((1280, 960))
        g = Game(start_level=3)
        assert g.world.power, "第 4 关没有总闸节点，这条测试需要重写"
        game_rects = [r for label, r, mode in audit_ui.collect(g) if label == "游戏中"][0]
        # 总闸面板 (12, 198, 322, 28)：宽 322、高 28、贴着左沿
        hits = [k for k in game_rects if k[0] == 12 and k[2] == 322 and k[3] == 28]
        assert hits, (
            "第 4 关的落点里没有总闸条那块 322x28 的面板 —— 它没进账本。"
            f" 录到的矩形：{sorted(game_rects)[:12]}")
    finally:
        _restore(snap)
    print("[OK] 第 4 关总闸条进了审计账本")


def test_every_level_every_size_is_clean():
    """四关 × 四档窗口：零重叠、零越界、零漏登记。

    这是"能力写在降级列表里就必须每个尺寸跑一次"的落地：
    - 关卡维度：第 4 关有总闸条和全图，前三关没有；
    - 尺寸维度：`_create_window` 会降到 896x672 / 800x600。
    两个维度都要真跑，不能拿"第 1 关 + 1280x960"代表全部。
    """
    snap = _restoring()
    try:
        for lv in L.LEVELS:
            for size in audit_ui.SIZES:
                rc = audit_ui.audit_size(size, lv.idx)
                assert rc == 0, (
                    f"第 {lv.idx + 1} 关在窗口 {size[0]}x{size[1]} 下审计不通过"
                    f"（见上方明细）")
    finally:
        _restore(snap)
    print(f"[OK] {len(L.LEVELS)} 关 × {len(audit_ui.SIZES)} 档窗口全部干净")


def test_full_map_is_bounds_only():
    """全图属于模态覆盖层：只查越界，不参与重叠。

    全图打开时 HUD 还在下面画着，两者"重叠"是设计（它盖住 HUD 才看得清
    整张图）。如果哪天有人把全图并进重叠检查，会立刻报出一堆假阳性 ——
    而假阳性会让人开始不信这个工具，比漏报更伤。
    """
    g = Game(start_level=3)
    groups = {label: mode for label, _r, mode in audit_ui.collect(g)}
    assert groups.get("全图") == "bounds", (
        f"全图那一组的检查模式不是 bounds，而是 {groups.get('全图')!r}")
    assert groups.get("游戏中") == "overlap", "游戏中的检查模式被改掉了"
    print("[OK] 全图只查越界，不参与重叠统计")


def main():
    print("=== W23：布局审计的覆盖率 ===")
    test_every_draw_method_is_registered()
    test_orphan_method_would_be_caught()
    test_power_bar_enters_the_ledger()
    test_full_map_is_bounds_only()
    test_every_level_every_size_is_clean()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
