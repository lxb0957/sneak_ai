"""W15 测试：GOAP 接线（跟丢后的搜查路线）。

这一组测试要回答的问题不是"代码跑不跑得动"，而是：
**规划器到底有没有在做决定？** 如果把它换成一条写死的 if/else，哪些断言会红？

用法：python scripts/test_w15.py
"""

import itertools
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.ai.coordination import Coordinator, Role  # noqa: E402
from sneak_ai.ai.goap import Action, plan  # noqa: E402
from sneak_ai.ai.search_plan import (  # noqa: E402
    build_search_plan,
    find_hiding_spots,
)
from sneak_ai.core.grid import GridMap, raycast_blocks  # noqa: E402
from sneak_ai.core.world import World, build_demo_map  # noqa: E402
from sneak_ai.entity.enemy import Enemy  # noqa: E402
from sneak_ai.entity.player import Player  # noqa: E402
from sneak_ai.perception.perception import Perception  # noqa: E402


def make_world(grid=None, w=44, h=30, player=(35.5, 25.5)):
    grid = grid if grid is not None else GridMap.empty(w, h)
    world = World(grid)
    world.coordinator = Coordinator(grid)
    world.player = Player(*player)
    return world


def tick(world, seconds=1.0, dt=1 / 60.0, until=None):
    """推进世界。until(world) 返回 True 时提前停。"""
    for _ in range(int(seconds / dt)):
        world.update(dt)
        if until is not None and until(world):
            return True
    return False


def corridor_grid() -> GridMap:
    """L 形走廊：横向 y=5 从 x=1 到 x=10，纵向 x=10 从 y=5 到 y=10。

    这样 (10,6)(10,7)... 是"从 (7,5) 看过去被墙挡住、又贴着墙"的格子 ——
    正是藏身点的定义。其余走廊格子都看得见，不该被选中。
    """
    grid = GridMap.filled(14, 14)
    for x in range(1, 11):
        grid.set_floor(x, 5)
    for y in range(5, 11):
        grid.set_floor(10, y)
    return grid


# ---------------------------------------------------------------- GOAP 本体
def test_goap_add_delete_lists():
    """运动类动作必须能"划掉"旧位置，否则规划器会给出瞬移计划。"""
    move = Action(
        "move", 1.0,
        frozenset(["at_a"]), frozenset(["at_b"]), frozenset(["at_a"]),
    )
    after = move.apply(frozenset(["at_a"]))
    assert after == frozenset(["at_b"]), f"旧位置应被划掉，实际 {after}"
    assert not move.can_execute(after), "已离开 a 就不该再原地执行一次"

    # 不传 removes 时保持旧语义（纯加法），老测试不受影响
    add_only = Action("eat", 1.0, frozenset(["has_food"]), frozenset(["fed"]))
    assert add_only.apply(frozenset(["has_food"])) == frozenset(["has_food", "fed"])
    print("[OK] GOAP 动作支持加/删双列表（STRIPS）")


def test_planner_minimises_travel():
    """规划结果必须等于"暴力枚举所有访问顺序"里的最短那条。

    这是这一组的核心断言：换掉规划器、改成写死的顺序，它就会红。
    """
    start, target = (0, 0), (12, 0)
    spots = [(2, 9), (10, 3), (1, 1)]
    steps = build_search_plan(start, target, spots)

    moves = [tile for kind, tile in steps if kind == "move"]
    points = {target, *spots}
    assert set(moves) == points, f"每个点都要去一次，实际 {moves}"

    def travel(order):
        pos, total = start, 0.0
        for p in order:
            total += abs(p[0] - pos[0]) + abs(p[1] - pos[1])
            pos = p
        return total

    best = min(travel(o) for o in itertools.permutations(points))
    got = travel(moves)
    assert abs(got - best) < 1e-6, f"路线不是最短：规划 {got}，最优 {best}"
    print(f"[OK] 规划器按最省脚程排序（{got:.0f} 格 = 枚举最优解）")


def test_each_point_moved_then_swept():
    """每个点必须是"先走到、再原地搜"，不能出现连续两次移动后才回头搜。"""
    steps = build_search_plan((0, 0), (4, 4), [(4, 6), (6, 4)])
    assert steps[0][0] == "move", "第一步应该是移动"
    for i, (kind, tile) in enumerate(steps):
        if kind == "move":
            assert i + 1 < len(steps), "移动之后必须跟着一次搜查"
            assert steps[i + 1] == ("sweep", tile), (
                f"到点后应先搜这一处，实际下一步 {steps[i + 1]}"
            )
    sweeps = [t for k, t in steps if k == "sweep"]
    assert len(sweeps) == 3, f"主点 + 2 个藏身点 = 3 次搜查，实际 {len(sweeps)}"
    print("[OK] 计划是 move → sweep 成对推进")


# ---------------------------------------------------------------- 藏身点
def test_hiding_spots_are_occluded_and_walled():
    grid = corridor_grid()
    spots = find_hiding_spots(grid, (7.5, 5.5))

    assert spots, "L 形走廊的拐角后应该有藏身点"
    for (x, y) in spots:
        assert grid.is_walkable(x, y), f"{(x, y)} 不可走"
        assert raycast_blocks(grid, 7.5, 5.5, x + 0.5, y + 0.5), (
            f"{(x, y)} 从记忆点是看得见的，不该被当成藏身点"
        )
        walls = sum(
            1 for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0),
                             (-1, 1), (0, 1), (1, 1))
            if grid.is_wall(x + dx, y + dy)
        )
        assert walls >= 3, f"{(x, y)} 只有 {walls} 面墙，不像藏身处"

    # 走廊前半段（一眼看得到）不该入选
    assert all(x != 3 for x, _ in spots), f"看得见的格子被选进来了：{spots}"
    assert len(spots) <= C.SEARCH_SPOT_LIMIT
    print(f"[OK] 藏身点筛选（遮挡 + 贴墙）：{spots}")


def test_open_room_has_no_hiding_spots():
    """空旷地图上不该凭空造出"藏身处"——那会让哨兵毫无理由地跑去搜空气。"""
    assert find_hiding_spots(GridMap.empty(30, 30), (15.5, 15.5)) == []
    print("[OK] 空旷地图没有藏身点（不会无中生有）")


# ---------------------------------------------------------------- 敌人集成
def _enemy_losing_player(world, pos=(3.5, 5.5), seen=(9.5, 5.5), saw=True):
    """造一个"刚跟丢"的哨兵：可疑度在起疑以上，记忆点在前方。

    saw=True 表示这一轮里它真的看见过玩家 —— 翻角落是以此为前提的
    （只听到脚步的哨兵只查记忆点，见 _make_search_plan）。
    """
    e = Enemy(*pos, perception=Perception())
    world.enemies = [e]
    e.suspicion = C.SUSPICION_CURIOUS + 0.15
    e._last_seen = seen
    e._seen_this_episode = saw
    return e


def test_enemy_builds_and_executes_plan():
    grid = corridor_grid()
    world = make_world(grid, player=(12.5, 12.5))
    e = _enemy_losing_player(world)

    tick(world, 0.2)
    assert e._search_steps, "跟丢后应规划出搜查路线"
    kinds = [k for k, _ in e._search_steps]
    assert kinds.count("sweep") >= 2, f"应查多处，实际 {kinds}"
    assert e.state_name == "investigate"

    # 走一段之后：位置应该往记忆点/藏身点推进，而且搜查步在往前推
    x0 = e.x
    tick(world, 3.0)
    assert e.x > x0 + 0.5, f"应朝记忆点方向移动，x {x0:.2f} → {e.x:.2f}"
    assert e._search_i >= 1, "应该已经完成至少一步"
    print(f"[OK] 敌人按计划搜查（{len(e._search_steps)} 步，已推进 {e._search_i}）")


def test_plan_cleared_when_regaining_and_losing_sight():
    """重新追到人之后，旧搜查计划必须作废 —— 否则会接着上次的半条路线走。"""
    grid = corridor_grid()
    world = make_world(grid, player=(12.5, 12.5))
    e = _enemy_losing_player(world)
    tick(world, 3.5)
    assert e._search_steps is not None and e._search_i >= 1, "先要有半条走完的计划"

    # 人为切到"追击中"，再让可疑度掉到确认阈值以下 → 那一帧应作废旧计划
    e.role = Role.CHASE
    e.searching = False
    e.suspicion = 0.0
    tick(world, 0.1)
    assert e.role == Role.PATROL
    assert e._search_steps is None, "退回巡逻时应作废旧计划"
    assert e._search_i == 0, "旧计划的进度不应留到下一次"

    # 再次起疑（记忆还在）→ 重新排一条完整路线，从第一步开始
    e.suspicion = C.SUSPICION_CURIOUS + 0.1
    e._last_seen = (9.5, 5.5)
    tick(world, 0.1)
    assert e._search_steps, "再次起疑要重新规划"
    assert e._search_i == 0, f"新路线应从第一步开始，实际 {e._search_i}"
    print("[OK] 追击/跟丢切换时旧计划被作废，重新起疑时重排")


def test_unreachable_point_is_skipped():
    """记忆点落在墙里（听觉猜的）时不许顶着墙推，要跳到下一个点。"""
    grid = corridor_grid()
    world = make_world(grid, player=(12.5, 12.5))
    e = _enemy_losing_player(world, pos=(3.5, 5.5), seen=(0.5, 5.5))  # 左边是实心岩层
    tick(world, 0.3)
    assert e._search_steps, "即使目标不可达也要排计划"
    before = len(e._search_steps)
    ok = tick(world, 4.0, until=lambda w: len(e._search_steps) < before)
    assert ok, f"不可达的点应被划掉（{before} 步一直没减少）"
    print(f"[OK] 不可达点被跳过（{before} → {len(e._search_steps)} 步）")


def test_gives_up_after_budget():
    """搜查有总时长上限，不能无限期离岗。"""
    grid = corridor_grid()
    world = make_world(grid, player=(12.5, 12.5))
    e = _enemy_losing_player(world)
    tick(world, C.INVESTIGATE_GIVEUP + 2.0)
    assert e._last_seen is None, "超时后应清空记忆"
    assert e.state_name == "patrol", f"超时后应回巡逻，实际 {e.state_name}"
    assert e._search_steps is None
    print("[OK] 搜查超预算后放弃并回巡逻")


def test_demo_map_smoke():
    """真实关卡上跑一遍：规划得出来、不抛异常，且大部分哨兵都有得搜。"""
    from sneak_ai.core.world import build_spawn_and_patrols

    grid = build_demo_map()
    _, cfg = build_spawn_and_patrols(grid)

    with_spots = 0
    for (x, y, _patrol) in cfg:
        spots = find_hiding_spots(grid, (x, y))
        if not spots:
            continue
        with_spots += 1
        steps = build_search_plan((x, y), (int(x) + 3, int(y)), spots)
        assert steps and steps[0][0] == "move"
        assert [k for k, _ in steps].count("sweep") == len(spots) + 1

    # 没有藏身点时也必须给出"只看一眼"的计划，不能返回空（否则 AI 卡死在原地）
    for (x, y, _patrol) in cfg:
        steps = build_search_plan((x, y), (int(x) + 3, int(y)), [])
        assert steps, "没有藏身点时也要有兜底计划"

    assert with_spots >= len(cfg) // 3, (
        f"演示地图上只有 {with_spots}/{len(cfg)} 名哨兵能找到藏身点，"
        "藏身点判据可能过严，搜查计划会退化成原地看一眼"
    )
    print(f"[OK] 演示地图冒烟：{with_spots}/{len(cfg)} 名哨兵有可搜查的藏身点")


def main():
    test_goap_add_delete_lists()
    test_planner_minimises_travel()
    test_each_point_moved_then_swept()
    test_hiding_spots_are_occluded_and_walled()
    test_open_room_has_no_hiding_spots()
    test_enemy_builds_and_executes_plan()
    test_plan_cleared_when_regaining_and_losing_sight()
    test_unreachable_point_is_skipped()
    test_gives_up_after_budget()
    test_demo_map_smoke()
    print("\nW15 全部通过 ✅")


if __name__ == "__main__":
    main()
