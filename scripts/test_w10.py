"""W10 测试：关卡拓扑 + 消音手枪 + 敌人重生。

这组测试守的是**设计约束**，不是"功能存在"：

关卡
1. 路线数（最小割）必须很小 —— 否则"很少的路径选择"只是嘴上说说；
2. 每道门只有 2 格宽；
3. 巡逻点不能贴着门（贴着门 = 把门永久封死，实测机器人会撞死在门口）。

武器
4. 命中只认"直线没有被墙挡住"——不能隔墙打死人；
   而且**点画面上的身体就得命中**：判定点（脚底）和精灵（画在脚底上方）必须一致，
   否则玩家会得到"这枪打不死人"的体感；
5. 弹药有限且不能连发；
6. 枪声会引来 9.5 格内的哨兵，但**不会**直接把他们抬到"确认发现"；
   听到 ≠ 看到，否则一枪等于给自己画了个全图红点。

重生
7. 尸体不感知、不抓人、不参与协同；
8. 满 5 秒在"自己巡逻网里离玩家最远的点"复活，不是原地，也不是玩家脸上；
9. 复活点如果实在离玩家太近，必须给一段"苏醒"延迟。

开枪的代价（W10.1）
10. 尸体被同僚看见必须拉警报 —— 但要看得见才算数（背对 / 隔墙不触发），
    而且一具尸体只报一次；这是"杀人零代价"这个漏洞的补丁；
11. 命中必须有记号，打墙不能有 —— 否则混战里玩家分不清打中人还是打中墙。

用法：
    python scripts/test_w10.py
"""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sneak_ai import constants as C
from sneak_ai.ai.coordination import Role
from sneak_ai.core.grid import GridMap
from sneak_ai.core.world import (
    DOORS,
    World,
    build_demo_map,
    build_spawn_and_patrols,
)
from sneak_ai.entity.enemy import Enemy
from sneak_ai.entity.player import Player
from sneak_ai.perception.perception import Perception

DT = 1 / 60


# ---------------------------------------------------------------- 工具
def make_arena(size=32):
    grid = GridMap.empty(size, size)
    for i in range(size):
        grid.set_wall(i, 0)
        grid.set_wall(i, size - 1)
        grid.set_wall(0, i)
        grid.set_wall(size - 1, i)
    w = World(grid)
    w.player = Player(8.5, 10.5)
    return w


def add_guard(w, x, y, facing=0.0, patrol=None):
    e = Enemy(x, y, patrol=patrol or [], perception=Perception())
    e.facing = facing
    w.enemies.append(e)
    return e


def tick(w, seconds):
    for _ in range(int(seconds * 60)):
        w.update(DT)


# ---------------------------------------------------------------- 关卡拓扑
def test_route_count_is_small():
    """路线数（最小割）必须 ≤ 4：这是"路径选择很少"的硬指标。"""
    from audit_routes import min_cut_size

    grid = build_demo_map()
    s = (int(C.EXFIL_POINT[0]), int(C.EXFIL_POINT[1]))
    t = (int(C.DATA_TERMINAL[0]), int(C.DATA_TERMINAL[1]))
    cut = min_cut_size(grid, s, t)
    assert cut <= 4, f"路线太多（最小割 {cut} 格），玩家可以四处乱绕"
    print(f"[OK] 路线数（最小割）{cut} 格 —— 想切断关卡只需堵住 {cut} 格")


def test_doors_are_two_cells_wide():
    """门必须统一 2 格宽：1 格太憋屈，3 格以上就失去了"一次只过一个人"的意义。"""
    grid = build_demo_map()
    widths = []
    for d in DOORS:
        cells = [(x, y) for y in range(d[1], d[3] + 1)
                 for x in range(d[0], d[2] + 1)]
        assert all(not grid.is_wall(*c) for c in cells), f"{d[4]} 没挖开"
        widths.append(len(cells))
    assert set(widths) == {2}, f"门宽不统一：{sorted(set(widths))}"
    print(f"[OK] {len(DOORS)} 道门全部 2 格宽")


def test_no_patrol_point_blocks_a_door():
    """巡逻点（含出生点）离任何门都至少要 3 格。

    这条规则是踩出来的：最初我把"每个门后都有人看"实现成
    "哨兵贴着门来回走"，2 格宽的门就被永久封死 —— 机器人 8 秒撞死在门口。
    门后的人该做的是"扫过去"，不是"站在门里"。
    """
    grid = build_demo_map()
    _spawn, cfg = build_spawn_and_patrols(grid)
    door_cells = [(x + 0.5, y + 0.5, d[4])
                  for d in DOORS
                  for y in range(d[1], d[3] + 1)
                  for x in range(d[0], d[2] + 1)]
    bad = []
    for i, (x, y, pts) in enumerate(cfg):
        for tag, p in [("出生点", (x, y))] + [("巡逻点", q) for q in pts]:
            near = min(door_cells, key=lambda c: math.hypot(c[0] - p[0], c[1] - p[1]))
            dist = math.hypot(near[0] - p[0], near[1] - p[1])
            if dist < 3.0:
                bad.append((i, tag, (round(p[0], 1), round(p[1], 1)),
                            near[2], round(dist, 1)))
    assert not bad, f"这些哨兵会堵门：{bad}"
    print(f"[OK] {len(cfg)} 个哨兵的站位全部离门 ≥3 格（不堵门）")


def test_enemy_count_and_walkable_patrols():
    grid = build_demo_map()
    spawn, cfg = build_spawn_and_patrols(grid)
    # 下限只是防误删，**不是难度指标**。这条断言原来是 `>= 20`
    # （W10 那一版的目标是"把敌人从 12 加到 21，难度就上来了"）——
    # v1.7 的数据把它推翻了：第 1 关从 21 个删到 17 个，12 相位胜率
    # 反而从 25% 涨到 92%，因为删掉的是三处"钉死在 3 格宽窄廊里"的哨兵。
    # 能不能过，取决于哨兵站在哪、要走多长，不取决于有几个。
    assert len(cfg) >= 15, f"哨兵只剩 {len(cfg)} 个，是不是删多了"
    assert not grid.is_wall(int(spawn[0]), int(spawn[1]))
    for i, (x, y, pts) in enumerate(cfg):
        assert not grid.is_wall(int(x), int(y)), f"#{i} 出生在墙里"
        for px, py in pts:
            assert not grid.is_wall(int(px), int(py)), f"#{i} 的巡逻点在墙里"
    print(f"[OK] {len(cfg)} 个哨兵，出生点与巡逻点全部在可走格上")


# ---------------------------------------------------------------- 射击
def test_shot_kills_in_line_of_sight():
    w = make_arena()
    guard = add_guard(w, w.player.x + 6, w.player.y, facing=math.pi)  # 背对玩家
    assert w.shoot((guard.x, guard.y)) == "enemy"
    assert not guard.alive, "命中后哨兵应该倒地"
    assert w.player.ammo == C.WEAPON_AMMO - 1
    assert w.kills == 1
    print(f"[OK] 直线射击命中：哨兵倒地，弹药 {C.WEAPON_AMMO} → {w.player.ammo}")


def test_clicking_the_body_hits():
    """点"看见的身体"必须命中，而不是只能点脚底。

    这是玩家实测报上来的 bug：命中判定点在脚底 (e.x, e.y)，
    但精灵是脚底对齐往上画的（48px ≈ 1.5 格），身体中心在脚底上方 0.7 格。
    于是玩家瞄着眼睛看到的那个人 → 射线从头顶飞过 → 打在背后的墙上。
    表现就是"这把枪永远打不死人"，而单测（点脚底）却是全绿的。

    这条测试点的是**身体中心**和**头部**两个位置。
    """
    w = make_arena()
    guard = add_guard(w, w.player.x + 6, w.player.y, facing=math.pi)
    for label, dy in (("躯干", C.WEAPON_AIM_OFFSET), ("头部", 1.3)):
        point = (guard.x, guard.y - dy)
        assert w.aim_target(point) is guard, f"点{label}应该识别成这个哨兵"
        assert w.shoot(point) == "enemy", f"点{label}应该命中"
        assert not guard.alive, f"点{label}之后哨兵应该倒地"
        guard.alive, guard.dead_timer, guard.grace = True, 0.0, 0.0
        w.player.ammo, w.player.fire_cd = C.WEAPON_AMMO, 0.0
    print("[OK] 击中判定与画面一致：点躯干 / 点头部都能打倒（不再只认脚底）")


def test_aim_snap_never_shoots_through_walls():
    """瞄准吸附只解决"点的是谁"，不解决"能不能打到" —— 隔墙点人依然是墙。"""
    w = make_arena()
    guard = add_guard(w, w.player.x + 6, w.player.y)
    for y in range(32):
        w.grid.set_wall(int(w.player.x) + 3, y)
    body = (guard.x, guard.y - C.WEAPON_AIM_OFFSET)
    assert w.aim_target(body) is None, "隔着墙不该吸附成目标"
    assert w.shoot(body) == "wall"
    assert guard.alive
    print("[OK] 瞄准吸附不豁免遮挡：隔墙点人仍然打墙")


def test_shot_blocked_by_wall():
    w = make_arena()
    guard = add_guard(w, w.player.x + 6, w.player.y)
    for y in range(32):                     # 中间砌一整堵竖墙
        w.grid.set_wall(int(w.player.x) + 3, y)
    assert w.shoot((guard.x, guard.y)) == "wall"
    assert guard.alive, "隔着墙不应该打死人"
    print("[OK] 墙后的哨兵打不到（hitscan 被墙截断）")


def test_shot_out_of_range():
    w = make_arena(40)
    w.player.x, w.player.y = 5.5, 20.5
    guard = add_guard(w, 5.5 + C.WEAPON_RANGE + 3, 21.5)   # 偏一点，避免刚好在射程边界
    assert w.shoot((guard.x, guard.y)) == "max"
    assert guard.alive, "超出射程不该命中"
    print(f"[OK] 射程 {C.WEAPON_RANGE:.0f} 格外打不到")


def test_ammo_is_limited_and_has_cooldown():
    w = make_arena()
    add_guard(w, 20.5, 10.5, facing=math.pi)
    assert w.shoot((20.5, 10.5)) != "empty"
    assert w.shoot((20.5, 10.5)) == "empty", "冷却中不能再开一枪"
    tick(w, C.WEAPON_COOLDOWN + 0.05)

    fired = 1
    while w.player.ammo > 0:
        before = w.player.ammo
        w.shoot((8.5, 2.5))                 # 朝天上打，不指望命中
        if w.player.ammo < before:
            fired += 1
        tick(w, C.WEAPON_COOLDOWN + 0.02)
    assert fired == C.WEAPON_AMMO, f"应能打出 {C.WEAPON_AMMO} 发，实际 {fired}"
    assert w.shoot((8.5, 2.5)) == "empty"
    assert "gun_empty" in w.events
    print(f"[OK] 弹药 {C.WEAPON_AMMO} 发 + {C.WEAPON_COOLDOWN}s 冷却，打空后空仓")


def test_gunshot_raises_suspicion_but_not_confirmed():
    """枪声是"响"，不是"看见"：只能把附近哨兵抬到起疑，不能直接锁定。"""
    w = make_arena()
    # 放在枪声半径内、但是背对（看不见玩家）
    gy = 10.5 + C.WEAPON_NOISE_RADIUS - 1.5
    far = add_guard(w, 8.5, gy, facing=math.pi / 2)
    w.shoot((8.5, 4.5))                                  # 朝北空放一枪
    tick(w, 0.2)
    assert far.suspicion > 0, f"{C.WEAPON_NOISE_RADIUS:.0f} 格内的哨兵应该被枪声惊动"
    assert far.suspicion < C.SUSPICION_CONFIRM, (
        f"枪声不能直接确认发现：{far.suspicion:.2f}"
    )
    assert far.role == Role.PATROL, "枪声不应让哨兵立刻进入协同追击"
    print(f"[OK] 枪声只抬到 {far.suspicion:.2f} < 确认阈值 {C.SUSPICION_CONFIRM}")


def test_gunshot_has_finite_radius():
    w = make_arena(48)
    w.player.x, w.player.y = 5.5, 40.5
    far = add_guard(w, 45.5, 5.5)      # 约 40 格，远超枪声半径
    w.shoot((5.5, 30.5))
    tick(w, 0.2)
    assert far.suspicion == 0.0, "枪声半径外的哨兵不该有反应"
    print(f"[OK] 枪声半径 {C.WEAPON_NOISE_RADIUS:.0f} 格外无影响")


# ---------------------------------------------------------------- 死亡与重生
def test_corpse_is_not_a_threat():
    """尸体必须彻底退出玩法：不感知、不抓人、不占协同名额。"""
    w = make_arena()
    guard = add_guard(w, 20.5, 10.5, facing=math.pi)
    w.shoot((guard.x, guard.y))
    assert not guard.alive

    # 把玩家直接放到尸体上：既不该被抓，也不该被"看见"
    w.player.x, w.player.y = guard.x, guard.y
    tick(w, 0.5)
    assert w.state != C.STATE_DEFEAT, "尸体不能抓人"
    assert not guard.sees_player
    assert w.engaged == 0, "尸体不应该被分配协同角色"
    assert w.threat == 0.0
    print("[OK] 尸体不感知、不抓人、不参与协同")


def test_respawn_after_five_seconds():
    w = make_arena()
    guard = add_guard(w, 20.5, 10.5, facing=math.pi,
                      patrol=[(20.5, 10.5), (26.5, 24.5)])
    w.shoot((guard.x, guard.y))
    assert not guard.alive

    tick(w, C.ENEMY_RESPAWN - 0.3)
    assert not guard.alive, f"{C.ENEMY_RESPAWN} 秒没到就复活了"
    assert guard.respawn_at is not None, "复活前应有一段预警期"
    tick(w, 0.5)
    assert guard.alive, "到点必须复活"
    assert guard.role == Role.PATROL and guard.suspicion == 0.0
    assert not w.grid.is_wall(int(guard.x), int(guard.y)), "不能复活在墙里"
    print(f"[OK] {C.ENEMY_RESPAWN:.0f} 秒后复活，状态重置为巡逻")


def test_respawn_picks_farthest_patrol_point():
    """复活点 = 自己巡逻网里离玩家最远的点：别在玩家脸上刷人。"""
    w = make_arena(40)
    w.player.x, w.player.y = 5.5, 5.5
    near = (8.5, 8.5)
    far = (34.5, 34.5)
    guard = add_guard(w, near[0], near[1], facing=math.pi / 2,
                      patrol=[near, far])
    w.shoot((guard.x, guard.y))
    tick(w, C.ENEMY_RESPAWN + 0.1)
    assert guard.alive
    assert math.hypot(guard.x - far[0], guard.y - far[1]) < 1.0, (
        f"应复活在离玩家最远的巡逻点 {far}，实际 ({guard.x:.1f},{guard.y:.1f})"
    )
    print("[OK] 复活点选在离玩家最远的巡逻点")


def test_respawn_near_player_grants_grace():
    """所有巡逻点都离玩家很近时，必须给一段苏醒延迟，不能立刻抓人。"""
    w = make_arena()
    w.player.x, w.player.y = 20.5, 10.5
    # 两个巡逻点都在 3 格内：无论选哪个都在玩家眼前
    guard = add_guard(w, 22.5, 10.5, facing=math.pi,
                      patrol=[(22.5, 10.5), (21.5, 11.5)])
    w.shoot((guard.x, guard.y))
    # 玩家守尸：复活点必然在眼前
    tick(w, C.ENEMY_RESPAWN + 0.05)
    assert guard.alive
    assert guard.grace > 0.0, "贴脸复活必须有苏醒延迟"
    assert not guard.sees_player, "苏醒期内不应该感知玩家"
    print(f"[OK] 贴脸复活给 {C.RESPAWN_GRACE:.1f}s 苏醒延迟")


# ---------------------------------------------------------------- 尸体目击
def test_body_seen_triggers_alarm():
    """尸体被同僚看见 → 拉警报：可疑度抬到起疑以上，并且**原地**警戒。

    这条机制补的是"杀人零代价"的漏洞：没有它，开枪的代价只有扣分，
    玩家可以站在尸体旁边慢慢数钱。

    为什么特意断言"人不许移动"：最初的做法是"目击者跑去看现场"，
    结果在门控关卡里必然变成堵门（玩家杀人的位置几乎总是一道门），
    带枪玩法 3 局全灭。看见同事的尸体却傻乎乎走过去，也不是安保该做的事 ——
    他该做的是警觉起来、原地张望、用无线电通报。
    """
    w = make_arena(40)
    w.player.x, w.player.y = 5.5, 5.5
    victim = add_guard(w, 20.5, 10.5, facing=0.0)
    witness = add_guard(w, 20.5, 16.5, facing=-math.pi / 2)  # 正对尸体（朝上）
    wx0, wy0 = witness.x, witness.y

    victim.kill()
    assert w.bodies_found == 0, "刚倒下、还没人看见"
    # 扫描在第一帧就会跑（_body_scan_cd 初值 0），推一帧读到的是警报峰值
    w.update(DT)

    assert w.bodies_found == 1, "同僚看见了尸体，应该记一次暴露"
    assert witness.suspicion >= C.BODY_SPOT_SUSPICION - 0.02, (
        f"目击者可疑度应被抬到 {C.BODY_SPOT_SUSPICION} 附近，实际 {witness.suspicion:.2f}"
    )
    assert witness.suspicion < C.SUSPICION_CONFIRM, "看到尸体不等于看到入侵者"
    assert witness.body_alert > 0.0, "目击者应该进入警戒期"

    # 警戒期内可疑度不许掉回安全区，但人也不许挪窝
    tick(w, 4.0)
    assert witness.suspicion >= C.SUSPICION_CURIOUS, (
        f"警戒期内可疑度不许掉回安全区，实际 {witness.suspicion:.2f}"
    )
    moved = math.hypot(witness.x - wx0, witness.y - wy0)
    assert moved < 0.5, f"目击者只能原地警戒，不许跑去看现场（移动了 {moved:.1f} 格）"
    print("[OK] 尸体被目击 → 原地警戒（可疑度托底 12s，不移动、不堵门）")


def test_body_alert_expires():
    """警戒是会过去的：否则一次开枪就永久拉高全场的暴露度。"""
    w = make_arena(40)
    w.player.x, w.player.y = 5.5, 5.5
    victim = add_guard(w, 20.5, 10.5)
    witness = add_guard(w, 20.5, 16.5, facing=-math.pi / 2)
    victim.kill()
    w.update(DT)
    assert witness.body_alert > 0.0
    # 尸体 5 秒就复活了，这里直接把警戒计时推到过期，避免与重生逻辑纠缠
    witness.body_alert = 0.01
    tick(w, 3.0)
    assert witness.body_alert == 0.0
    assert witness.suspicion < C.SUSPICION_CURIOUS, (
        f"警戒过期后可疑度应该自然衰减，实际 {witness.suspicion:.2f}"
    )
    print("[OK] 警戒期结束 → 可疑度自然衰减，不会永久警觉")


def test_body_behind_guard_is_not_seen():
    """背对尸体、或者隔着墙，都不该触发警报 —— 这不是全图透视。"""
    # 背对：目击者朝下（+y），尸体在他背后
    w = make_arena(40)
    w.player.x, w.player.y = 5.5, 5.5
    victim = add_guard(w, 20.5, 10.5)
    add_guard(w, 20.5, 16.5, facing=math.pi / 2)
    victim.kill()
    tick(w, C.BODY_SCAN_INTERVAL + 0.05)
    assert w.bodies_found == 0, "背对着尸体不该看见"

    # 隔墙：中间砌一道墙
    w2 = make_arena(40)
    w2.player.x, w2.player.y = 5.5, 5.5
    for y in range(40):
        w2.grid.set_wall(20, y)
    v2 = add_guard(w2, 20.5, 10.5)
    add_guard(w2, 24.5, 10.5, facing=math.pi)  # 正对尸体，但中间有墙
    v2.kill()
    tick(w2, C.BODY_SCAN_INTERVAL + 0.05)
    assert w2.bodies_found == 0, "隔着墙不该看见尸体"
    print("[OK] 背对 / 隔墙都看不见尸体（不是全图透视）")


def test_body_is_only_reported_once():
    """一具尸体只报一次警：否则尸体会在 5 秒里把全图的人反复拉过来。"""
    w = make_arena(40)
    w.player.x, w.player.y = 5.5, 5.5
    victim = add_guard(w, 20.5, 10.5)
    add_guard(w, 20.5, 15.5, facing=-math.pi / 2)
    victim.kill()
    tick(w, 2.0)
    assert w.bodies_found == 1, f"一具尸体应该只报 1 次，实际 {w.bodies_found}"
    print("[OK] 同一具尸体只触发一次警报")


def test_hitmarker_marks_the_hit():
    """命中必须有肉眼可见的记号，而且它自己会消失（不然会糊满屏幕）。"""
    w = make_arena()
    guard = add_guard(w, 8.5, 6.5, facing=math.pi / 2)
    kind = w.shoot((guard.x, guard.y))
    assert kind == "enemy"
    assert len(w.hit_marks) == 1, "命中必须留下一个记号"
    hm = w.hit_marks[0]
    assert abs(hm["y"] - (guard.y - C.WEAPON_AIM_OFFSET)) < 0.01, (
        "记号要画在身体中心，不是脚底"
    )
    tick(w, C.HITMARK_LIFE + 0.05)
    assert not w.hit_marks, "命中记号必须自己过期"
    print(f"[OK] 命中记号画在身体中心，{C.HITMARK_LIFE:.2f}s 后自动消失")


def test_miss_leaves_no_hitmarker():
    """打墙 / 打空不留记号：否则"打中了"这个信号就一文不值。"""
    w = make_arena()
    add_guard(w, 8.5, 6.5, facing=math.pi / 2)
    kind = w.shoot((0.5, 10.5))  # 朝左平射，撞上地图边框
    assert kind == "wall", kind
    assert not w.hit_marks
    print("[OK] 打墙不留命中记号")


# ---------------------------------------------------------------- 性能
def test_frame_budget_with_21_enemies():
    import time

    grid = build_demo_map()
    w = World(grid)
    w.player = Player(5.5, 5.5)
    _spawn, cfg = build_spawn_and_patrols(grid)
    w.enemies = [Enemy(x, y, patrol=pts, perception=Perception())
                 for (x, y, pts) in cfg]
    for _ in range(30):
        w.update(DT)
    t0 = time.perf_counter()
    n = 300
    for _ in range(n):
        w.update(DT)
    ms = (time.perf_counter() - t0) / n * 1000
    assert ms < 8.0, f"单帧逻辑 {ms:.2f}ms 太慢"
    print(f"[OK] {len(w.enemies)} 个哨兵，单帧逻辑 {ms:.2f}ms -> {1000 / ms:.0f} FPS")


if __name__ == "__main__":
    test_route_count_is_small()
    test_doors_are_two_cells_wide()
    test_no_patrol_point_blocks_a_door()
    test_enemy_count_and_walkable_patrols()
    test_shot_kills_in_line_of_sight()
    test_clicking_the_body_hits()
    test_aim_snap_never_shoots_through_walls()
    test_shot_blocked_by_wall()
    test_shot_out_of_range()
    test_ammo_is_limited_and_has_cooldown()
    test_gunshot_raises_suspicion_but_not_confirmed()
    test_gunshot_has_finite_radius()
    test_corpse_is_not_a_threat()
    test_respawn_after_five_seconds()
    test_respawn_picks_farthest_patrol_point()
    test_respawn_near_player_grants_grace()
    test_body_seen_triggers_alarm()
    test_body_alert_expires()
    test_body_behind_guard_is_not_seen()
    test_body_is_only_reported_once()
    test_hitmarker_marks_the_hit()
    test_miss_leaves_no_hitmarker()
    test_frame_budget_with_21_enemies()
    print("\nW10 关卡拓扑 / 武器 / 重生 测试全部通过 ✅")
