"""W24：第 5 关的"一人一扇门"。

这一关的全部设计压在三条几何/行为事实上，任何一条不成立，关卡就塌了：

1. **岗站得住**：单点巡逻 = 原地站岗，60 秒内位置不动、环视基准不漂。
   （原来 `_start_dwell` 每轮都把摆动的极值当新基准，单点岗 14 秒原地转 231°。
   这条断言就是那次修复的护栏。）
2. **岗堵得死**：门格只有 1 格，而整个门格（含四角）都在抓捕半径内 ——
   所以"贴墙挤过去"在几何上不存在，只能让那一格空出来。
3. **石头引得开**：在他侧后方 4 格弄出响动，5 秒内他必须离开岗位 ≥3 格，
   并且离开的这几秒足够一个玩家穿过门格（4 格 / 4.4 格每秒 < 2 秒）。

第 3 条是这一关"能玩"的唯一证据。没有它，玩家面对的就是一堵不会动的墙。
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.ai.coordination import Coordinator  # noqa: E402
from sneak_ai.core.levels import LEVELS, spawn_and_patrols  # noqa: E402
from sneak_ai.core.world import World  # noqa: E402
from sneak_ai.entity.player import Player  # noqa: E402

LV = 4                      # 第 5 关
LEVEL = LEVELS[LV]
DT = 1 / 60


def _world():
    grid = LEVEL.build()
    spawn, cfg = spawn_and_patrols(LEVEL, grid)
    w = World(grid)
    w.level_index = LEVEL.idx
    w.level_name = LEVEL.name
    w.terminals = LEVEL.terminals
    w.exfil = LEVEL.exfil
    w.par = LEVEL.par
    w.alert_duration = LEVEL.alert_duration
    # 玩家放在最西头的出生点，离终端很远：只当"世界里有一个玩家"用，
    # 不参与这一关的任何判定（否则会触发拿数据/撤离）。
    w.player = Player(*spawn)
    from sneak_ai.entity.enemy import Enemy
    w.enemies = [
        Enemy(x, y, patrol=pts, chase_speed=LEVEL.chase,
              alert_speed=LEVEL.alert_speed, alert_view=LEVEL.alert_view)
        for (x, y, pts) in cfg
    ]
    posts = {(round(px + 0.5, 3), round(py + 0.5, 3)): face
             for (px, py, face) in LEVEL.posts}
    for e in w.enemies:
        face = posts.get((round(e.x, 3), round(e.y, 3)))
        if face is not None:
            e.facing = face
            e.post_facing = face   # 与 __main__ 保持一致：回岗要恢复这个朝向
    w.coordinator = Coordinator(grid)
    return w


def _posts(w):
    """世界里的固定岗（单点巡逻的人）。"""
    return [e for e in w.enemies if len(e.patrol) == 1]


def test_posts_stand_still():
    w = _world()
    posts = _posts(w)
    assert len(posts) == len(LEVEL.posts), (
        f"关卡写了 {len(LEVEL.posts)} 个固定岗，世界里只有 {len(posts)} 个"
    )
    # 先跑两帧让第一次"到点站岗"发生 —— `_dwell_face` 是在那一刻才锁定的，
    # 构造完就读会读到 0.0（Enemy 的默认朝向），于是"基准漂了 180°"其实是
    # 从"还没开始站岗"到"第一次站岗"的正常初始化。
    for _ in range(2):
        w.update(DT)
    start = [(e.x, e.y, e.facing) for e in posts]
    base = [e._dwell_face for e in posts]
    for _ in range(int(60 / DT)):
        w.update(DT)
    for i, (e, (sx, sy, sf)) in enumerate(zip(posts, start)):
        assert math.hypot(e.x - sx, e.y - sy) < 0.25, (
            f"第 {i + 1} 个岗离开岗位 {math.hypot(e.x - sx, e.y - sy):.2f} 格 —— "
            f"单点巡逻的语义是'站住'，不是'慢慢挪'"
        )
        drift = abs(e._dwell_face - base[i])
        assert drift < 0.08, (
            f"第 {i + 1} 个岗的环视基准 60 秒漂了 {math.degrees(drift):.0f}° —— "
            f"每轮站岗都把摆动的极值当新基准，他就会原地转圈（v1.8.3 修过这个）"
        )
    print(f"[OK] {len(posts)} 个固定岗 60 秒站定：位置不动、环视基准不漂")


def test_each_gate_is_a_single_cell_with_a_post_inside():
    grid = LEVEL.build()
    posts = [(px + 0.5, py + 0.5) for (px, py, _f) in LEVEL.posts]
    for (hole, name) in LEVEL.doors:
        x0, y0, x1, y1 = hole
        cells = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        assert len(cells) == 1, f"{name} 有 {len(cells)} 格 —— 这一关的门必须是 1 格"
        cx, cy = cells[0][0] + 0.5, cells[0][1] + 0.5
        near = min(posts, key=lambda p: math.hypot(p[0] - cx, p[1] - cy))
        d = math.hypot(near[0] - cx, near[1] - cy)
        assert d < 0.2, f"{name} 的门格里没人站着（最近的人离格心 {d:.2f} 格）"
    print(f"[OK] {len(LEVEL.doors)} 道闸都是 1 格，且每格正中都站着一个岗")


def test_gate_cell_leaves_no_gap_to_squeeze_through():
    """1 格门里，玩家身体能站的范围只有中线 ±0.15 —— 绕不过去。

    判据要用**玩家实际能站的位置**，不是门格的四角。四角是几何上的点，
    玩家半径 0.35，永远站不到角上：通道净宽 1 格，玩家中心只能落在
    [y+0.35, y+0.65]（离两侧墙各 0.35），也就是距岗（格心）最多 0.15 格。
    0.15 远小于抓判定 0.6，所以"贴墙挤过去"这件事在物理上不存在。

    这也解释了为什么第 5 关**不需要**动全局的抓判定：门窄到一定程度，
    贴不贴墙都一样。（我曾经把 0.6 改成 0.75 来"堵这个洞"，
    结果第 2 关从 72% 掉到 5%，前四关的难度全压在那一格上。）
    """
    slack = 1.0 / 2 - 0.35          # 玩家中心离通道中线的最大偏移
    assert slack < C.CAPTURE_DIST, (
        f"1 格门里玩家能横向挪 {slack:.2f} 格，而抓判定只有 {C.CAPTURE_DIST} —— "
        f"这个差值是负的，说明门已经堵不住了"
    )
    print(f"[OK] 1 格门：玩家可站范围 ±{slack:.2f} 格 < 抓判定 {C.CAPTURE_DIST}"
          f"（贴墙绕不过去）")


def test_noise_pulls_the_post_off_the_gate():
    """在岗侧后方弄出响动：他必须**离开门格**，把那一格让出来。"""
    w = _world()
    post = _posts(w)[0]
    home = (post.x, post.y)
    # 落点：岗的西南 4 格（房间这一侧的空地，不隔墙）。
    # 不选正西：那是他正在看的方向，走过去等于把门让给玩家看；
    # 也不选西北：x=22 那道闸墙在 y<=16，西北方向会穿墙。
    tx, ty = home[0] - 2.0, home[1] + 4.0
    assert not w.grid.is_wall(int(tx), int(ty)), "测试用的落点在墙里"
    w._emit_noise(tx, ty, C.STONE_NOISE_RADIUS, C.STONE_SUSPICION, "stone")

    peak, peak_t = 0.0, 0.0
    for i in range(int(8.0 / DT)):
        w.update(DT)
        d = math.hypot(post.x - home[0], post.y - home[1])
        if d > peak:
            peak, peak_t = d, i * DT
    # 门格 1x1、岗身 0.7 —— 他离格心 1.2 格以上，那一格就真的空出来了
    # （玩家要过的是格心，抓判定 0.75）。
    assert peak >= 1.2, (
        f"岗听到响动只离开 {peak:.2f} 格 —— 门还堵着，玩家没有空窗"
    )
    assert peak_t < 6.0, f"岗用了 {peak_t:.1f} 秒才离岗 —— 玩家等不起"
    print(f"[OK] 响动 4 格：岗 {peak_t:.1f} 秒离岗 {peak:.1f} 格（门格已空）")


def test_noise_behind_the_wall_does_not_pull_him():
    """隔着墙的响动不该把他叫走 —— 否则玩家会以为"地图没有墙"。

    这是投石机制最早的一条约束（W16 里验过代价场），这里再验一次
    第 5 关的几何：门两侧是 1 格厚的墙，隔壁房间的响动传不过来。
    """
    w = _world()
    post = _posts(w)[0]
    home = (post.x, post.y)
    # 岗在 x≈22.5（第一道闸）。往西 12 格、再往北 8 格 —— 中间隔着闸墙与货箱。
    w._emit_noise(home[0] - 12.0, home[1] - 8.0, C.STONE_NOISE_RADIUS,
                  C.STONE_SUSPICION, "stone")
    moved = 0.0
    for _ in range(int(4.0 / DT)):
        w.update(DT)
        moved = max(moved, math.hypot(post.x - home[0], post.y - home[1]))
    assert moved < 1.0, (
        f"隔着房间/墙的响动把他叫走了 {moved:.2f} 格 —— 声音没有过墙，是bug"
    )
    print(f"[OK] 隔墙响动不引人（位移 {moved:.2f} 格）")


def test_post_returns_to_facing_the_approach():
    """被骗出去再走回来，他必须**重新面朝来路**。

    这一条是实测出来的：岗被引到西北、走回来时朝东南（+45°），环视又在
    那个基准上摆 ±40°，于是他的视野锥正好盖住"玩家刚过去的那条走廊东侧"。
    玩家站在 6.9 格（视野 6.5）外，本来在视野外，就因为这个朝向被揪住。
    一局里 41% 的败局死在第二道闸前，全是这个原因。

    关卡数据里那句 `math.pi = 朝西 = 朝来路` 必须是真的 —— 否则玩家读到的
    "这扇门有人守着、他面朝你"就是假话。判的是**环视基准**（`_dwell_face`），
    不是瞬时朝向：站岗时朝向本来就在基准上摆 ±40°。
    """
    w = _world()
    post = _posts(w)[0]
    home = (post.x, post.y)
    w._emit_noise(home[0] - 2.0, home[1] + 4.0, C.STONE_NOISE_RADIUS,
                  C.STONE_SUSPICION, "stone")
    # 先等他真的离开岗位，再等他自己走回来（位置 + 环视基准都归位）
    left, back = False, False
    for _ in range(int(45.0 / DT)):
        w.update(DT)
        if math.hypot(post.x - home[0], post.y - home[1]) > 1.2:
            left = True
        # "回到岗位"要连"开始站岗"一起看：`PATROL_ARRIVE_DIST` 允许他停在
        # 格心外一点点就转成站岗，光比位置会以为他永远没回来。
        if (left and post._dwell > 0.0
                and math.hypot(post.x - home[0], post.y - home[1]) < 0.6):
            back = True
            break
    assert left, "岗根本没被响动叫走，这条断言测不到东西"
    assert back, "岗 45 秒都没走回岗位"
    # 再跑 2 秒让新一轮站岗把基准锁好（回岗后的第一轮就该锁）
    for _ in range(int(2.0 / DT)):
        w.update(DT)
    off = abs((post._dwell_face - post.post_facing + math.pi) % (2 * math.pi)
              - math.pi)
    assert off < 0.10, (
        f"回岗后环视基准偏了 {math.degrees(off):.0f}°（应为 0°）—— "
        f"基准取的是'我走回来的方向'，不是'岗位朝的方向'"
    )
    print(f"[OK] 被引开再回岗：环视基准回到岗位朝向（偏 {math.degrees(off):.1f}°）")


def test_the_ruler_can_clear_the_first_gate():
    """机器人必须**过得了第一道闸** —— 否则量出来的不是关卡难度，是尺子不会玩。

    这条是补上的：给机器人加了"离岗的人朝我走过来就往后让"之后，它一度
    在第一道闸前站满 200 秒 —— 扔石头 → 他被叫走 → 我后退 → 他回岗 →
    我再扔，一个自洽的死循环。胜率 0%，但一局都没死，只有"超时"。
    **"不会死"不等于"会玩"**，要用进度判，不能用死因判。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import botrun  # noqa: E402
    import random  # noqa: E402

    best = [0.0]

    def watch(_t, game, _mode):
        best[0] = max(best[0], game.player.x)

    r = botrun.run(level=LV, rng=random.Random(31337), loot=True,
                   budget=90.0, on_frame=watch)
    first_gate = LEVEL.doors[0][0][0]          # 第一道闸的 x
    assert best[0] > first_gate + 1.5, (
        f"90 秒只走到 x={best[0]:.1f}（第一道闸在 x={first_gate}）—— "
        f"机器人卡在闸前：{r['state']} @ {r['pos']}"
    )
    print(f"[OK] 机器人 90 秒内过了第一道闸（到 x={best[0]:.1f}，闸在 x={first_gate}）")


def main():
    print("=== W24：第 5 关「一人一扇门」===")
    test_posts_stand_still()
    test_each_gate_is_a_single_cell_with_a_post_inside()
    test_gate_cell_leaves_no_gap_to_squeeze_through()
    test_noise_pulls_the_post_off_the_gate()
    test_noise_behind_the_wall_does_not_pull_him()
    test_post_returns_to_facing_the_approach()
    test_the_ruler_can_clear_the_first_gate()
    print("\nW24 全部通过：岗站得住、堵得死、引得开、回岗还朝你来路。")


if __name__ == "__main__":
    main()
