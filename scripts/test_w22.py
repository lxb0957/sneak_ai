"""W22 测试：相机边界 —— "窗口过不去，看不到最右边"。

玩家反馈第 4 关走到东头就推不动了，最右边永远看不到。原因不在渲染，在
`Camera.follow` 里：它把边界写死成常量 `C.GRID_W/GRID_H`（80x60，第 1 关的
尺寸），而第 4 关是 **92x42**：

    实际需要 max_x = 92*32 - 1280 = 1664
    代码夹到   max_x = 80*32 - 1280 = 1280   ← 少滑 384 像素 = 12 格

第 4 关唯一的终端在 x=88.5、第二个总闸在 x=89.5，两个都在那 12 格里 ——
玩家不但看不到最右边，连通关目标都被挤到屏幕外，人根本没法玩
（机器人是拿格坐标走路的，所以 25% 的胜率里看不出这个 bug）。

这组测试守四件事：

1. **四个方向都能滑到头**：站在地图最东/最南，视口必须真的覆盖到地图边缘；
2. **玩家自己不能滑出屏幕**：目标点被 clamp 掉的时候，玩家会先消失；
3. **相机不许滑出地图**：第 4 关只有 42 行，按 60 行算会滑出 18 行空白；
4. **每一关的每个目标点都进得了屏幕**：这才是玩家真正要的东西。

用法：
    python scripts/test_w22.py
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
from sneak_ai.core.camera import Camera  # noqa: E402


def _settle(cam: Camera, x: float, y: float) -> None:
    """把相机平滑跟到位（真机上也是这么跟过来的）。"""
    for _ in range(180):
        cam.follow(x, y, 1 / 60.0)


def _view(cam: Camera) -> tuple:
    """当前视口覆盖的世界格范围：x0, y0, x1, y1（格）。"""
    t = C.TILE_SIZE
    return (cam.offset_x / t, cam.offset_y / t,
            (cam.offset_x + C.WINDOW_W) / t, (cam.offset_y + C.WINDOW_H) / t)


def test_camera_reaches_the_far_edge_of_every_level():
    """每个方向都要能滑到地图边缘，一格都不能少。"""
    for lv in L.LEVELS:
        g = Game(start_level=lv.idx)
        grid = g.world.grid
        for wx, wy, axis in ((grid.w - 1.5, grid.h / 2, "东"),
                             (grid.w / 2, grid.h - 1.5, "南"),
                             (0.5, grid.h / 2, "西"),
                             (grid.w / 2, 0.5, "北")):
            _settle(g.cam, wx, wy)
            x0, y0, x1, y1 = _view(g.cam)
            if axis == "东":
                assert x1 >= grid.w - 0.01, (
                    f"第 {lv.idx + 1} 关走到最东端，视口只到 x={x1:.1f}/{grid.w}"
                    f"（差 {grid.w - x1:.1f} 格看不到）")
            elif axis == "西":
                assert x0 <= 0.01, f"第 {lv.idx + 1} 关最西端没贴住地图左沿 x0={x0:.1f}"
            elif axis == "南":
                assert y1 >= grid.h - 0.01, (
                    f"第 {lv.idx + 1} 关走到最南端，视口只到 y={y1:.1f}/{grid.h}")
            else:
                assert y0 <= 0.01, f"第 {lv.idx + 1} 关最北端没贴住地图上沿 y0={y0:.1f}"
    print("[OK] 四关的东南西北都能滑到地图边缘")


def test_player_stays_on_screen_at_the_extremes():
    """玩家站位不能跑出屏幕 —— 那等于把玩家自己藏起来。"""
    for lv in L.LEVELS:
        g = Game(start_level=lv.idx)
        grid = g.world.grid
        for wx, wy in ((grid.w - 1.5, grid.h - 1.5), (0.5, 0.5),
                       (grid.w - 1.5, 0.5), (0.5, grid.h - 1.5)):
            _settle(g.cam, wx, wy)
            sx, sy = g.cam.world_to_screen(wx, wy)
            assert 0 <= sx < C.WINDOW_W and 0 <= sy < C.WINDOW_H, (
                f"第 {lv.idx + 1} 关玩家在 ({wx},{wy}) 被推出屏幕："
                f"屏幕坐标 ({sx},{sy}) / 窗口 {C.WINDOW_W}x{C.WINDOW_H}")
    print("[OK] 玩家在最角落也留在屏幕内")


def test_camera_never_pans_outside_the_map():
    """相机不许滑出地图：第 4 关 42 行，按 60 行算会露出 18 行黑边。"""
    for lv in L.LEVELS:
        g = Game(start_level=lv.idx)
        grid = g.world.grid
        max_x = max(0.0, grid.w * C.TILE_SIZE - C.WINDOW_W)
        max_y = max(0.0, grid.h * C.TILE_SIZE - C.WINDOW_H)
        for wx, wy in ((grid.w - 0.5, grid.h - 0.5), (0.0, 0.0),
                       (grid.w - 0.5, 0.0), (0.0, grid.h - 0.5)):
            _settle(g.cam, wx, wy)
            assert -0.01 <= g.cam.offset_x <= max_x + 0.01, (
                f"第 {lv.idx + 1} 关 offset_x={g.cam.offset_x:.1f} 越界 [0,{max_x}]")
            assert -0.01 <= g.cam.offset_y <= max_y + 0.01, (
                f"第 {lv.idx + 1} 关 offset_y={g.cam.offset_y:.1f} 越界 [0,{max_y}]")
    print("[OK] 相机不会滑出地图")


def test_every_objective_is_visible_from_somewhere():
    """每一关的终端、出口、总闸，站在它上面时必须真的在屏幕里。

    这是这个 bug 最真实的形态：第 4 关终端在 x=88.5，旧边界只到 x=80，
    玩家就算摸到终端脚下，屏幕上也什么都看不到。
    """
    for lv in L.LEVELS:
        g = Game(start_level=lv.idx)
        targets = list(lv.terminals) + [lv.exfil] + list(lv.power)
        for (tx, ty) in targets:
            _settle(g.cam, tx, ty)
            x0, y0, x1, y1 = _view(g.cam)
            assert x0 <= tx <= x1 and y0 <= ty <= y1, (
                f"第 {lv.idx + 1} 关的目标点 ({tx},{ty}) 不在视口内："
                f"视口 x {x0:.1f}~{x1:.1f}, y {y0:.1f}~{y1:.1f}")
    print("[OK] 每一关的目标点都进得了屏幕")


def test_bounds_follow_the_level_not_a_constant():
    """边界必须来自当前关卡，而不是常量。

    反向验证：把第 4 关的相机按旧的 80x60 设边界，"能看到东沿"这条断言
    必须**失败** —— 否则上面那些测试就是空壳，改不改代码都一样绿。
    """
    grid4 = L.LEVELS[3].build()
    assert grid4.w > C.GRID_W, "第 4 关不再比常量宽，这条回归测试需要重写"

    stale = Camera()
    stale.set_bounds(C.GRID_W, C.GRID_H)          # 旧行为
    _settle(stale, grid4.w - 1.5, grid4.h / 2)
    x0, _y0, x1, _y1 = _view(stale)
    assert x1 < grid4.w - 1, (
        f"用旧边界 80x60 竟然也能看到东沿（x1={x1:.1f}），这条反向验证失效了")

    fixed = Camera()
    fixed.set_bounds(grid4.w, grid4.h)
    _settle(fixed, grid4.w - 1.5, grid4.h / 2)
    x0, _y0, x1, _y1 = _view(fixed)
    assert x1 >= grid4.w - 0.01, f"按关卡尺寸设边界后仍看不到东沿 x1={x1:.1f}"

    # 真机路径：Game 加载关卡时必须自己设过边界
    g = Game(start_level=3)
    assert (g.cam.grid_w, g.cam.grid_h) == (grid4.w, grid4.h), (
        f"Game 没有把关卡尺寸交给相机：相机仍按 {g.cam.grid_w}x{g.cam.grid_h} 夹取")
    print("[OK] 相机边界跟着关卡走（旧常量行为确认会失败）")


def main():
    print("=== W22：相机边界 ===")
    test_bounds_follow_the_level_not_a_constant()
    test_camera_reaches_the_far_edge_of_every_level()
    test_player_stays_on_screen_at_the_extremes()
    test_camera_never_pans_outside_the_map()
    test_every_objective_is_visible_from_somewhere()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
