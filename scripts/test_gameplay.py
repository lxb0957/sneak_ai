"""通关玩法逻辑测试：不启动图形窗口，验证状态机、目标点、捕获判定。

用法：
    python scripts/test_gameplay.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sneak_ai import constants as C
from sneak_ai.core.world import World, build_demo_map, build_spawn_and_patrols
from sneak_ai.ai.coordination import Coordinator
from sneak_ai.entity.enemy import Enemy
from sneak_ai.entity.player import Player
from sneak_ai.perception.perception import Perception


def make_world():
    w = World(build_demo_map())
    w.coordinator = Coordinator(w.grid)
    player_spawn, enemy_cfg = build_spawn_and_patrols()
    w.player = Player(*player_spawn)
    w.enemies = [Enemy(x, y, patrol=pts, perception=Perception())
                 for (x, y, pts) in enemy_cfg]
    return w


def test_infiltrate_to_data():
    w = make_world()
    assert w.state == C.STATE_INFILTRATE
    # 直接把玩家放到数据终端旁，触发接触
    w.player.x, w.player.y = C.DATA_TERMINAL
    w.update(1 / 60.0)
    assert w.state == C.STATE_EXFILTRATE, "接触数据终端应进入逃脱阶段"
    assert w.has_data is True
    assert w.alert is True
    assert all(e.alerted for e in w.enemies), "拿数据后全图敌人应警觉"
    print("[OK] 潜入→拿数据→警觉 状态转移正确")


def test_exfil_to_victory():
    w = make_world()
    w.player.x, w.player.y = C.DATA_TERMINAL
    w.update(1 / 60.0)  # 先拿数据
    assert w.state == C.STATE_EXFILTRATE
    # 移动到撤离点
    w.player.x, w.player.y = C.EXFIL_POINT
    w.update(1 / 60.0)
    assert w.state == C.STATE_VICTORY, "抵达撤离点应胜利"
    print("[OK] 逃脱→撤离→胜利 状态转移正确")


def test_capture_defeat():
    w = make_world()
    # 把一个敌人贴到玩家脸上
    w.player.x, w.player.y = 5.5, 5.5
    w.enemies[0].x, w.enemies[0].y = 5.5, 5.5
    w.update(1 / 60.0)
    assert w.state == C.STATE_DEFEAT, "敌人贴近应失败"
    assert w.captured is True
    print("[OK] 捕获判定正确")


def test_alert_speed_and_view():
    w = make_world()
    e = w.enemies[0]
    base_speed = e.speed_chase
    base_range = e.perception.view_range
    assert not e.alerted
    # 触发警觉
    e.alerted = True
    assert e._chase_speed() > base_speed, "警觉追击应更快"
    # 视野扩大逻辑（在 _update_senses 里临时改 view_range）
    assert C.ALERT_VIEW_RANGE > base_range, "警觉视野应更远"
    print("[OK] 警觉速度/视野增强配置正确")


def test_frozen_in_end_state():
    w = make_world()
    w.state = C.STATE_VICTORY
    before = w.elapsed
    w.update(1 / 60.0)
    assert w.elapsed == before, "结算状态应冻结世界更新"
    print("[OK] 结算状态冻结世界更新正确")


if __name__ == "__main__":
    test_infiltrate_to_data()
    test_exfil_to_victory()
    test_capture_defeat()
    test_alert_speed_and_view()
    test_frozen_in_end_state()
    print("\n全部通关玩法测试通过 ✅")
