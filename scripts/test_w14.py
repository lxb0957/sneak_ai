"""W14：计分系统重做（基础分 + 风格分 / 失败零分 / 风格分明细落存档）。

这一轮改的不是"数值大小"，而是**分数的含义**：
- 分数必须能说出"你赢在哪"（基础分 = 效率，风格分 = 方式）；
- 没通关就没有分数（以前失败局能拿全场最高分，那是 bug 不是设计）；
- 风格分要能拉开玩法差距，而且不能靠绕圈刷。

所以测试分三层：公式对账（数值）、行为对账（什么时候给分）、持久化对账
（存档里多了什么字段、旧存档还能不能读）。
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.core import records  # noqa: E402
from sneak_ai.core.world import World, build_demo_map  # noqa: E402

pygame.init()
pygame.display.set_mode((320, 240))

_results = []


def check(name: str, cond: bool) -> None:
    _results.append((name, cond))
    print(("[OK] " if cond else "[FAIL] ") + name)


def fresh_world():
    """一个停在通关状态的干净世界，逐项改指标来验证公式。"""
    w = World(build_demo_map())
    w.state = C.STATE_VICTORY
    w.elapsed = 60.0
    w.spotted = 0
    w.kills = 0
    w.peak_threat = 1.0        # 默认"被确认发现过"，风格分从零开始叠
    w.bodies_found = 0
    w.decoy_hits = 0
    w.shield_kills = 0
    w.spots_by_enemy.clear()
    return w


# ---------- 公式 ----------
def test_formula():
    w = fresh_world()
    expect = C.SCORE_BASE - 60.0 * C.SCORE_TIME_PENALTY + C.SCORE_STYLE_NO_KILL
    check(f"60 秒零死零暴露 = {expect}", w.score() == round(expect))

    # 失败就是 0：不能只在结算页"不显示"，score() 本身必须返回 0
    w.state = C.STATE_DEFEAT
    check("失败局得分为 0", w.score() == 0)
    check("失败局基础分仍可单独查看（用于复盘）", w.base_score() > 0)
    w.state = C.STATE_VICTORY

    # 时间封顶：超过 SCORE_TIME_FREE 之后按比例折，不再等额扣
    slow, slower = fresh_world(), fresh_world()
    slow.elapsed = C.SCORE_TIME_FREE
    slower.elapsed = C.SCORE_TIME_FREE + 40
    drop = slow.score() - slower.score()
    linear = 40 * C.SCORE_TIME_PENALTY * C.SCORE_TIME_OVER_RATE
    check(f"超时部分折算扣分（40 秒扣 {drop}，线性应为 {linear:.0f}）",
          abs(drop - linear) <= 2)

    # 击杀/发现/时间都是单调的：多一项坏指标，分只能更低
    a, b = fresh_world(), fresh_world()
    b.spotted = 2
    check("被发现越多分越低", b.score() < a.score())
    a, b = fresh_world(), fresh_world()
    b.kills = 1
    check("击倒越多分越低", b.score() < a.score())

    # 分数不会掉成负数（玩家不该看到 -320 分）
    w = fresh_world()
    w.elapsed = 400.0
    w.spotted = 40
    w.kills = 21
    check("极端失败局不会出现负分", w.score() == 0)


# ---------- 风格分 ----------
def test_style():
    w = fresh_world()
    w.peak_threat = 0.2
    names = [n for n, _ in w.style_bonus()]
    check(f"全程没被确认 → 全身而退: {names}", "全身而退" in names)
    w.peak_threat = C.SUSPICION_CONFIRM
    check("被确认过一次就拿不到全身而退",
          "全身而退" not in [n for n, _ in w.style_bonus()])

    # "起疑但没确认"这一档必须放过：否则风格分变成了"一次都别被看见"，
    # 那基础分里的"被发现"就没意义了（同一件事扣两次）。
    w.peak_threat = (C.SUSPICION_CURIOUS + C.SUSPICION_CONFIRM) / 2
    check("只是起疑、没被确认 → 仍算全身而退",
          "全身而退" in [n for n, _ in w.style_bonus()])

    # 引开：必须真的引开过人（≥3），只扔石子不算
    w = fresh_world()
    w.decoy_hits = 2
    check("引开 2 人次不给分", not any("引开" in n for n, _ in w.style_bonus()))
    w.decoy_hits = 4
    check("引开 4 人次给分",
          any("引开" in n for n, _ in w.style_bonus()))

    # 干净开枪：没开枪的人不该拿这一项
    w = fresh_world()
    check("零击倒时不给干净开枪",
          "开枪未被目击" not in [n for n, _ in w.style_bonus()])
    w.kills, w.bodies_found = 2, 0
    check("开了枪且尸体没暴露 → 干净开枪",
          "开枪未被目击" in [n for n, _ in w.style_bonus()])
    w.bodies_found = 1
    check("尸体被目击后撤销干净开枪",
          "开枪未被目击" not in [n for n, _ in w.style_bonus()])

    # 反向补丁：同一个哨兵面前反复被发现，风格分要被扣回去
    w = fresh_world()
    w.peak_threat = 0.2
    base = w.style_total()
    w.spots_by_enemy["3.5,4.5"] = C.REPEAT_SPOT_LIMIT
    check(f"反复被发现扣回 {C.SCORE_REPEAT_PENALTY} 分",
          w.style_total() == base - C.SCORE_REPEAT_PENALTY)


# ---------- 存档 ----------
def test_records(tmp: Path):
    records.RECORD_FILE = tmp / "records.json"

    w = fresh_world()
    w.peak_threat = 0.1
    rec = w.record()
    check("record() 带风格分明细", isinstance(rec.get("style"), list) and rec["style"])
    check("record() 带风格分合计", rec.get("style_total", None) == w.style_total())

    check("首局提交算新纪录", records.submit(rec) is True)
    back = records.load()
    check("读回来的明细一致", back.get("style") == rec["style"])

    # 旧存档（没有 style 字段）必须能读，且不崩
    records.RECORD_FILE.write_text(json.dumps(
        {"score": 700, "time": 80.0, "spotted": 1, "kills": 2, "rank": "B"},
        ensure_ascii=False), encoding="utf-8")
    old = records.load()
    check("旧存档缺 style 字段 → 补空列表", old.get("style") == [])
    check("旧存档缺 style_total → 补 0", old.get("style_total") == 0)

    # 明细字段类型坏了也不能崩：HUD 会拿它做 f-string
    records.RECORD_FILE.write_text(json.dumps(
        {"score": 700, "style": [{"name": "零击倒", "value": "80"},
                                 {"value": 10}, "垃圾", None],
         "style_total": "abc"}, ensure_ascii=False), encoding="utf-8")
    dirty = records.load()
    check("明细里的坏条目被丢弃",
          dirty["style"] == [{"name": "零击倒", "value": 80}])
    check("坏的 style_total 归零", dirty["style_total"] == 0)

    # 0 分不算纪录：以前失败局能拿到非零分，会把最佳纪录顶掉
    records.RECORD_FILE.unlink(missing_ok=True)
    check("0 分不刷新纪录", records.submit({"score": 0}) is False)

    # 打包成 exe 后数据目录要锚在可执行文件旁边（onefile 的临时目录会被删）。
    # 这里只验"锚点真的生效"，不验 PyInstaller 本身 —— 那部分只能靠手动打一次包。
    # 注意 RECORD_FILE 的优先级高于 DATA_DIR（测试覆盖优先），所以要先清掉它，
    # 否则这条断言测的是上一条测试留下的显式路径。
    from sneak_ai.core import settings
    records.RECORD_FILE = None
    records.DATA_DIR = settings.DATA_DIR = tmp / "dist"
    (tmp / "dist").mkdir()
    check("DATA_DIR 生效后 records 落在 dist/",
          records.path().parent == tmp / "dist")
    check("DATA_DIR 生效后 settings 落在 dist/",
          settings.path().parent == tmp / "dist")
    records.DATA_DIR = settings.DATA_DIR = None


# ---------- 结算页 ----------
def test_end_screen():
    from sneak_ai.__main__ import Game
    import audit_ui

    g = Game()
    w = fresh_world()
    w.elapsed, w.spotted, w.kills = 68.0, 2, 1
    w.peak_threat, w.decoy_hits, w.shield_kills = 0.2, 4, 1
    g.hud.draw_end(g.screen, w, {"score": 431, "time": 72.3, "rank": "B"},
                   True, 0.5)
    check("结算页（含满额风格分 + 新纪录）绘制不崩", True)

    # 明细行数变了也不能压到底部：这一条由 audit_ui 的最坏情况兜着
    groups = audit_ui.collect(g)
    bad = []
    for _label, rects in groups:
        bad += audit_ui.find_overlaps(rects)
    total = sum(len(r) for _l, r in groups)
    detail = "; ".join(f"{a.topleft}×{b.topleft}" for a, _, b, _, _ in bad)
    check(f"结算页与 HUD 零重叠（{total} 个落点）{detail}", not bad)


def main() -> int:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="w14_"))
    test_formula()
    test_style()
    test_records(tmp)
    test_end_screen()

    failed = [n for n, ok in _results if not ok]
    if failed:
        print(f"\n{len(failed)} 项失败: {failed}")
        return 1
    print("\nW14 计分系统 测试全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
