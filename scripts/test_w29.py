"""W29：单关纪录 —— 让"练一关"这件事终于有反馈。

## 为什么需要这个

在这之前，`_handle_scoring` 里有一句 `if self._start_level > 0: return`：
从第 N 关起步的这一局**完全不提交成绩**。理由是对的 —— 从第 3 关起步
35 秒就能"打完三关"，交了就是一条刷得掉的假纪录。

但代价没人算过：第 4 关的机器人胜率只有 25%，一个卡在那里的人，
跳过去练十遍、每遍都在进步，屏幕上一句话都没有。**练习没有反馈，
玩家就没有理由再点一次开始。**

所以拆成两套纪录：
- 整局纪录（顶层字段）：只认从第 1 关起步、一路打到底的那一条，口径完全不变；
- 单关纪录（`per_level`）：只认 `level_index == _start_level` 的那一关。

## 这组测试守四件事

1. **起步关过关就进单关榜**（跳关练习终于有分）；
2. **后面几关不进**：从第 1 关连打到第 2 关时，第 2 关的 elapsed 里含着
   第 1 关的时间，记进去会让练习分永远低于正式分 —— 这是**错误方向**，
   比"漏记"严重得多，必须有断言；
3. **两套纪录写同一个文件，谁都不能把谁冲掉**（这是最容易写错的一处，
   而且是静默的：不会报错，只表现为"我的纪录怎么没了"）；
4. **结算页拿对纪录比**：跳关局不能把跨五关的整局纪录摆在下面，
   否则玩家看到的是一条永远够不着的数字。

用法：
    python scripts/test_w29.py
"""

from __future__ import annotations

import json
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
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core import records  # noqa: E402


class _TmpRecord:
    """把存档指到临时目录；退出时恢复，别碰玩家真实的 records.json。"""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sneak_w29_"))
        self.old = (records.DATA_DIR, records.RECORD_FILE)
        records.DATA_DIR, records.RECORD_FILE = self.dir, None
        return records.path()

    def __exit__(self, *exc):
        records.DATA_DIR, records.RECORD_FILE = self.old
        return False


def _win(g: Game, score: int = 900) -> None:
    """把这局摆成"刚通关"的状态，分数可控（不动真实计分逻辑）。"""
    g.world.state = C.STATE_VICTORY
    g.world.score = lambda: score          # type: ignore[method-assign]
    g._scored = False
    g._next_timer = 0.0


def test_start_level_is_recorded():
    """从第 4 关起步、打赢第 4 关 → 写进 per_level['3']，不碰整局纪录。"""
    with _TmpRecord() as path:
        g = Game()
        g.menu.sel = 3
        g._on_menu_start()
        _win(g, 812)
        g._handle_scoring()

        data = json.loads(path.read_text(encoding="utf-8"))
        per = data.get(records.PER_LEVEL_KEY) or {}
        assert "3" in per, f"起步关的成绩没落盘：{data}"
        assert per["3"]["score"] == 812, per["3"]
        assert "score" not in data, f"跳关局不该写整局纪录：{data}"
        assert g.new_level_record is True, "破了纪录却没置标志，过场不会报喜"
    print("[OK] 跳关打赢起步关 → 进单关榜（812 分），整局纪录纹丝不动")


def test_later_levels_are_not_recorded():
    """从第 1 关连打到第 2 关：第 1 关进榜，第 2 关**不进**。

    错误方向在这儿：第 2 关的 elapsed 已经含了第 1 关的时间，
    记进去等于给练习成绩打了一个永远追不上的折扣。
    """
    with _TmpRecord() as path:
        g = Game()
        g.menu.sel = 0
        g._on_menu_start()

        _win(g, 700)            # 第 1 关
        g._handle_scoring()
        assert g._next_timer > 0, "第 1 关过关后没有进过场"

        g._next_timer = 0.0
        g._advance_level()      # 进第 2 关
        _win(g, 750)            # 第 2 关
        g._handle_scoring()

        per = json.loads(path.read_text(encoding="utf-8")).get(
            records.PER_LEVEL_KEY) or {}
        assert per.get("0", {}).get("score") == 700, f"第 1 关没记上：{per}"
        assert "1" not in per, f"第 2 关被记进单关榜了（带着第 1 关的时间）：{per}"
    print("[OK] 连打时只有起步关进榜；第 2 关带着前一关的时间，不记")


def test_two_record_kinds_do_not_clobber():
    """两套纪录同写一个文件：互相必须保留。

    这一处写错是**静默**的 —— 不报错，只表现为"我刷的那些关怎么没了"。
    """
    with _TmpRecord() as path:
        g = Game()
        g.menu.sel = 3
        g._on_menu_start()
        _win(g, 800)
        g._handle_scoring()

        # 再来一局，从第 1 关起步打到底，破整局纪录
        g2 = Game()
        g2.menu.sel = 0
        g2._on_menu_start()
        g2.level_index = len(L.LEVELS) - 1
        g2._new_world()
        _win(g2, 999)
        g2._handle_scoring()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("score") == 999, f"整局纪录没写进去：{data}"
        assert (data.get(records.PER_LEVEL_KEY) or {}).get("3", {}).get("score") \
            == 800, f"破整局纪录时把单关榜冲掉了：{data}"

        # 反方向：再破一次单关，整局纪录必须还在
        R = records.submit_level(3, {"score": 850, "time": 40.0, "rank": "S"})
        assert R is True, "更高的单关成绩没被接受"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("score") == 999, f"破单关纪录时把整局纪录冲掉了：{data}"
        assert data[records.PER_LEVEL_KEY]["3"]["score"] == 850, data
    print("[OK] 整局纪录与单关纪录互不覆盖（两个方向都验过）")


def test_end_screen_compares_against_right_record():
    """跳关局结算页比"本关最佳"，正式局比"最佳纪录"。"""
    with _TmpRecord():
        # 先造两组纪录：整局 999，第 5 关 400
        records.submit({"score": 999, "time": 120.0, "rank": "S", "levels": 5})
        records.submit_level(4, {"score": 400, "time": 90.0, "rank": "C"})

        jump = Game()
        jump.menu.sel = 4
        jump._on_menu_start()
        jump.best = records.load()
        best, label = jump._end_best()
        assert label == "本关最佳", f"跳关局的标题不对：{label}"
        assert best.get("score") == 400, \
            f"跳关局拿了整局纪录来比：{best}（应该拿到第 5 关的 400 分）"

        full = Game()
        full.menu.sel = 0
        full._on_menu_start()
        full.best = records.load()
        best2, label2 = full._end_best()
        assert label2 == "最佳纪录", f"正式局的标题不对：{label2}"
        assert best2.get("score") == 999, f"正式局没拿整局纪录：{best2}"
    print("[OK] 跳关局比本关最佳（400 分）；正式局比整局最佳（999 分）")


def test_dirty_per_level_is_survivable():
    """手改过的存档不能让菜单崩：坏键坏值一律丢掉，好的一起留下。"""
    dirty = {
        "score": 500, "levels": 5,
        "per_level": {
            "2": {"score": 600, "time": 50.0, "rank": "A"},
            "-1": {"score": 999},                    # 负数关卡不存在
            "abc": {"score": 999},                   # 不是数字
            "3": {"score": "oops"},                  # 分数不是数字
            "4": None,                               # 整个条目是 null
            "5": {"score": -3},                      # 负数分数
            "99": {"score": 999},                    # 超出上限
        },
    }
    with _TmpRecord() as path:
        path.write_text(json.dumps(dirty, ensure_ascii=False), encoding="utf-8")
        best = records.load()
        per = best.get(records.PER_LEVEL_KEY)
        assert list(per.keys()) == ["2"], f"脏键没被清掉：{per}"
        assert per["2"]["score"] == 600, per["2"]
        assert records.level_best(best, 9) == {}, "不存在的关卡应该返回空 dict"
        assert records.level_best({}, 0) == {}, "空存档也要能问"
    print("[OK] 脏 per_level 只留下合法那条，其余静默丢弃，不抛异常")


def test_stale_save_without_per_level():
    """加单关纪录之前的老存档没有 per_level 字段，读到的是空表而不是报错。"""
    with _TmpRecord() as path:
        path.write_text(json.dumps({"score": 1284, "time": 210, "rank": "A",
                                    "levels": 5}), encoding="utf-8")
        best = records.load()
        assert best.get("score") == 1284, "老存档读不出分数了"
        assert best.get(records.PER_LEVEL_KEY) == {}, \
            f"老存档应该给出空表：{best.get(records.PER_LEVEL_KEY)}"
        # 老存档之上追加一条单关成绩，整局纪录要保住
        assert records.submit_level(0, {"score": 846, "time": 66.3, "rank": "S"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("score") == 1284, f"升级存档时丢了整局纪录：{data}"
    print("[OK] 老存档无 per_level → 空表；在其上新增单关成绩不丢整局纪录")


def main():
    print("=== W29：单关纪录 ===")
    test_start_level_is_recorded()
    test_later_levels_are_not_recorded()
    test_two_record_kinds_do_not_clobber()
    test_end_screen_compares_against_right_record()
    test_dirty_per_level_is_survivable()
    test_stale_save_without_per_level()
    print("\nW29 全部通过：跳关练习有分、后面几关不掺、两套纪录互不覆盖。")


if __name__ == "__main__":
    main()
