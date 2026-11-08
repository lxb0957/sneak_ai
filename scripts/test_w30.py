"""W30：复盘页 —— 把"这一局怎么输的"从数据里捞出来。

## 为什么需要这个

结算页只告诉你四个数：用时、被发现、击倒、得分。它回答不了玩家最想问的
那两个问题 —— **"我是从哪一关开始崩的"** 和 **"崩之前发生了什么"**。

这两件事的数据本来就有：`_carry` 记着每关带过来的用时和计数，
`ai/trace.py` 记着整局的决策流。但它们从没被摊开给玩家看过 ——
trace 只喂给 F1 面板和 `--trace` 日志，两个都是"给开发者看的"。

复盘页把它们接上：逐关拆解（本关花了多久、被发现几次）+ 关键时刻
（第一次被看见是什么时候、谁喊了人、最后是撤离还是被抓）。

## 这组测试守五件事

1. **每关报的是增量，不是累计**。第 3 关的世界里 `elapsed` 含着一二关的
   时间，直接显示会得到一张单调递增的假表格 —— 玩家第一反应是"我越打越慢"。
   这处写错**不会报错**，只会让整页数字说谎，所以必须有断言；
2. **计数真的在记**：扔石子、开枪以前根本没有计数器（只有"引开了几个人"），
   复盘页要说的"你用了什么工具"就无从谈起；
3. **关键时刻的折叠与排序**：同一帧里"喊人"和"被看见"时间戳完全相同，
   不排序就会读到"他们先喊了一声，然后才看见我"；
4. **版式不重叠**：文字叠字是静默错误，不会抛异常，只会让人读不懂；
   所以直接对布局出来的矩形做两两求交；
5. **一关一行、重复结算不重复记**：`_handle_scoring` 每帧都可能被调用，
   靠 `_scored` 挡着 —— 万一挡不住，复盘页会出现同一关五行。

用法：
    python scripts/test_w30.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.ai.trace import Row  # noqa: E402
from sneak_ai.core import debrief  # noqa: E402
from sneak_ai.core import records  # noqa: E402
from sneak_ai.core.hud import END_DESIGN, HUD  # noqa: E402


class _TmpRecord:
    """把存档指到临时目录，别碰玩家真实的 records.json。"""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sneak_w30_"))
        self.old = (records.DATA_DIR, records.RECORD_FILE)
        records.DATA_DIR, records.RECORD_FILE = self.dir, None
        return self

    def __exit__(self, *exc):
        records.DATA_DIR, records.RECORD_FILE = self.old
        return False


class _FakeWorld:
    """只带复盘要读的字段。真 World 要建地图、敌人、流场，这里不需要。"""

    def __init__(self, **kw):
        self.elapsed = 0.0
        self.level_index = 0
        self.level_name = "哨站"
        self.state = C.STATE_VICTORY
        self.spotted = 0
        self.kills = 0
        self.stones_thrown = 0
        self.shots_fired = 0
        self.trace = type("T", (), {"rows": []})()
        for k, v in kw.items():
            setattr(self, k, v)


def _rows(*spec):
    """(t, event) → 决策流。data 里必须有 event，`key_events` 靠它筛。"""
    return [Row(t, 0, "world", debrief.NOTABLE.get(ev, ev), {"event": ev})
            for t, ev in spec]


# ---------------------------------------------------------------- 1. 增量
def test_level_row_reports_delta_not_total():
    """第 3 关报 54.2 秒，不是 174.2 秒。"""
    w = _FakeWorld(elapsed=174.2, level_index=2, level_name="回廊",
                   spotted=4, kills=1, stones_thrown=3, shots_fired=2)
    carry = {"elapsed": 120.0, "spotted": 3, "kills": 1,
             "stones_thrown": 1, "shots_fired": 0}
    row = debrief.level_row(w, carry, 2, "回廊")
    assert abs(row["time"] - 54.2) < 1e-6, \
        f"本关用时算成了 {row['time']}（累计口径会是 174.2）"
    assert (row["spotted"], row["kills"], row["stones_thrown"],
            row["shots_fired"]) == (1, 0, 2, 2), row
    assert row["start"] == 120.0, row

    # 第一关没有 carry：增量必须等于累计，别把"没有上一关"算成负数
    first = debrief.level_row(_FakeWorld(elapsed=66.3, spotted=2), {}, 0, "哨站")
    assert first["time"] == 66.3 and first["spotted"] == 2, first
    assert first["kills"] == 0 and first["stones_thrown"] == 0, first
    print("[OK] 逐关报的是增量：174.2−120 = 54.2 秒，计数同样逐项相减")


# ---------------------------------------------------------------- 2. 计数
def test_counters_are_really_incremented():
    """扔石子/开枪以前没有计数器，现在必须真的在记，并且跨关携带。"""
    from sneak_ai.__main__ import Game

    with _TmpRecord():
        g = Game()
        g.menu.sel = 0
        g._on_menu_start()
        w = g.world
        before = (w.stones_thrown, w.shots_fired)

        w.player.stones = 3
        w.player.ammo = 3
        thrown = w.throw_stone(None)
        assert thrown, "石子没扔出去，后面的断言没有意义"
        w.shoot((w.player.x + 2.0, w.player.y))
        got = (w.stones_thrown, w.shots_fired)
        assert got == (before[0] + 1, before[1] + 1), \
            f"投石/开枪计数没跟上：{before} → {got}"

        # 跨关要跟着走，否则第 2 关的"本关增量"会算出负数
        assert "stones_thrown" in Game._CARRY_COUNTERS, Game._CARRY_COUNTERS
        assert "shots_fired" in Game._CARRY_COUNTERS, Game._CARRY_COUNTERS
        snap = g._snapshot_carry()
        assert snap.get("stones_thrown") == got[0], snap
    print("[OK] 投石/开枪各自计数，并且随 _CARRY_COUNTERS 一起跨关")


# ---------------------------------------------------------------- 3. 事件
def test_key_events_collapse_and_prioritise():
    """连发折叠、超量保头保尾、同一时刻按因果排（被看见先于喊人）。"""
    rows = _rows(
        (3.0, "stone_throw"),
        # 真实 trace 的顺序就是这样：广播（radio）在敌人更新前记，追击判定
        # （spotted）在后，两者时间戳相同。照原序读就是"先喊人、才被看见"。
        (11.2, "radio"),
        (11.2, "spotted"),
        (11.5, "spotted"),        # 同一轮追击的连发，应被折叠
        (30.0, "body_found"),
        (48.0, "defeat"),
    )
    got = debrief.key_events(rows, limit=10)
    names = [ev for _, _, ev in got]
    assert names.count("spotted") == 1, f"连发没折叠：{names}"
    assert "stone_throw" not in names, f"白名单外的事件漏进来了：{names}"
    i_spot = names.index("spotted")
    i_radio = names.index("radio")
    assert i_spot < i_radio, f"同一帧里喊人排到了被看见前面：{names}"
    assert [t for t, _, _ in got] == sorted(t for t, _, _ in got), got

    # 超量：保头保尾，中间等距抽
    many = _rows(*[(float(i), "spotted" if i % 2 else "radio")
                  for i in range(1, 21)])
    thin = debrief.key_events(many, limit=4)
    assert len(thin) == 4, thin
    times = [t for t, _, _ in thin]
    assert times[0] == 1.0 and times[-1] == 20.0, f"首尾没保住：{times}"
    print("[OK] 关键时刻：白名单筛 / 连发折叠 / 同帧按因果排 / 保头保尾")


# ---------------------------------------------------------------- 4. 汇总
def test_summary_counts_failed_level():
    """失败局要把"正在打的那一关"也列进去，标未完成。"""
    log = [{"idx": 0, "name": "哨站", "start": 0.0, "time": 66.3,
            "completed": True, "spotted": 0, "kills": 0,
            "stones_thrown": 0, "shots_fired": 0,
            "events": [(5.0, debrief.NOTABLE["victory"], "victory")]}]
    w = _FakeWorld(elapsed=94.0, level_index=1, level_name="环形枢纽",
                   spotted=2, stones_thrown=1, state=C.STATE_DEFEAT,
                   trace=type("T", (), {"rows": _rows(
                       (8.0, "spotted"), (33.0, "defeat"))})())
    s = debrief.summary(w, {"elapsed": 66.3, "spotted": 0}, log, limit=6)
    assert len(s["levels"]) == 2, s["levels"]
    last = s["levels"][-1]
    assert not last["completed"], last
    assert abs(last["time"] - 27.7) < 1e-6, f"失败关用时算错：{last['time']}"
    assert s["totals"]["spotted"] == 2, s["totals"]
    assert abs(s["elapsed"] - 94.0) < 1e-6, s["elapsed"]
    print("[OK] 失败局的当前关也进表（标未完成），总量是各关之和")


# ---------------------------------------------------------------- 5. 版式
def _brief(n_levels=5, n_events=4):
    rows = []
    for i in range(n_levels):
        rows.append({"idx": i, "name": f"第 {i + 1} 关", "start": i * 60.0,
                     "time": 60.0 + i, "completed": i < n_levels - 1,
                     "spotted": i, "kills": 0, "stones_thrown": i % 2,
                     "shots_fired": 0, "events": []})
    evs = [(11.2, "被看见 → 转入追击"),
           (69.4, "目击者喊了一声，附近的人都过来了"),
           (121.8, "有人发现了尸体"),
           (207.4, "被抓")][:n_events]
    return {"levels": rows, "totals": {}, "events": evs, "elapsed": 372.3}


def test_review_layout_never_overlaps():
    """块与块两两不相交、全在设计尺寸内、被面板包住。"""
    hud = HUD()
    for n_levels in (1, 5):
        brief = _brief(n_levels=n_levels)
        rect, placed = hud._review_layout(brief)
        rects = [(s, pygame.Rect(pos, s.get_size())) for s, pos in placed]
        assert rect.contains(rects[0][1]), "面板没包住第一块"
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i][1], rects[j][1]
                assert not a.colliderect(b), (
                    f"{n_levels} 关版式：第 {i} 块 {a} 压住了第 {j} 块 {b}")
        design = pygame.Rect(0, 0, *END_DESIGN)
        assert design.contains(rect), f"面板超出设计尺寸：{rect}"
        for _, r in rects:
            assert design.contains(r), f"内容超出设计尺寸：{r}"
    print("[OK] 复盘版式：任意两块不重叠、整页落在 1280x960 之内")


def test_review_draws_at_three_sizes():
    """三种窗口都要画得出来（矮窗口走等比缩放，别把字缩没）。"""
    old = (C.WINDOW_W, C.WINDOW_H)
    try:
        for size in ((800, 600), (1024, 768), (1280, 960), (1920, 1080)):
            C.WINDOW_W, C.WINDOW_H = size
            screen = pygame.display.set_mode(size)
            screen.fill((0, 0, 0))
            hud = HUD()
            hud.draw_review(screen, _brief(), 0.0)
            lit = sum(1 for x in range(0, size[0], 8) for y in range(0, size[1], 8)
                      if screen.get_at((x, y))[:3] != (0, 0, 0))
            assert lit > 50, f"{size} 下几乎什么都没画出来（亮点 {lit}）"
    finally:
        C.WINDOW_W, C.WINDOW_H = old
    print("[OK] 800x600 / 1024x768 / 1280x960 / 1920x1080 四种窗口都能画出复盘页")


# ---------------------------------------------------------------- 6. 接线
def test_scoring_logs_one_row_per_level():
    """过关记一行；重复结算不重复记；换局清空。"""
    from sneak_ai.__main__ import Game

    with _TmpRecord():
        g = Game()
        g.menu.sel = 0
        g._on_menu_start()
        g.world.elapsed = 66.3
        g.world.spotted = 1
        g.world.state = C.STATE_VICTORY
        g._handle_scoring()
        assert len(g.level_log) == 1, g.level_log
        g._handle_scoring()                     # 第二帧不会重复记
        assert len(g.level_log) == 1, "重复结算记了两行"

        row = g.level_log[0]
        assert row["idx"] == 0 and row["completed"], row
        assert abs(row["time"] - 66.3) < 1e-6 and row["spotted"] == 1, row

        g._restart_run()
        assert g.level_log == [], "重开一局还留着上一局的复盘"
    print("[OK] 一关一行、重复结算不重复记、重开清空")


def main() -> int:
    test_level_row_reports_delta_not_total()
    test_counters_are_really_incremented()
    test_key_events_collapse_and_prioritise()
    test_summary_counts_failed_level()
    test_review_layout_never_overlaps()
    test_review_draws_at_three_sizes()
    test_scoring_logs_one_row_per_level()
    print("\nW30 全部通过：复盘页的数据与版式都立得住")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
