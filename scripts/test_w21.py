"""W21 测试：全图（Tab）—— "能不能一次看完一整关"。

第 4 关是 92x42 格，窗口一次只显示 40x30 格，也就是**不到全图的两成**；
小地图每格 2 像素，那条 74 格长的直廊被压成 184 像素，三处收窄段和
通往总闸的支路全糊成一片。玩家反馈"地图没办法完全显示"说的就是这件事。

这组测试守的是"全图这件事本身还成立"，而不是配色好不好看：

1. **每一关都放得进窗口**：缩放下限 3 像素/格、上限不超过窗口 —— 上下限
   任一失效，第 4 关就会溢出屏幕或糊成色块；
2. **画出来的就是整张图**：`w // tile == grid.w`，少一格都叫"没显示完"；
3. **打开全图时世界冻结**：站在长廊里摊开地图不该被巡逻兵摸掉；
   同时 `elapsed` 也要冻住，否则"开着地图"就成了免费的思考时间；
4. **关闭后世界继续走**：冻结必须是可逆的，别把暂停写成死锁；
5. **全图里没有敌人**：这是潜行游戏，一张带敌人位置的图等于全图透视。

用法：
    python scripts/test_w21.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((1280, 960))

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core.minimap import FULL_TILE_MAX, FULL_TILE_MIN, FullMap  # noqa: E402


def test_full_map_fits_every_level():
    """每一关的整图都必须塞进窗口，且不能糊到看不清走廊。"""
    for lv in L.LEVELS:
        grid = lv.build()
        fm = FullMap(grid)
        assert fm.tile >= FULL_TILE_MIN, (
            f"第 {lv.idx + 1} 关缩到 {fm.tile} 像素/格，走廊会糊掉")
        assert fm.tile <= FULL_TILE_MAX, (
            f"第 {lv.idx + 1} 关放大到 {fm.tile} 像素/格，超出窗口")
        assert fm.w <= C.WINDOW_W - 40, (
            f"第 {lv.idx + 1} 关整图宽 {fm.w} > 窗口 {C.WINDOW_W}")
        # 上方要留标题和一行说明，下方留面板内边距
        assert fm.h <= C.WINDOW_H - 120, (
            f"第 {lv.idx + 1} 关整图高 {fm.h} > 窗口 {C.WINDOW_H} 的可用高度")
    print(f"[OK] {len(L.LEVELS)} 关整图都放得进窗口")


def test_full_map_fits_a_small_window():
    """窗口被降级到 1024x768 时，整图也必须跟着缩。

    这条是补的：第一版把 max_box 写死成 1160x760，在 1280x960 下没问题，
    而笔记本上 `_create_window` 会降级到 1024x768 —— 那时 1104 宽的整图
    比窗口还宽，直接被切掉两边，功能等于没做。
    """
    big_w, big_h, big_vw, big_vh = C.WINDOW_W, C.WINDOW_H, C.VIEW_W, C.VIEW_H
    try:
        C.WINDOW_W, C.WINDOW_H = 1024, 768
        C.VIEW_W, C.VIEW_H = 1024 // C.TILE_SIZE, 768 // C.TILE_SIZE
        for lv in L.LEVELS:
            fm = FullMap(lv.build())
            assert fm.w <= C.WINDOW_W - 40, (
                f"1024x768 窗口下第 {lv.idx + 1} 关整图宽 {fm.w}，放不下")
            assert fm.h <= C.WINDOW_H - 120, (
                f"1024x768 窗口下第 {lv.idx + 1} 关整图高 {fm.h}，放不下")
            assert fm.w // fm.tile == lv.build().w, "缩放档位不是整数倍"
    finally:
        C.WINDOW_W, C.WINDOW_H = big_w, big_h
        C.VIEW_W, C.VIEW_H = big_vw, big_vh
    print("[OK] 1024x768 的小窗口下整图也放得下")


def test_full_map_covers_the_whole_grid():
    """"整图"必须是整张：少一格都叫没显示完。"""
    for lv in L.LEVELS:
        grid = lv.build()
        fm = FullMap(grid)
        assert fm.w // fm.tile == grid.w and fm.h // fm.tile == grid.h, (
            f"第 {lv.idx + 1} 关整图 {fm.w // fm.tile}x{fm.h // fm.tile} "
            f"!= 地图 {grid.w}x{grid.h}")
    # 第 4 关正是"一个视口装不下"的那一关，否则这个功能对它没有意义
    grid4 = L.LEVELS[3].build()
    assert grid4.w > C.VIEW_W, f"第 4 关宽 {grid4.w} 格，视口都装得下，不需要全图"
    assert grid4.w > C.VIEW_W * 2, (
        f"第 4 关宽 {grid4.w} 格 / 视口 {C.VIEW_W} 格 —— 应当超过两屏")
    print("[OK] 整图覆盖整张地图，第 4 关明显大于一个视口")


def test_tab_freezes_the_world():
    """打开全图：敌人不动、时间不走。"""
    g = Game(start_level=3)
    for _ in range(3):
        g.step(1 / 60.0)
    g.show_map = True
    t0 = g.world.elapsed
    pos0 = [(e.x, e.y) for e in g.world.enemies]
    for _ in range(30):
        g.step(1 / 60.0)
    assert g.world.elapsed == t0, f"开着全图时间还在走：{t0} -> {g.world.elapsed}"
    pos1 = [(e.x, e.y) for e in g.world.enemies]
    assert pos0 == pos1, "开着全图敌人还在巡逻（会被顺手摸掉）"
    print("[OK] 全图打开时世界冻结")


def test_closing_the_map_resumes_the_world():
    """冻结必须可逆。"""
    g = Game(start_level=3)
    g.show_map = True
    g.step(1 / 60.0)
    t0 = g.world.elapsed
    g.show_map = False
    for _ in range(10):
        g.step(1 / 60.0)
    assert g.world.elapsed > t0, "关掉全图之后世界没有恢复"
    print("[OK] 关掉全图之后世界继续走")


def test_full_map_never_draws_enemies():
    """全图上不许出现敌人：潜行游戏里那叫全图透视。

    验的是**合成之后的整帧**，不是底图 —— 只查底图等于没查：
    底图按构造就只有三种颜色，把敌人画在 `draw()` 里照样能绿。
    所以先真的画一帧，再用 numpy 全图扫描敌兵色。
    """
    import numpy as np

    role_colors = [tuple(c[:3]) for c in C.COL_ROLE.values()]
    role_colors.append(tuple(C.COL_ENEMY[:3]))

    g = Game(start_level=3)
    surf = pygame.Surface((C.WINDOW_W, C.WINDOW_H))
    surf.fill((0, 0, 0))
    g.fullmap.draw(surf, g.world, g.cam)
    arr = np.array(pygame.surfarray.pixels3d(surf))     # (x, y, 3)

    def has(color) -> bool:
        return bool((arr == np.array(color)).all(axis=-1).any())

    found = [c for c in role_colors if has(c)]
    assert not found, f"第 4 关的全图上出现了敌人色：{found}"

    # 反向验证：扫描器本身不是永远返回 False 的空壳
    probe = arr.copy()
    probe[4, 4] = role_colors[0]
    assert (probe == np.array(role_colors[0])).all(axis=-1).any(), "扫描器失效"
    print("[OK] 全图上没有敌人（整帧扫描）")


def main():
    print("=== W21：全图（Tab） ===")
    test_full_map_fits_every_level()
    test_full_map_fits_a_small_window()
    test_full_map_covers_the_whole_grid()
    test_tab_freezes_the_world()
    test_closing_the_map_resumes_the_world()
    test_full_map_never_draws_enemies()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
