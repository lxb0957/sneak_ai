"""逻辑冒烟测试：不启动图形窗口，只验证核心算法正确性。

用法：
    python scripts/smoke_test.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai.core.grid import GridMap, raycast_blocks
from sneak_ai.core.world import build_demo_map
from sneak_ai.perception.perception import Perception, angle_diff


def test_raycast():
    m = GridMap.empty(10, 10)
    for x in range(3, 7):
        m.set_wall(x, 5)
    # 被墙挡住
    assert raycast_blocks(m, 5, 2, 5, 8), "射线应被中间墙挡住"
    # 无墙直线
    assert not raycast_blocks(m, 0.5, 0.5, 2.5, 0.5), "无障碍直线不应被挡"
    print("[OK] raycast 遮挡判定正确")


def test_angle_diff():
    assert abs(angle_diff(0.0, math.pi / 2) - math.pi / 2) < 1e-6
    assert abs(angle_diff(math.radians(350), math.radians(10)) - math.radians(20)) < 1e-6
    print("[OK] angle_diff 跨 0° 归一化正确")


def test_fov():
    m = GridMap.empty(20, 20)
    p = Perception(fov_angle=math.radians(90), view_range=8.0)
    eye = (10.0, 10.0)
    facing = 0.0  # 朝右
    # 正前方可见
    assert p.can_see(eye, facing, (15.0, 10.0), m) > 0
    # 正后方不可见（锥外）
    assert p.can_see(eye, facing, (5.0, 10.0), m) == 0.0
    # 视野范围外不可见
    assert p.can_see(eye, facing, (18.5, 10.0), m) == 0.0
    print("[OK] FOV 视觉锥 + 距离判定正确")


def test_demo_map():
    m = build_demo_map()
    assert m.w == 80 and m.h == 60
    # 走廊与房间已挖开（A1 走廊 / G1 房间内部）
    assert m.is_walkable(14, 6)
    assert m.is_walkable(23, 8)   # G1 房间内部（避开柱子）
    # 门收窄到 2 格：门两侧的格子必须是墙，否则"门"就退化成一条通道
    assert m.is_walkable(11, 5) and m.is_walkable(11, 6)
    assert m.is_wall(11, 4) and m.is_wall(11, 7)
    # 边界是墙
    assert m.is_wall(0, 0)
    print("[OK] 演示地图生成正确（实心开挖 + 2 格门）")


if __name__ == "__main__":
    test_raycast()
    test_angle_diff()
    test_fov()
    test_demo_map()
    print("\n全部冒烟测试通过 ✅")
