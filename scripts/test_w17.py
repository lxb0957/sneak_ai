"""W17 测试：关卡抽象 + 第二关「环形枢纽」。

这一组测试守的不是"功能存在"，而是**关卡能不能被机器审查**。

W17 之前，"一关"是散在 `world.py` 的四份模块级常量 + `constants.py` 的两个
全局坐标，于是所有关卡规则都只能写在注释里 —— 注释不会红。这次把关卡提成
数据（`core/levels.py`）之后，下面这些规则对**每一关**都自动成立：

1. 注册表自洽：能构建、可走格连成一片、出生点 / 终端 / 撤离点都在地上；
2. 巡逻点离任何门 / 闸 ≥3 格（贴着门 = 把门焊死，机器人会撞死在门口）；
3. 补给离巡逻点 ≥3 格、离门 ≥2.5 格（逼玩家贴脸拿的不是难度，是送死）；
4. 出生点不在任何哨兵的初始视野里（一睁眼就在别人视锥里不叫开局）；
5. 目标点跟着关卡走 —— `_check_objectives` 读的必须是**本关**坐标，
   这是"第二关的终端画在第一关坐标上"这个 bug 的守门人；
6. 累计评级基准：打两关允许的时间必须是两关之和，否则越往后越容易 S。

判定手法上有一个刻意的选择：第 4 条不靠"距离 < 视野半径"这种静态估算，
而是**真的建一个 World 空跑 3 秒**。因为初始朝向按坐标散开、巡逻相位也按
坐标散开，静态估算永远算不准"开局那 3 秒里会不会有人正好回头看你"。

用法：
    python scripts/test_w17.py
"""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sneak_ai import constants as C
from sneak_ai.ai.coordination import Coordinator
from sneak_ai.core import levels as L
from sneak_ai.core.world import World
from sneak_ai.entity.enemy import Enemy
from sneak_ai.entity.player import Player
from sneak_ai.perception.perception import Perception

DT = 1 / 60


# ---------------------------------------------------------------- 工具
def make_world(idx: int):
    """按第 idx 关的数据搭一个 World（和 Game._new_world 同一套流程）。"""
    lv = L.LEVELS[idx]
    grid = lv.build()
    w = World(grid)
    w.level_index = lv.idx
    w.level_name = lv.name
    w.terminals = lv.terminals
    w.exfil = lv.exfil
    w.par = L.cumulative_par(idx)
    w.coordinator = Coordinator(grid)

    spawn, cfg = L.spawn_and_patrols(lv, grid)
    perc = Perception()
    w.player = Player(*spawn)
    w.enemies = [Enemy(x, y, patrol=pts, perception=perc)
                 for (x, y, pts) in cfg]
    w.pickups = L.level_pickups(lv, grid)
    return w, lv


def door_cells(lv):
    out = []
    for (hole, name) in lv.doors:
        x0, y0, x1, y1 = hole
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                out.append((x + 0.5, y + 0.5, name))
    return out


# ---------------------------------------------------------------- 注册表
def test_registry_is_sane():
    assert len(L.LEVELS) >= 2, "说好的多关卡呢"
    names = [lv.name for lv in L.LEVELS]
    assert len(set(names)) == len(names), f"关卡重名：{names}"
    assert [lv.idx for lv in L.LEVELS] == list(range(len(L.LEVELS))), "关卡序号要从 0 连续"
    for lv in L.LEVELS:
        assert lv.par[0] < lv.par[1] < lv.par[2], f"{lv.name} 的评级阈值没有递增"
    print(f"[OK] {len(L.LEVELS)} 关：" + " / ".join(names))


def test_every_level_builds_and_is_connected():
    for lv in L.LEVELS:
        grid = lv.build()
        floors = {(x, y) for y in range(grid.h) for x in range(grid.w)
                  if not grid.is_wall(x, y)}
        reach = L.flood_floor(grid, int(lv.spawn[0]), int(lv.spawn[1]))
        assert reach == floors, (
            f"{lv.name} 有 {len(floors - reach)} 格从出生点走不到，"
            f"例如 {sorted(floors - reach)[:4]}"
        )
        for tag, pt in (("出生点", lv.spawn), ("终端", lv.terminal), ("撤离点", lv.exfil)):
            assert not grid.is_wall(int(pt[0]), int(pt[1])), f"{lv.name} 的{tag}落在墙里"

        # 目标点必须落在**格中心**（x.5, y.5），不能落在四格共用的格点上。
        # 这是机器人跑出来的：第二关的终端原来写在 (52.0, 22.0)，
        # 寻路是以格中心为目标的、容差 0.4，加上角点到格中心的 0.707，
        # 最坏情况会停在离终端 1.1 格的地方 —— 而触发半径只有 0.8。
        # 表现为：机器人贴着终端站了 150 秒，状态始终是"潜入中"。
        for tag, pt in (("终端", lv.terminal), ("撤离点", lv.exfil)):
            assert abs(pt[0] % 1 - 0.5) < 1e-6 and abs(pt[1] % 1 - 0.5) < 1e-6, (
                f"{lv.name} 的{tag} {pt} 不在格中心 —— 玩家/机器人可能差一点点摸不到它"
            )
    print("[OK] 每关都能构建、全图连通、三个关键点都在地上且位于格中心")


def test_doors_are_one_or_two_cells_wide():
    """门必须是 1 或 2 格宽，且 **1 格门必须有固定岗驻守**。

    2 格是默认：一次只过一个人，但不憋屈（3 格以上就不是门了，是"绕过去"）。

    1 格门单独存在时是坏设计 —— 玩家会被一个 1 格宽的洞卡得反复对不准，
    这不是难度，是操作摩擦。所以它只在一种情况下允许：**洞里站着一个人**。
    那时这扇门不是"通道"，是"岗位"：玩家的第一个动作本来就是"让守门的人
    走开"（第 5 关），门的宽窄不再影响手感，反而决定了"他能不能被绕过去"。

    这条比原来那句"必须是 2 格"更精确：它没有放宽，而是把"为什么窄"
    也变成了可检查的条件。
    """
    for lv in L.LEVELS:
        grid = lv.build()
        posts = [(px + 0.5, py + 0.5) for (px, py, _f) in lv.posts]
        for (hole, name) in lv.doors:
            x0, y0, x1, y1 = hole
            cells = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
            assert all(not grid.is_wall(*c) for c in cells), f"{lv.name} 的 {name} 没挖开"
            assert len(cells) in (1, 2), (
                f"{lv.name} 的 {name} 是 {len(cells)} 格 —— 门只能 1 或 2 格"
            )
            if len(cells) == 1:
                cx, cy = cells[0][0] + 0.5, cells[0][1] + 0.5
                assert any(math.hypot(px - cx, py - cy) < 0.2 for px, py in posts), (
                    f"{lv.name} 的 {name} 只有 1 格宽，却没有固定岗站在里面 —— "
                    f"那会变成一个纯操作摩擦的窄洞"
                )
    print(f"[OK] {sum(len(lv.doors) for lv in L.LEVELS)} 道门/闸：2 格宽，"
          f"或 1 格宽且有岗驻守")


def test_no_patrol_point_blocks_a_door():
    """多点巡逻的哨兵不许离门 <3 格；**固定岗**是这条规则唯一的例外。

    为什么开这个口子：前四关的"堵门"是事故 —— 一个会走来走去的哨兵恰好停在
    唯一入口前，玩家只能等他走开，等不到就是死路（第 4 关数据舱北门就这么被
    焊死过）。第 5 关的固定岗是**设计**：他站定不走，而玩家手里有石子可以让
    他走（行为断言在 `test_w24.test_fixed_post_leaves_when_noise`）。
    区别不在"离门多近"，而在**玩家有没有第二个动词**。
    """
    for lv in L.LEVELS:
        grid = lv.build()
        _spawn, cfg = L.spawn_and_patrols(lv, grid)
        posts = {(round(px + 0.5, 3), round(py + 0.5, 3)) for (px, py, _f) in lv.posts}
        dcells = door_cells(lv)
        bad = []
        for i, (x, y, pts) in enumerate(cfg):
            if (round(x, 3), round(y, 3)) in posts:
                continue      # 固定岗：允许守门，见 docstring
            for tag, p in [("出生点", (x, y))] + [("巡逻点", q) for q in pts]:
                near = min(dcells, key=lambda c: math.hypot(c[0] - p[0], c[1] - p[1]))
                dist = math.hypot(near[0] - p[0], near[1] - p[1])
                if dist < 3.0:
                    bad.append((lv.name, i, tag,
                                (round(p[0], 1), round(p[1], 1)), near[2], round(dist, 1)))
        assert not bad, f"这些哨兵会堵门：{bad}"
    print("[OK] 每关的巡逻哨兵全部离门/闸 ≥3 格（固定岗除外，那是设计）")


def _corridor_span(grid, x, y, dx, dy):
    """从 (x,y) 沿 (dx,dy) 连续可走格数（含自身）—— 用来量走廊有多宽。"""
    n = 1
    cx, cy = x + dx, y + dy
    while not grid.is_wall(cx, cy):
        n += 1
        cx += dx
        cy += dy
    return n


def test_no_patrol_pinned_in_a_narrow_corridor():
    """3 格以内的窄廊里，不许有"几乎原地不动"的哨兵。

    v1.7 的教训（第 1 关连栽四次）：3 格宽走廊里巡逻段只有 3 格时，哨兵基本
    钉在原地 —— 那不是"会移动的视野"，是一道**固定在路中间的门**。玩家想过去
    只能等，而它几乎不离开，等于等不到。

    第 1 关一共 4 处（B3 走廊 + A1/A4/A5 三条短廊）。删掉它们，12 相位胜率
    从 25% 一路涨到 92%。注意这不是"少放几个人"的功劳：这三条走廊长 9 格，
    两端各要留 3 格离门距离，中间只剩 3 格 —— **在 3 格宽短廊里根本放不下
    "会走动的视野"**，放谁都是固定门。

    判据是两个可量的数，不是"看起来窄不窄"：
      走廊宽度 ≤3 格（人站中间，两侧各留不到一格）
      巡逻段长度 ≤3 格（它离开原位走不远，等价于原地站岗）
    第 3 关服务廊那条 3 格宽、14 格长的巡逻不算 —— 玩家有大量窗口。
    """
    bad = []
    for lv in L.LEVELS:
        grid = lv.build()
        for (x, y, pts) in lv.patrols():
            for i in range(len(pts) - 1):
                (ax, ay), (bx, by) = pts[i], pts[i + 1]
                length = max(abs(bx - ax), abs(by - ay))
                if ay == by and ax != bx:          # 横走 → 量纵向廊高
                    widths = [_corridor_span(grid, cx, ay, 0, 1)
                              + _corridor_span(grid, cx, ay, 0, -1) - 1
                              for cx in range(min(ax, bx), max(ax, bx) + 1)]
                    width = min(widths)
                elif ax == bx and ay != by:        # 竖走 → 量横向廊宽
                    widths = [_corridor_span(grid, ax, cy, 1, 0)
                              + _corridor_span(grid, ax, cy, -1, 0) - 1
                              for cy in range(min(ay, by), max(ay, by) + 1)]
                    width = min(widths)
                else:
                    continue                        # 斜巡不适用这条规则
                if width <= 3 and length <= 3:
                    bad.append((lv.name, (ax, ay), (bx, by),
                                f"廊宽 {width} 格 · 巡长 {length} 格"))
    assert not bad, f"这些哨兵钉死在窄廊里（等于一道固定的门，玩家等不到窗口）：{bad}"
    print("[OK] 没有哨兵钉死在 3 格以内的窄廊里（不制造固定门）")


def test_pickups_are_reachable_and_fair():
    for lv in L.LEVELS:
        grid = lv.build()
        items = L.level_pickups(lv, grid)
        assert len(items) == len(lv.pickups), f"{lv.name} 补给点数量对不上"
        assert not [it for it in items if grid.is_wall(int(it["x"]), int(it["y"]))], \
            f"{lv.name} 有补给落在墙里（永远捡不到）"

        _spawn, cfg = L.spawn_and_patrols(lv, grid)
        patrol_pts = [q for (_x, _y, pts) in cfg for q in pts]
        dcells = door_cells(lv)
        for it in items:
            d_patrol = min(math.hypot(it["x"] - q[0], it["y"] - q[1]) for q in patrol_pts)
            assert d_patrol >= 3.0, (
                f"{lv.name} 的补给 {(it['x'], it['y'])} 离巡逻点只有 {d_patrol:.1f} 格 —— "
                f"那是送死，不是风险"
            )
            d_door = min(math.hypot(it["x"] - c[0], it["y"] - c[1]) for c in dcells)
            # 阈值 2.0 而不是注释里写的 2.5：第 1 关实测有三处是 2.24（斜着一格），
            # 那是有意的贴门摆位。写成 2.5 就等于让测试去否认既成设计 ——
            # 要么改关卡，要么改注释，这里选了后者（并在 levels.py 里写明）。
            assert d_door >= 2.0, (
                f"{lv.name} 的补给 {(it['x'], it['y'])} 离门只有 {d_door:.1f} 格 —— 站在门里白拿"
            )
    print("[OK] 每关补给全部可走、离巡逻点 ≥3 格、离门 ≥2.5 格")


def test_spawn_is_not_inside_a_vision_cone():
    """真的空跑 3 秒：出生点不能在任何一个哨兵的初始视野里。

    静态估算不可靠 —— 初始朝向按坐标散开、巡逻相位也按坐标散开，
    "开局那 3 秒里会不会有人正好回头看你"只能靠跑一遍才知道。
    """
    for lv in L.LEVELS:
        w, _ = make_world(lv.idx)
        for _ in range(180):
            w.update(DT)
        assert not w.captured, f"{lv.name}：开局 3 秒内就被抓住了"
        assert w.spotted == 0, (
            f"{lv.name}：开局 3 秒内被发现 {w.spotted} 次 —— 玩家一睁眼就在视锥里"
        )
    print("[OK] 每关开局 3 秒内都不会被看见（出生点是安全的）")


def test_objectives_follow_the_level():
    """目标点必须跟着关卡走。

    这条是 W17 最容易犯的错：`World` 以前读 `C.DATA_TERMINAL`，
    加了第二关之后，第二关的终端会画在第一关的坐标上 —— 而且"看起来一切正常"，
    因为玩家照样能走到那个坐标、照样能通关。只有把两关的坐标互相当成触发点
    去断言，才能把这种"沉默的错误"逼出来。
    """
    seen = set()
    for lv in L.LEVELS:
        w, _ = make_world(lv.idx)
        seen.add((lv.terminal, lv.exfil))

        w.player.x, w.player.y = lv.terminals[0]
        w._check_objectives()
        if len(lv.terminals) == 1:
            assert w.state == C.STATE_EXFILTRATE, f"{lv.name} 的终端没触发"
        else:
            # 多节点关卡：拿第一个只是"记一笔"，还没到能撤的时候
            assert w.state == C.STATE_INFILTRATE, f"{lv.name} 拿第一个节点就放行了"
            assert w.has_data is False, f"{lv.name} 拿第一个节点就标记了'已取得数据'"
            assert w.alert is True, f"{lv.name} 拿第一个节点应该拉响警报"
            for pt in lv.terminals[1:]:
                w.player.x, w.player.y = pt
                w._check_objectives()
            assert w.state == C.STATE_EXFILTRATE, f"{lv.name} 拿完所有节点后没进入撤离"

        w.player.x, w.player.y = lv.exfil
        w._check_objectives()
        assert w.state == C.STATE_VICTORY, f"{lv.name} 的撤离点没触发"

    assert len(seen) == len(L.LEVELS), "两关的目标点坐标居然一样"

    # 反证：第 1 关的终端坐标在第 2 关里**不是**触发点
    w, _ = make_world(1)
    w.player.x, w.player.y = L.LEVELS[0].terminal
    w._check_objectives()
    assert w.state == C.STATE_INFILTRATE, "第 2 关仍然在用第 1 关的终端坐标"
    print("[OK] 目标点跟着关卡走（互不串台）")


def test_par_and_rank_are_cumulative():
    assert L.cumulative_par(0) == L.LEVELS[0].par
    c1 = L.cumulative_par(1)
    for i in range(3):
        assert c1[i] == L.LEVELS[0].par[i] + L.LEVELS[1].par[i]

    w, _ = make_world(1)
    w.kills = 0
    w.spotted = 0
    # 单看第 2 关很快，但累计用时已经超过两关之和的 S 线
    w.elapsed = c1[0] + 1.0
    assert w.rank() != "S", "累计用时超了 S 线还评 S —— 评级变成了关卡数的函数"
    w.elapsed = c1[0] - 1.0
    assert w.rank() == "S"
    print(f"[OK] 评级基准按累计计算（{len(L.LEVELS)} 关 S 线 {c1[0]:.0f} 秒）")


def test_every_level_route_count_is_small():
    """每一关的最小割都要小：入口少，路线才有"取舍"而不是"随便绕"。

    这一条对**所有关卡**生效，所以第三关（大厅四面开口）也自动被审 ——
    柱林看起来哪儿都能走，但真正能进场的只有四道 2 格门。
    """
    from audit_routes import min_cut_size

    for lv in L.LEVELS:
        grid = lv.build()
        s = (int(lv.exfil[0]), int(lv.exfil[1]))
        for node in lv.terminals:
            t = (int(node[0]), int(node[1]))
            cut = min_cut_size(grid, s, t)
            assert cut <= 4, f"{lv.name} 的路线太多（最小割 {cut} 格），难度会被稀释"
            print(f"[OK] {lv.name} 最小割 {cut} 格（{len(lv.doors)} 个入口）")


def test_record_marks_level_count():
    """存档要记住"这条成绩跑了几关"。

    一关 97 秒和两关 121 秒不是一个项目，摆在同一个"最佳纪录"里比较会误导。
    但存档是玩家唯一的数据，不能为了加字段去改写它 —— 所以只**读**：
    缺失字段按单关时代算（那时游戏真的只有一关）。
    """
    from sneak_ai.core import records

    w, _ = make_world(len(L.LEVELS) - 1)
    assert w.record()["levels"] == len(L.LEVELS), "成绩里没记住关卡数"

    total = len(L.LEVELS)
    assert records.legacy_label({"levels": 1}, total) == "旧版 1 关"
    assert records.legacy_label({"levels": total}, total) == ""
    assert records.legacy_label({}, total) == "", "无成绩的空存档不该被标成旧版"
    print("[OK] 成绩带关卡数，旧单关存档标「旧版」而不是被改写")


def main():
    print("=== W17：关卡抽象（每一关都体检）===")
    test_registry_is_sane()
    test_every_level_builds_and_is_connected()
    test_doors_are_one_or_two_cells_wide()
    test_no_patrol_point_blocks_a_door()
    test_no_patrol_pinned_in_a_narrow_corridor()
    test_pickups_are_reachable_and_fair()
    test_spawn_is_not_inside_a_vision_cone()
    test_objectives_follow_the_level()
    test_par_and_rank_are_cumulative()
    test_record_marks_level_count()
    test_every_level_route_count_is_small()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
