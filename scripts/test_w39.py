"""W39：中英双语 —— 中文一个像素不动，英文一个汉字不漏。

## 为什么需要这个

界面双语有两种坏结果，都不会让任何现成测试变红：

1. **中文画面悄悄变了。** 翻译层接在 `ui.render` / `TextCache.get` 上，
   那是最热的两条路径。改错一个字符、把 `t()` 写成会返回新对象的版本，
   中文界面就会有肉眼看不出的偏移。
2. **英文界面漏中文。** 199 条字面量散在 9 个模块里，翻完一版看着都对，
   但只要有一条走的是没被覆盖的分支（某一关的某个提示），英文玩家就会
   在最关键的那句话上看到方块字 —— 而方块字不会让任何断言失败。

所以这个脚本钉四件事：

1. `t()` 在中文模式下**恒等**：跑遍所有界面，每一条上屏文本都原样返回；
2. 英文模式下跑同样一遍，**一条中文都不许剩**；
3. 源码里的字面量（含极难走到的分支）逐条查表，白名单外必须翻得到；
4. 切语言后旧缓存必须失效（否则菜单会一直吐中文）。
"""

import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame  # noqa: E402

from sneak_ai.core import i18n  # noqa: E402
from sneak_ai.core import settings  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[OK] {name}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAIL.append(name)


# 扫描范围：**会往屏幕上写字**的模块
_UI_MODULES = (
    "core/hud.py", "core/menu.py", "core/levels.py", "core/world.py",
    "entity/enemy.py", "core/debrief.py", "core/tutorial.py",
    "core/minimap.py", "core/records.py", "__main__.py",
)

# 白名单：这些字符串**从不上屏**，翻了也没人看；或者只是拼句子用的碎片。
# 每一条都要说得出理由，否则"白名单"会慢慢变成"漏译收纳箱"。
_WHITELIST = {
    # 建图自检的异常文本（只在关卡数据写错时出现在 traceback 里）
    "关卡不连通：", " 个可走格从出生点不可达，例如 ",
    # 巡逻点 / 闸门 / 捷径的内部命名，只用于诊断与生成器日志
    "S 东门", "北弧", "G1 西门", "G1 东门", "G2 西门", "G2 南门",
    "G3 北门", "G3 东门", "G4 西门", "G4 南门", "T 北门", "S 南门",
    "南弧", "G5 北门", "G5 东门", "G6 西门", "G6 东门", "G7 西门",
    "G7 南门", "T 西门", "X1 上口", "捷径", "X1 下口",
    "北廊西闸", "北廊东闸", "南廊西闸", "南廊东闸",
    "西廊北闸", "西廊南闸", "东廊北闸", "东廊南闸",
    "北门", "南门", "N1 口", "N2 口", "配电间西口", "配电间东口",
    "捷径北口", "捷径南口", "数据舱北门", "数据舱南门",
    # 拼接用的碎片（永远和别的字一起出现，整句已经单独翻过）
    " 分 · ", " 秒 · ", " 评级", "关｜", " 关 · ",
    # 底栏那一行的碎片（整行已按模板翻过）
    "    L 语言：",
}


def test_zh_is_identity() -> None:
    """中文模式下每一条上屏文本都必须原样返回。"""
    import i18n_scan

    i18n.set_lang(i18n.ZH)
    seen = i18n_scan.collect(i18n.ZH)
    bad = [s for s in seen if i18n.t(s) != s]
    check(f"中文模式恒等（{len(seen)} 条上屏文本）", not bad, f"变了：{bad[:3]}")


def test_en_has_no_chinese() -> None:
    """英文模式跑遍所有界面，屏幕上一个汉字都不该出现。"""
    import i18n_scan

    i18n.set_lang(i18n.EN)
    drawn = i18n_scan.collect(i18n.EN)
    left = sorted(s for s in drawn if CJK.search(s))
    check(f"英文模式无中文残留（{len(drawn)} 条上屏文本）", not left,
          f"\n        漏译 {len(left)} 条：{left[:6]}")
    i18n.set_lang(i18n.ZH)


def test_source_literals_covered() -> None:
    """源码字面量逐条查表：运行时不走到的分支也要有译文。"""
    import ast

    i18n.set_lang(i18n.EN)
    missing = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.hits = []

        def visit_Expr(self, node):
            pass          # 文档字符串 / 裸字符串

        def visit_Assert(self, node):
            pass          # 断言信息不上屏

        def visit_Constant(self, node):
            v = node.value
            if isinstance(v, str) and CJK.search(v) and len(v.strip()) >= 3:
                self.hits.append((node.lineno, v))

    for mod in _UI_MODULES:
        path = ROOT / "sneak_ai" / mod
        tree = ast.parse(path.read_text(encoding="utf-8"))
        v = V()
        v.visit(tree)
        for line, text in v.hits:
            if text in _WHITELIST:
                continue
            if CJK.search(i18n.t(text)):
                missing.append(f"{mod}:{line} {text!r}")

    i18n.set_lang(i18n.ZH)
    check(f"源码字面量全部有译文（白名单 {len(_WHITELIST)} 条除外）",
          not missing, f"\n        {len(missing)} 条没翻：{missing[:5]}")


def test_table_hygiene() -> None:
    """表本身要说实话：译文里不能有中文，key 必须是中文（防止误把英文当 key）。"""
    dirty = [k for k, v in i18n.TABLE.items() if CJK.search(v)]
    check("表里没有「译文仍是中文」的条目", not dirty, f"{dirty[:3]}")

    # 英文 key 是允许的（副标题就是"英文源串换一种英文说法"），
    # 真正该防的是两种：key==value 的无意义条目、以及译文会被再翻一次。
    self_map = [k for k, v in i18n.TABLE.items() if k == v]
    check("表里没有 key==value 的无意义条目", not self_map, f"{self_map[:3]}")

    i18n.set_lang(i18n.EN)
    again = [v for v in i18n.TABLE.values() if CJK.search(i18n.t(v))]
    i18n.set_lang(i18n.ZH)
    check("英文译文不会被再翻一次", not again, f"{again[:3]}")

    bad_tpl = [t for t, en in i18n.TEMPLATES.items()
               if CJK.search(en) or "{" in en.replace("{0}", "").replace("{1}", "")
               .replace("{2}", "").replace("{3}", "")]
    check("模板译文里没有中文/占位符错位", not bad_tpl, f"{bad_tpl[:3]}")

    # 数字替换：带小数的、整数的、坐标的都要走得通
    samples = {
        "断电 12s · 哨兵视野 ×0.5": "Blackout 12s · guard vision ×0.5",
        "第 3 关完成": "Level 3 cleared",
        "去查 (24,17)": "check (24,17)",
        "用时 66.3s    被发现 3 次": "Time 66.3s    spotted 3×",
    }
    i18n.set_lang(i18n.EN)
    got = {k: i18n.t(k) for k in samples}
    i18n.set_lang(i18n.ZH)
    wrong = {k: (v, got[k]) for k, v in samples.items() if got[k] != v}
    check("带数字的模板替换正确", not wrong, f"{wrong}")


def test_cache_invalidates_on_switch() -> None:
    """切语言后同一把 key 必须吐出新的文本。

    漏了这一步的表现是：菜单还挂着中文，而游戏内已经是英文 ——
    因为 TextCache 的签名只看 (text, size, color, bold)，而它缓存的是
    **已翻译**的文本，翻译发生在算签名之前才行。
    """
    from sneak_ai.core.ui import TextCache

    tc = TextCache()
    i18n.set_lang(i18n.ZH)
    zh = tc.get("k", "体力", 16, (200, 200, 200))
    i18n.set_lang(i18n.EN)
    en = tc.get("k", "体力", 16, (200, 200, 200))
    i18n.set_lang(i18n.ZH)
    check("切语言后文本缓存失效", zh.get_width() != en.get_width(),
          f"宽 {zh.get_width()} vs {en.get_width()}（相同 = 缓存没失效）")


def test_lang_persists() -> None:
    """L 键切完要落盘，下次进来还是这个语言。"""
    old = settings.SETTINGS_FILE
    settings.SETTINGS_FILE = Path(tempfile.gettempdir()) / "w39_settings.json"
    try:
        settings.SETTINGS_FILE.unlink(missing_ok=True)
        check("默认中文", settings.load_lang() == "zh", settings.load_lang())
        settings.save(lang="en")
        check("落盘后读回 en", settings.load_lang() == "en", settings.load_lang())
        settings.save(lang="zh")
        check("落盘后读回 zh", settings.load_lang() == "zh", settings.load_lang())
        # 坏值不许把界面变成英文
        settings.SETTINGS_FILE.write_text('{"lang": "de"}', encoding="utf-8")
        check("非法语言值回落到中文", settings.load_lang() == "zh",
              settings.load_lang())
    finally:
        settings.SETTINGS_FILE = old
        try:
            Path(tempfile.gettempdir(), "w39_settings.json").unlink(missing_ok=True)
        except OSError:
            pass


def test_menu_key_toggles() -> None:
    """按 L 切语言：界面语言、落盘、底栏文案三者必须同时变。"""
    import sneak_ai.core.menu as menu_mod

    old = settings.SETTINGS_FILE
    settings.SETTINGS_FILE = Path(tempfile.gettempdir()) / "w39_menu.json"
    try:
        settings.SETTINGS_FILE.unlink(missing_ok=True)
        i18n.set_lang(i18n.ZH)
        m = menu_mod.Menu({"levels": 0})
        before = m.foot_text(False)
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_l)

        class _Dummy:
            def toggle_mute(self):
                pass

            def play(self, *a, **kw):
                pass

        m.handle_event(ev, _Dummy())
        after = m.foot_text(False)
        check("L 键切换界面语言", i18n.lang() == i18n.EN, i18n.lang())
        check("底栏文案跟着变", before != after, f"{before!r} → {after!r}")
        check("切换后立即落盘", settings.load_lang() == "en", settings.load_lang())
        m.handle_event(ev, _Dummy())
        check("再按一次切回中文", i18n.lang() == i18n.ZH, i18n.lang())
    finally:
        settings.SETTINGS_FILE = old
        i18n.set_lang(i18n.ZH)
        try:
            Path(tempfile.gettempdir(), "w39_menu.json").unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    pygame.init()
    pygame.display.set_mode((64, 64))
    test_zh_is_identity()
    test_en_has_no_chinese()
    test_source_literals_covered()
    test_table_hygiene()
    test_cache_invalidates_on_switch()
    test_lang_persists()
    test_menu_key_toggles()
    pygame.quit()
    if FAIL:
        print(f"\nW39 失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("\nW39 全部通过：中文不动、英文不漏、开关记得住")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
