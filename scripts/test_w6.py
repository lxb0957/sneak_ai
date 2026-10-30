"""W6 测试：调查行为、局部报警广播、体力/噪音、计分记录、渲染冒烟。

用法：python scripts/test_w6.py
（无窗口环境也能跑：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy）
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.ai.behavior_tree import Selector  # noqa: E402
from sneak_ai.ai.coordination import Coordinator, Role  # noqa: E402
from sneak_ai.core import records  # noqa: E402
from sneak_ai.core.grid import GridMap  # noqa: E402
from sneak_ai.core.world import World  # noqa: E402
from sneak_ai.entity.enemy import Enemy  # noqa: E402
from sneak_ai.entity.player import Player  # noqa: E402
from sneak_ai.perception.perception import Perception  # noqa: E402


def make_world(w=44, h=30, player=(10.5, 10.5)):
    """空地图世界：AI 行为测试不该被演示地图的墙影响。"""
    grid = GridMap.empty(w, h)
    world = World(grid)
    world.coordinator = Coordinator(grid)
    world.player = Player(*player)
    return world


def tick(world, seconds=1.0, dt=1 / 60.0):
    states = set()
    for _ in range(int(seconds / dt)):
        world.update(dt)
        for e in world.enemies:
            states.add(e.state_name)
    return states


# ---------------------------------------------------------------- AI
def test_behavior_tree_drives_enemy():
    world = make_world()
    e = Enemy(10.5, 12.5, perception=Perception())
    world.enemies = [e]
    assert isinstance(e._tree, Selector), "敌人决策应由行为树驱动"
    tick(world, 0.2)
    assert e.state_name == "patrol", f"无人时应在巡逻，实际 {e.state_name}"
    print("[OK] 敌人决策接入行为树（Selector: engage/investigate/patrol）")


def test_investigate_last_known_position():
    """看到玩家 → 玩家消失 → 应前往"最后已知位置"调查，而不是立刻忘记。"""
    world = make_world()
    e = Enemy(10.5, 10.5, perception=Perception())
    e.facing = 0.0  # 朝右
    world.enemies = [e]

    # 玩家出现在右侧 4 格处，被看到
    world.player.x, world.player.y = 14.5, 10.5
    # 4 格处可见置信度约 0.38，按 SUSPICION_GAIN=1.0 每秒涨 0.38，
    # 到确认阈值 0.70 需要约 1.9 秒 —— "要盯一会儿才确认"本身是设计意图。
    # 注意：一旦确认就必须立刻把玩家挪走，否则敌人会在 2 秒内追到玩家身上
    # 触发"被抓"，世界冻结、后续感知不再更新，测试就会拿到过期的 sees_player。
    for _ in range(int(3.0 / (1 / 60))):
        world.update(1 / 60)
        if e.suspicion >= C.SUSPICION_CONFIRM:
            break
    assert world.state != C.STATE_DEFEAT, "测试场景出错：玩家被抓住了"
    assert e.suspicion >= C.SUSPICION_CONFIRM, f"应确认发现，suspicion={e.suspicion:.2f}"
    # 协同角色由 world 在下一帧统一分配（先分配、再让敌人执行），
    # 所以这里要再走一帧才会拿到 CHASE 角色。
    world.update(1 / 60)
    assert e.role != Role.PATROL, "确认发现后应拿到协同角色"

    # 玩家瞬移到远处（脱离视线与听觉）
    world.player.x, world.player.y = 38.0, 25.0
    states = tick(world, 6.0)
    assert "investigate" in states, f"跟丢后应进入调查状态，实际出现 {states}"
    assert e.x > 11.5, f"应朝最后已知位置移动，实际 x={e.x:.2f}"

    # 调查超时后会放弃，回到巡逻（不会永远卡在调查里）
    assert e.state_name in ("patrol", "investigate")
    print("[OK] 跟丢后前往最后已知位置调查")


def test_investigate_gives_up():
    world = make_world()
    # 玩家站远点：否则和敌人重合会直接触发"被抓住"，世界冻结、测不出东西
    world.player.x, world.player.y = 35.5, 25.5
    e = Enemy(10.5, 10.5, perception=Perception())
    world.enemies = [e]
    e.suspicion = C.SUSPICION_CURIOUS + 0.1
    e._last_seen = (10.5, 10.5)  # 就在脚下 → 直接进入环视
    # 环视 LOOK_AROUND_TIME 秒后放弃
    tick(world, C.LOOK_AROUND_TIME + 1.0)
    assert e._last_seen is None, "调查超时应清空记忆"
    assert e.state_name == "patrol", f"放弃后应回巡逻，实际 {e.state_name}"
    print("[OK] 调查超时会放弃并回到巡逻")


def test_only_engaged_enemies_get_roles():
    """一个敌人看到你，不该让全图敌人同时精确追击。"""
    world = make_world()
    a = Enemy(10.5, 10.5, perception=Perception())
    a.facing = 0.0
    far = Enemy(38.0, 27.0, perception=Perception())
    far.facing = 3.14
    world.enemies = [a, far]

    world.player.x, world.player.y = 13.0, 10.5
    # 2.5 格处可见置信度约 0.62，按 GAIN=1.0 需要约 1.15 秒才确认（原来 0.8 秒）
    tick(world, 1.4)
    assert a.role != Role.PATROL, "目击者应进入追击"
    assert world.engaged == 1, f"参与人数应为 1，实际 {world.engaged}"
    assert far.role == Role.PATROL, "远处的哨兵应继续巡逻"
    assert far.suspicion < C.SUSPICION_CURIOUS, "远处哨兵不该被凭空通知"
    print("[OK] 只有确认发现的敌人才被分配协同角色")


def test_radio_broadcast_is_local():
    """报警只在 RADIO_RANGE 内传播，且只抬到"起疑"，不是全图精确追击。"""
    world = make_world()
    witness = Enemy(10.5, 10.5, perception=Perception())
    witness.facing = 0.0
    near = Enemy(10.5, 17.5, perception=Perception())
    near.facing = 0.0  # 背对玩家，看不到
    far = Enemy(38.0, 27.0, perception=Perception())
    far.facing = 0.0
    world.enemies = [witness, near, far]

    world.player.x, world.player.y = 13.5, 10.5
    tick(world, 1.5)
    assert witness.suspicion >= C.SUSPICION_CONFIRM
    assert near.suspicion >= C.SUSPICION_CURIOUS, "附近队友应被喊来查看"
    assert near.suspicion < C.SUSPICION_CONFIRM, "队友只该起疑，不该直接锁定"
    assert far.suspicion == 0.0, "超出广播范围的哨兵不该有任何反应"
    print("[OK] 报警广播是局部且分级的（起疑 ≠ 锁定）")


# ---------------------------------------------------------------- 玩家手感
def test_sprint_and_stamina():
    world = make_world()
    p = world.player
    p._keys = {pygame.K_d, pygame.K_SPACE}
    p.update(1 / 60, world)
    assert p.sprinting and p.speed == C.PLAYER_SPRINT_SPEED
    assert p.noise == C.NOISE_SPRINT, "冲刺噪音应最大"

    start = p.stamina
    for _ in range(60):
        p.update(1 / 60, world)
    assert p.stamina < start, "冲刺应消耗体力"

    p._keys = {pygame.K_d}
    for _ in range(120):
        p.update(1 / 60, world)
    assert p.stamina > 0 and not p.sprinting, "松开后应恢复体力"

    # 体力耗尽后按住 Space 也跑不动
    p.stamina = 0.0
    p._keys = {pygame.K_d, pygame.K_SPACE}
    p.update(1 / 60, world)
    assert not p.sprinting, "体力耗尽时不应能冲刺"
    print("[OK] 冲刺消耗体力 / 体力耗尽无法起跑 / 松开恢复")


def test_noise_levels_affect_hearing():
    p = Perception(hear_range=5.0)
    eye = (0.0, 0.0)
    target = (1.5, 0.0)  # 贴脸
    sneak = p.can_hear(eye, target, C.NOISE_SNEAK)
    walk = p.can_hear(eye, target, C.NOISE_WALK)
    sprint = p.can_hear(eye, target, C.NOISE_SPRINT)
    assert sneak < walk < sprint, f"听觉强度应随噪音递增: {sneak:.2f}/{walk:.2f}/{sprint:.2f}"
    assert p.can_hear(eye, (30.0, 0.0), C.NOISE_SPRINT) == 0.0, "太远应听不见"
    assert p.can_hear(eye, target, C.NOISE_STILL) == 0.0, "站着不动应无声"
    print("[OK] 噪音分级影响听觉半径与强度")


def test_alerted_view_range_without_mutating_shared_perception():
    """警觉状态扩大视野，但不能改共享 Perception 的字段（会污染其他敌人）。"""
    world = make_world()
    shared = Perception()
    e = Enemy(10.5, 10.5, perception=shared)
    e.alerted = True
    world.enemies = [e]
    base = shared.view_range
    tick(world, 0.2)
    assert e.view_range() == C.ALERT_VIEW_RANGE
    assert shared.view_range == base, "共享 Perception 的 view_range 不应被改写"
    print("[OK] 警觉视野按实例生效，不污染共享感知参数")


# ---------------------------------------------------------------- 计分/记录
def test_score_and_records():
    world = make_world()
    # W14 起：只有通关局才有分（失败局 = 0），所以这里必须显式置成通关态。
    # 也要把风格分的前置条件摆平：默认世界"零击倒"就会吃到 +80，
    # 这条断言本来就是只验基础分公式的。
    world.state = C.STATE_VICTORY
    world.peak_threat = 1.0
    world.kills = 0
    world.elapsed = 30.0
    world.spotted = 1
    expected = int(C.SCORE_BASE - 30 * C.SCORE_TIME_PENALTY
                   - C.SCORE_SPOTTED_PENALTY + C.SCORE_STYLE_NO_KILL)
    assert world.score() == expected, f"计分应为 {expected}，实际 {world.score()}"
    # 评级门槛随新地图重算（RANK_S=75 / RANK_A=115），90 秒落在 A 档
    world.elapsed = 90.0
    assert world.rank() == "A"

    with tempfile.TemporaryDirectory() as tmp:
        records.RECORD_FILE = Path(tmp) / "records.json"
        try:
            assert records.submit({"score": 500, "time": 30, "rank": "S"}) is True
            assert records.submit({"score": 400}) is False, "低分不应覆盖记录"
            assert records.submit({"score": 900}) is True
            assert records.load()["score"] == 900
        finally:
            records.RECORD_FILE = None
    print("[OK] 计分 / 评级 / 最佳记录持久化正确")


# ---------------------------------------------------------------- 渲染
def test_render_smoke():
    pygame.init()
    from sneak_ai.__main__ import Game

    g = Game()
    # 强制进入"被发现"状态，把 HUD/红光/箭头/图标全部画一遍
    for e in g.world.enemies[:6]:
        e.suspicion = 1.0
        e.role = Role.CHASE
        e.state_name = "chase"
    g.world.engaged = 6
    g.world.seeing = 2
    g.world.threat = 1.0
    g.world.spotted = 1
    for _ in range(3):
        g._render(1 / 60)
    # 结算页也要能画（含最佳记录 / 新纪录标记）
    g.world.state = C.STATE_VICTORY
    g.world.elapsed = 42.0
    g._render(1 / 60)
    assert g.screen.get_at((0, 0)) is not None
    pygame.quit()
    print("[OK] 渲染冒烟（HUD / 小地图 / 视野锥 / 结算页）通过")


def test_mapview_is_prerendered():
    """地图必须是预渲染的一次性 Surface，而不是每帧重画 4800 个格子。"""
    pygame.init()
    pygame.display.set_mode((C.WINDOW_W, C.WINDOW_H))
    from sneak_ai.core.mapview import MapView
    from sneak_ai.core.world import build_demo_map

    mv = MapView(build_demo_map())
    assert mv.surface.get_size() == (C.GRID_W * C.TILE_SIZE, C.GRID_H * C.TILE_SIZE)
    pygame.quit()
    print("[OK] 地图预渲染为整张 Surface（每帧只 blit 可视区域）")


if __name__ == "__main__":
    test_behavior_tree_drives_enemy()
    test_investigate_last_known_position()
    test_investigate_gives_up()
    test_only_engaged_enemies_get_roles()
    test_radio_broadcast_is_local()
    test_sprint_and_stamina()
    test_noise_levels_affect_hearing()
    test_alerted_view_range_without_mutating_shared_perception()
    test_score_and_records()
    test_mapview_is_prerendered()
    test_render_smoke()
    print("\nW6 全部测试通过 ✅")
