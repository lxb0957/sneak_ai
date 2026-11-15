"""W40：版本号同步 —— 发版时"改了代码忘了改文案"这件事必须变红。

## 为什么需要这个

版本号在这个项目里没有单一真相源，实际是**手抄 8 处**：

    sneak_ai/__init__.py          __version__
    assets/itch/page-description.md   selfcheck 里那个 exe 文件名
    assets/itch/promo-copy.md         事实卡的"版本"一行
    assets/itch/publish-checklist.md  上传路径
    assets/itch/devlog-1.md           头注 "Posted with the v..."
    docs/releases/vX.Y.Z.md           发布正文（文件名 + 标题）
    README / BLOG                     历史叙事（**不该被这个测试约束**）

前六处是"指向当前产物"的引用：说错了玩家会去下载一个不存在的文件名，
或者照着清单传了上一版的 exe —— 上一次真发生过（清单指着 v1.8.11）。
后两处是历史记录，写着 v1.5、v1.8.6 都是对的，所以**不能全局断言版本一致**。

所以这个脚本只钉两件事：

1. **凡是指向产物的引用，版本必须等于 `__version__`** —— 没写不报，写错就红。
2. **`docs/releases/v{__version__}.md` 必须存在**，且标题里带版本号。

另外顺手查一件跟版本无关、但同属"发版才想起来"的事：
上架清单里不许有本机绝对路径（`C:\\Users\\...`）。清单是给未来的自己
和可能的合作者看的，钉死在一个用户名上等于换个环境就照做不了。
"""

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[OK] {name}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAIL.append(name)


VERSION = None


def _current_version() -> str:
    """从 __init__.py 读，绝不在这里再抄一份（抄了就会不同步）。"""
    ns: dict = {}
    src = (ROOT / "sneak_ai" / "__init__.py").read_text(encoding="utf-8")
    exec(compile(src, "sneak_ai/__init__.py", "exec"), ns)
    return str(ns["__version__"])


# ── 1. 版本号本身 ────────────────────────────────────────────────


def test_version_is_sane() -> None:
    check("__version__ 是 x.y.z 形式", bool(re.fullmatch(r"\d+\.\d+\.\d+", VERSION)),
          f"实际 {VERSION!r}")
    # 三段的第二、三段都不该有前导零，也不该出现 1.8.013 这种
    parts = VERSION.split(".")
    check("版本各段没有前导零", all(p == "0" or not p.startswith("0") for p in parts),
          VERSION)


# ── 2. 指向产物的引用必须同步 ────────────────────────────────────

# exe 文件名是"最常见的一处遗漏"：改文案的人记得改正文，忘了改文件名
_EXE = re.compile(r"StealthProtocol-v(\d+\.\d+\.\d+)-win64\.exe")

# 事实卡里的表格行：| 版本 | v1.8.13 ... |
_FACT_VER = re.compile(r"^\|\s*版本\s*\|\s*v(\d+\.\d+\.\d+)", re.MULTILINE)

# devlog 头注：Posted with the v1.8.13 build.
_POSTED = re.compile(r"Posted with the v(\d+\.\d+\.\d+) build")


def _doc_files() -> list[Path]:
    """只查"给玩家的物料"，不碰 docs/releases 里的历史正文。"""
    out = []
    itch = ROOT / "assets" / "itch"
    if itch.is_dir():
        out += sorted(p for p in itch.glob("*.md"))
    readme = ROOT / "README.md"
    if readme.exists():
        out.append(readme)
    return out


def test_exe_names_match() -> None:
    """凡是写了 exe 文件名的地方，版本必须是当前版本。"""
    bad = []
    for p in _doc_files():
        for m in _EXE.finditer(p.read_text(encoding="utf-8", errors="replace")):
            if m.group(1) != VERSION:
                bad.append(f"{p.relative_to(ROOT)} 写成 v{m.group(1)}")
    check("exe 文件名版本一致", not bad,
          "；".join(bad) + f"（当前 v{VERSION}）" if bad else "")


def test_fact_card_matches() -> None:
    """promo-copy 的事实卡是对外口径，错了会被引到帖子里。"""
    p = ROOT / "assets" / "itch" / "promo-copy.md"
    if not p.exists():
        check("事实卡版本一致", True, "(没有 promo-copy.md，跳过)")
        return
    hits = _FACT_VER.findall(p.read_text(encoding="utf-8", errors="replace"))
    check("事实卡版本一致", all(h == VERSION for h in hits),
          f"写成 {hits}，当前 v{VERSION}")


def test_devlog_header_matches() -> None:
    bad = []
    for p in sorted((ROOT / "assets" / "itch").glob("devlog-*.md")):
        for m in _POSTED.finditer(p.read_text(encoding="utf-8", errors="replace")):
            if m.group(1) != VERSION:
                bad.append(f"{p.name} 写着 v{m.group(1)}")
    check("devlog 头注版本一致", not bad,
          "；".join(bad) + f"（当前 v{VERSION}）" if bad else "")


# ── 3. 发布正文必须存在 ──────────────────────────────────────────


def test_release_notes_exist() -> None:
    p = ROOT / "docs" / "releases" / f"v{VERSION}.md"
    check(f"docs/releases/v{VERSION}.md 存在", p.exists(),
          "（发版前先写正文，否则 Release 页是空的）")
    if not p.exists():
        return
    head = p.read_text(encoding="utf-8", errors="replace")[:400]
    check("发布正文标题带版本号", VERSION in head,
          "（复制上一版正文最容易忘的就是改标题）")


def test_no_orphan_newer_notes() -> None:
    """反向检查：有比 __version__ 更新的正文 = 版本号忘了 bump。"""
    d = ROOT / "docs" / "releases"
    if not d.is_dir():
        return

    def key(v: str) -> tuple:
        return tuple(int(x) for x in v.split("."))

    ver = key(VERSION)
    newer = [p.name for p in d.glob("v*.md")
             if (m := re.fullmatch(r"v(\d+\.\d+\.\d+)\.md", p.name))
             and key(m.group(1)) > ver]
    check("没有比 __version__ 更新的发布正文", not newer,
          f"存在 {newer}，说明 __init__.py 忘了 bump")


# ── 4. 上架清单不许有本机绝对路径 ────────────────────────────────


def test_checklist_has_no_abs_path() -> None:
    """清单是给别人照做的，钉死 C:\\Users\\<某个人> 就等于换台机器照做不了。"""
    p = ROOT / "assets" / "itch" / "publish-checklist.md"
    if not p.exists():
        check("清单无本机绝对路径", True, "(没有清单，跳过)")
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    bad = [ln.strip() for ln in text.splitlines()
           if re.search(r"[A-Za-z]:\\{1,2}Users\\{1,2}", ln)]
    check("清单无本机绝对路径", not bad,
          f"仍有 {len(bad)} 行，例如：{bad[0][:80] if bad else ''}")


def main() -> int:
    global VERSION
    VERSION = _current_version()
    print(f"当前版本 v{VERSION}\n")
    test_version_is_sane()
    test_exe_names_match()
    test_fact_card_matches()
    test_devlog_header_matches()
    test_release_notes_exist()
    test_no_orphan_newer_notes()
    test_checklist_has_no_abs_path()
    if FAIL:
        print(f"\nW40 失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("\nW40 全部通过：版本只有一处真相，其余引用都对得上")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
