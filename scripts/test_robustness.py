"""健壮性测试：外部数据、启动环境都不该让游戏"一闪了事"。

为什么单独一个文件：这些用例不属于任何一版玩法功能（W3/W6/W10…），
它们针对的是**"游戏死得不明不白"**这一类问题 ——
它不产生错误画面、不抛给你看，只会让窗口消失，所以也最难复现。

覆盖：
1. 存档损坏（手改 / 旧版本残留 / 写入被中断）不能让通关那一瞬间崩溃；
2. 任何未捕获异常都必须留下 crash.log，main() 返回非 0；
3. 屏幕放不下时窗口按比例降级，小地图视野框跟着改；
4. 无头环境（自动化测试、录 GIF）必须保持标准分辨率，不许偷偷缩窗口。

用法：
    python scripts/test_robustness.py
"""

import json
import math
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame

from sneak_ai import __main__ as M
from sneak_ai import constants as C
from sneak_ai.core import records


def _tmp(d):
    records.RECORD_FILE = Path(d) / "records.json"


# ---------------------------------------------------------------- 存档容错
def test_corrupted_record_never_crashes():
    """手改过的 records.json 必须被当成"没有记录"，而不是抛 TypeError。

    这是真会发生的：玩家想看看存档长什么样，改完忘了改回来。
    修之前 `int(old.get("score"))` 会在**通关瞬间**炸掉 ——
    表现就是"结算画面刚出现，窗口就没了"。
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "records.json"
        cases = [
            ('{"score": null, "time": "x", "rank": "A"}', "score 是 null"),
            ('{"score": "六百", "time": 90}', "score 是中文字符串"),
            ('{"score": [1, 2], "spotted": {}}', "score 是列表"),
            ('{"time": 12.5}', "缺少 score 字段"),
            ('not json at all', "根本不是 JSON"),
            ('[]', "JSON 是数组而不是对象"),
            ('{"score": true}', "score 是布尔值"),
        ]
        for text, why in cases:
            p.write_text(text, encoding="utf-8")
            _tmp(d)
            try:
                best = records.load()
                # 读出来就得能用：HUD 会拿它做格式化，字段必须是规整的类型
                assert isinstance(best, dict), why
                if "score" in best:
                    assert isinstance(best["score"], int), why
                if "time" in best:
                    assert isinstance(best["time"], float), why
                # 提交也不能抛
                records.submit({"score": 500, "time": 80.0, "spotted": 1,
                                "kills": 0, "rank": "B"})
            except Exception as exc:
                raise AssertionError(f"{why} 时不该抛异常：{type(exc).__name__}: {exc}")
        print("[OK] 7 种损坏存档都能读、能提交，不抛异常")
    records.RECORD_FILE = None


def test_record_file_missing_is_fine():
    """第一次玩没有 records.json：load 返回空 dict，submit 负责创建。"""
    with tempfile.TemporaryDirectory() as d:
        _tmp(d)
        assert records.load() == {}, "不存在的存档应返回空"
        assert records.submit({"score": 300, "time": 90.0, "spotted": 2,
                               "kills": 0, "rank": "B"}) is True
        assert records.load()["score"] == 300
        # 分数更低就不该覆盖
        assert records.submit({"score": 100, "time": 99.0, "spotted": 5,
                               "kills": 1, "rank": "C"}) is False
        assert records.load()["score"] == 300
        print("[OK] 首次存档创建 + 低分不覆盖最佳纪录")
    records.RECORD_FILE = None


def test_scripts_never_touch_the_real_record():
    """从 scripts/ 跑出来的进程必须写临时存档。

    这条守的是一个已经发生过的伤害：某测试里有局 0.1 秒的假通关，
    拿了 1000 分，直接顶掉玩家真实的最佳纪录 —— 之后无论怎么打，
    结算页都显示"最佳 1000 分 / S 评级"，而且永远拿不到新纪录。
    """
    project = Path(__file__).resolve().parents[1]
    real = project / "records.json"
    before = real.read_text(encoding="utf-8") if real.exists() else None

    records.RECORD_FILE = None
    assert records._running_from_scripts(), "从 scripts/ 启动应当被判定为测试运行"
    assert records.path() != real, "测试脚本不许指向玩家的 records.json"

    # 就算submit 一个不可能被超越的高分，真实存档也一个字节都不能变
    records.submit({"score": 999999, "time": 0.01, "spotted": 0,
                    "kills": 0, "rank": "S"})
    after = real.read_text(encoding="utf-8") if real.exists() else None
    assert before == after, "真实存档被测试改动了"
    print(f"[OK] 脚本运行写临时存档 {records.path().name}，真实 records.json 未受影响")
    records.RECORD_FILE = None


def test_real_entry_writes_project_record():
    """反过来也要成立：正式入口（python -m sneak_ai）必须写项目根的存档。"""
    old_argv = sys.argv[0]
    sys.argv[0] = str(Path(__file__).resolve().parents[1] / "sneak_ai" / "__main__.py")
    try:
        assert not records._running_from_scripts(), "正式入口不该被当成测试"
        assert records.path().name == C.RECORD_PATH
    finally:
        sys.argv[0] = old_argv
    print("[OK] 正式入口仍然写入项目根的 records.json")


# ---------------------------------------------------------------- 崩溃留痕
def test_crash_writes_log_and_returns_nonzero():
    """崩溃必须留下 crash.log，并且 main() 返回 1（供 run.bat 判断）。

    闪退之所以难修，是它什么都不告诉开发者。这条测试守的是
    "最小可诊断性"：哪怕游戏崩了，也必须有一份堆栈躺在磁盘上。
    """
    log_path = Path(M.__file__).resolve().parents[1] / "crash.log"
    if log_path.exists():
        log_path.unlink()

    class Boom(RuntimeError):
        pass

    original_run = M.Game.run
    M.Game.run = lambda self: (_ for _ in ()).throw(Boom("测试用崩溃"))
    # 错误页会停 8 秒等按键；预先塞一个 KEYDOWN 让它立刻退出
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a))
    try:
        rc = M.main()
    finally:
        M.Game.run = original_run

    assert rc == 1, f"崩溃时 main() 应返回 1，实际 {rc}"
    assert log_path.exists(), "crash.log 没生成"
    text = log_path.read_text(encoding="utf-8")
    assert "Boom" in text, "日志里没有异常类型"
    assert "test_crash_writes_log" in text, "日志里没有调用栈（无法定位）"
    log_path.unlink()
    print("[OK] 崩溃 → 返回码 1 + crash.log 含完整调用栈")


# ---------------------------------------------------------------- 窗口适配
def test_window_shrinks_when_screen_is_small():
    """屏幕放不下 1280x960 时，按比例降级并同步视野格常量。

    以前这里是一行裸 set_mode，笔记本 / 远程桌面直接抛 pygame.error，
    进程瞬间死掉 —— 这就是"闪退"最典型的成因。
    """
    old_env = os.environ.get("SDL_VIDEODRIVER")
    old = (C.WINDOW_W, C.WINDOW_H, C.VIEW_W, C.VIEW_H)
    os.environ.pop("SDL_VIDEODRIVER", None)
    real_info = pygame.display.Info
    pygame.display.Info = lambda *a, **k: types.SimpleNamespace(
        current_w=1366, current_h=768)
    try:
        surf = M._create_window()
        w, h = surf.get_size()
        assert w <= 1366 and h <= 768, f"降级后仍然放不下：{w}x{h}"
        assert w < 1280, "小屏下应该缩小，而不是硬顶一个超大窗口"
        assert abs(w / h - 1280 / 960) < 1e-6, f"比例被破坏：{w}x{h}"
        assert C.WINDOW_W == w and C.WINDOW_H == h, "常量没同步，HUD 会画歪"
        assert C.VIEW_W == w // C.TILE_SIZE, "视野格宽没同步，小地图框会偏"
        assert C.VIEW_H == h // C.TILE_SIZE, "视野格高没同步，小地图框会偏"
        print(f"[OK] 1366x768 屏幕 → 窗口降到 {w}x{h}（视野 {C.VIEW_W}x{C.VIEW_H} 格）")
    finally:
        pygame.display.Info = real_info
        if old_env is not None:
            os.environ["SDL_VIDEODRIVER"] = old_env
        C.WINDOW_W, C.WINDOW_H, C.VIEW_W, C.VIEW_H = old
        pygame.display.set_mode((C.WINDOW_W, C.WINDOW_H))


def test_headless_keeps_standard_resolution():
    """无头驱动报的是 1024x768 假尺寸，按它降级会把截图/GIF 全改小。"""
    old_env = os.environ.get("SDL_VIDEODRIVER")
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    try:
        surf = M._create_window()
        assert surf.get_size() == (1280, 960), (
            f"无头环境必须保持 1280x960，实际 {surf.get_size()}")
        print("[OK] 无头环境保持 1280x960（截图与基准不受影响）")
    finally:
        if old_env is None:
            os.environ.pop("SDL_VIDEODRIVER", None)
        else:
            os.environ["SDL_VIDEODRIVER"] = old_env


def test_hitmark_renders_across_full_life():
    """命中记号从出生到消散全程绘制都不得崩溃。

    踩坑：颜色公式 `255*a + 30` 在 a≈1（刚命中的那一帧）时算出 285，
    超出 pygame 颜色通道上限 255，直接抛 ValueError。最阴险的地方在于
    它**只在真打中人的那一帧触发**——随机压力测试的枪几乎全打在墙上，
    从不产生 hitmark，所以 3000 次随机操作都没撞上它。
    教训：凡是"状态 A 会改变绘制参数"的特效，必须把生命周期逐帧扫一遍。
    """
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from sneak_ai.__main__ import Game

    g = Game()
    world = g.world
    world.hit_marks = [{
        "x": world.player.x, "y": world.player.y - 0.7,
        "age": 0.0, "life": 0.22,
    }]
    mark = world.hit_marks[0]
    steps = 40
    for i in range(steps + 1):
        mark["age"] = mark["life"] * i / steps   # 扫过 0 → 全生命周期
        g._render(1 / 60)                        # 真实绘制路径，不是绕过渲染的断言
    print(f"[OK] hitmark 全生命周期 {steps + 1} 帧绘制无崩溃")


# ---------------------------------------------------------------- 入口
def main():
    pygame.init()
    # 无头环境下先建一个窗口，后续用例才有 surface 可用
    if pygame.display.get_surface() is None:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.display.set_mode((C.WINDOW_W, C.WINDOW_H))

    print("=== 健壮性测试：崩溃 / 存档 / 启动 ===")
    test_corrupted_record_never_crashes()
    test_record_file_missing_is_fine()
    test_scripts_never_touch_the_real_record()
    test_real_entry_writes_project_record()
    test_crash_writes_log_and_returns_nonzero()
    test_window_shrinks_when_screen_is_small()
    test_headless_keeps_standard_resolution()
    test_hitmark_renders_across_full_life()
    print("\n健壮性测试全部通过 ✅")


if __name__ == "__main__":
    main()
