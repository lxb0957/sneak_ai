"""局后复盘：把"这一局发生过什么"压成几行能读的数字和瞬间。

## 为什么单独一个模块

结算页要说的话分三类：总量（用时/被发现/击倒，已经有了）、逐关拆解、
关键时刻。后两类是纯算术和纯筛选 —— 不需要窗口、不需要 pygame。放进
`hud.py` 会变成"想测就得开窗口"，而这个项目里凡是"想测就得开窗口"的
东西最后都没被测（`audit_ui` 是为了补这一课才写的）。

所以这里只做数据，`hud.draw_review()` 只做版式。两边都不猜对方。

## 为什么每关都要"减一次"

`_carry` 会把上一关的用时和计数灌进新世界 —— 那是**故意的**：结算页说
"累计用时 120 秒"才对，只报最后一关会让玩家以为自己 40 秒打完了三关。

但逐关拆解要的是另一个量：本关自己花了多少。第 3 关的世界里 `elapsed`
是一二关之和加上本关，直接拿去显示会得到一张单调递增的假表格（"第 1 关
66 秒、第 2 关 120 秒、第 3 关 174 秒"），而玩家看到这张表的第一反应是
"我越打越慢"，然后开始怀疑自己。

差别只在一次减法，但两种写法**都不会报错**，只有把表印出来看才发现。
"""

from __future__ import annotations

# 值得在复盘里单列的世界事件。只挑"改变局势"的那些：扔石子、捡弹夹这类
# 动作每局几十条，全列出来会把关键时刻淹掉。
NOTABLE = {
    "spotted": "被看见 → 转入追击",
    "radio": "目击者喊了一声，附近的人都过来了",
    "body_found": "有人发现了尸体",
    "power_cut": "总闸被拉下，全图黑灯",
    "defeat": "被抓",
    "victory": "抵达撤离点",
}

# 同一个事件连着发生时合并的窗口（秒）。被一个人反复看见是三个独立事件，
# 但"开枪→命中→命中"在 0.5 秒内连着来，列三条只会让人以为打了三枪。
_Collapse = 3.0

# 同一帧里的事件时间戳完全相同（trace 的 t 按帧推进，不是按事件），
# 显示顺序只能靠优先级定：被看见 → 喊人 → 尸体被发现 → 拉闸 → 结局。
# 不排的话实测会读到"目击者喊了一声"排在"被看见"前面 ——
# 喊人是被看见的**结果**，反过来读等于把因果讲反了。
_PRIORITY = {"spotted": 0, "radio": 1, "body_found": 2, "power_cut": 3,
             "defeat": 4, "victory": 4}

# 逐关拆解要报的计数：(字段名, 展示名)。顺序就是表格里的顺序。
COUNTERS = (
    ("spotted", "被发现"),
    ("kills", "击倒"),
    ("stones_thrown", "石子"),
    ("shots_fired", "开枪"),
)


def _num(world, name: str) -> int:
    """取计数；老世界对象/测试替身没有这个字段时当 0，而不是崩掉。

    复盘是"锦上添花"的一块，不该因为它读了一个新字段就让整局结算失败。
    """
    return int(getattr(world, name, 0) or 0)


def level_row(world, carry: dict, index: int, name: str,
              completed: bool = True, keep_events: int = 8) -> dict:
    """一关的拆解。所有计数都是**本关增量**，见模块说明。"""
    carry = carry or {}
    start = float(carry.get("elapsed", 0.0) or 0.0)
    row = {
        "idx": index,
        "name": name,
        "start": round(start, 1),
        "time": round(max(0.0, float(world.elapsed) - start), 1),
        "completed": completed,
        "events": key_events(getattr(world.trace, "rows", ()), keep_events),
    }
    for key, _ in COUNTERS:
        row[key] = max(0, _num(world, key) - int(carry.get(key, 0) or 0))
    return row


def key_events(rows, limit: int = 4) -> list:
    """从决策流里挑出"值得回头看"的瞬间，返回 [(本关时间, 说法)]。

    三条规则，每条都对应一种会被玩家读错的情况：
    1. 只认白名单里的事件 —— 全量事件是给 `--trace` 的，屏幕上放不下；
    2. 同一个事件在 3 秒内连发只留第一条 —— 追击中"被看见"会连着记，
       列成一串看起来像游戏卡了；
    3. 超量时**保头保尾**等距抽 —— 只留前面的会永远看不到结局，
       而结局（被抓/撤离）恰好是复盘最该回答的那句。
    """
    out = []
    for r in rows:
        ev = (getattr(r, "data", None) or {}).get("event")
        if ev not in NOTABLE:
            continue
        t = float(getattr(r, "t", 0.0))
        if out and out[-1][2] == ev and t - out[-1][0] < _Collapse:
            continue
        out.append((t, NOTABLE[ev], ev))
    # 排一次而不是依赖调用方：同一帧的事件时间戳相同，trace 里"喊人"是先于
    # "被看见"记下来的（广播在敌人更新前、追击判定在后），照着原序显示就是
    # "他们先喊了一声，然后才看见我"。排序放在这里，逐关和整局两个出口都吃到。
    out.sort(key=lambda e: (e[0], _PRIORITY.get(e[2], 9)))
    if limit > 0 and len(out) > limit:
        out = _thin(out, limit)
    return [(t, label, ev) for t, label, ev in out]


def _thin(items: list, limit: int) -> list:
    """等距抽到 limit 条，首尾必留。"""
    if limit <= 1:
        return items[:1]
    last = len(items) - 1
    step = last / (limit - 1)
    return [items[round(i * step)] for i in range(limit)]


def first_spot(rows) -> float | None:
    """第一次被看见的时刻（本关时间）；一次没被看见返回 None。

    这个数字比"被发现 3 次"更能说明问题：3 次全在最后 5 秒，和从第 10 秒
    就被盯上，是两种完全不同的局。
    """
    for r in rows:
        if (getattr(r, "data", None) or {}).get("event") == "spotted":
            return float(getattr(r, "t", 0.0))
    return None


def summary(world, carry: dict, level_log: list, limit: int = 4) -> dict:
    """整页复盘的数据。`level_log` 是已经打完的关，当前关按需补上。

    失败局的当前关也要进表（标"未完成"）：玩家最想看的就是"我死在哪一关、
    死之前走了多久"。把它藏起来，复盘就只剩前几关的成功记录。
    """
    rows = [dict(r) for r in (level_log or [])]
    cur = int(getattr(world, "level_index", 0))
    if not rows or rows[-1].get("idx") != cur:
        rows.append(level_row(
            world, carry, cur, getattr(world, "level_name", "本关"),
            completed=world.state == _victory_state()))

    totals = {k: sum(int(r.get(k, 0) or 0) for r in rows) for k, _ in COUNTERS}
    flat = []
    for r in rows:
        for t, label, ev in r.get("events", []):
            flat.append((r.get("start", 0.0) + t, label, ev))
    events = _thin(flat, limit) if limit > 0 and len(flat) > limit else flat
    return {
        "levels": rows,
        "totals": totals,
        "events": [(t, label) for t, label, _ in events],
        "elapsed": sum(r.get("time", 0.0) for r in rows),
    }


def _victory_state():
    """延迟导入常量，避免 core 模块之间出现 import 环。"""
    from .. import constants as C

    return C.STATE_VICTORY
