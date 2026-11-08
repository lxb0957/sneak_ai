"""W31：难度表那两行数字，必须能由脚本重跑出来。

## 为什么需要这个

README 的难度表里「最短路线」曾经是手记的：第 1 关按"往返合计"写 235、
第 4 关按"单程"写 88、第 3 关改过地图之后 238 一直没跟着改（真实 192）。
三个数并排放在一张表里，读者没法比较，改图的人也不知道该改哪个 ——
而**没有任何东西会因此报错**，这才是最贵的部分。

同一张表的「最小割」也在说假话：第 5 关写着"4 格（4 道闸）"，
但堵住任意一道闸这一关就断了，真实值是 1。把"有 4 道闸"当成"最小割是 4"，
是把**关闸数量**和**割的大小**混成了一件事。

## 这组测试守三件事

1. `scripts/audit_routes.py` 量的是 `LEVELS` 注册表，不是某一张手写地图 ——
   加第六关时它自动覆盖，不需要有人记得去改脚本；
2. README 那两行与脚本输出**逐关一致**（脚本是尺子，文档是读数，不能各说各的）；
3. 口径自洽：`格 = 步 + 段数`，`段数 = 数据节点数 + 1`。

用法：
    python scripts/test_w31.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import audit_routes as AR  # noqa: E402
from sneak_ai.core.levels import LEVELS  # noqa: E402

README = ROOT / "README.md"


def _cells(text: str) -> list:
    """把一行 markdown 表格拆成单元格文字（去掉 ** 加粗）。"""
    body = text.strip().strip("|")
    return [c.replace("*", "").strip() for c in body.split("|")]


def _row(label: str, width: int) -> list:
    """在 README 里找以 `| label |` 开头、且正好有 width 个单元格的那一行。

    难度表里有两处「最短路线」：一处是单关（第 1 关）的设计说明，一处是
    五关对照。用单元格数区分，比按行号找稳 —— 上面加一段话就不会错位。
    """
    hits = [_cells(ln) for ln in README.read_text(encoding="utf-8").splitlines()
            if ln.startswith(f"| {label} |")]
    for cells in hits:
        if len(cells) == width:
            return cells
    raise AssertionError(f"README 里找不到 {width} 格的「{label}」行，"
                         f"实际找到 {[len(h) for h in hits]} 格的行")


def _first_int(cell: str) -> int:
    m = re.search(r"(\d+)", cell)
    assert m, f"单元格里没有数字：{cell!r}"
    return int(m.group(1))


# ---------------------------------------------------------------- 1. 口径
def test_route_definition_is_self_consistent():
    """格 = 步 + 段数，段数 = 节点数 + 1，五关都走通。"""
    for lv in LEVELS:
        st = AR.route_stats(lv)
        assert st["ok"], f"第 {lv.idx + 1} 关有目标点走不通：{st['legs']}"
        assert len(st["legs"]) == len(lv.terminals) + 1, st
        assert st["cells"] == st["steps"] + len(st["legs"]), st
        for i, step in enumerate(st["legs"]):
            assert isinstance(step, int) and step > 0, (lv.name, i, step)
        # 一条路线至少得穿过一道门 —— 五关都是门控关卡，0 说明门没被算进去
        assert st["doors"] > 0, (lv.name, st)
    print("[OK] 口径自洽：格 = 步 + 段数，五关的目标点都走得到")


# ---------------------------------------------------------------- 2. 路线
def test_readme_shortest_route_matches_levels():
    """README 五关那行的「X 格」= route_stats() 算出来的格数。"""
    cells = _row("最短路线", len(LEVELS) + 1)
    for lv, cell in zip(LEVELS, cells[1:]):
        want = AR.route_stats(lv)["cells"]
        got = _first_int(cell)
        assert got == want, (
            f"第 {lv.idx + 1} 关的「最短路线」写着 {got} 格，"
            f"脚本量出来是 {want} 格 —— 改图之后忘了改表，或者口径又分叉了")
    print("[OK] 五关「最短路线」与脚本逐关一致："
          + " / ".join(f"{AR.route_stats(lv)['cells']} 格" for lv in LEVELS))


# ---------------------------------------------------------------- 3. 单关说明
def test_single_level_row_agrees_with_table():
    """设计说明里那行（第 1 关）和五关对照表是同一个数，不能各写一份。"""
    single = _row("最短路线", 3)
    table = _row("最短路线", len(LEVELS) + 1)
    assert _first_int(single[1]) == _first_int(table[1]), \
        f"第 1 关写了两遍最短路线：{single[1]!r} vs {table[1]!r}"

    l1 = AR.route_stats(LEVELS[0])
    m = re.search(r"穿\s*(\d+)\s*道门", single[1])
    assert m, f"第 1 关那行没写穿几道门：{single[1]!r}"
    assert int(m.group(1)) == l1["doors"], \
        f"第 1 关写着穿 {m.group(1)} 道门，脚本量出来 {l1['doors']} 道"
    print(f"[OK] 第 1 关两处写法一致：{l1['cells']} 格、穿 {l1['doors']} 道门")


# ---------------------------------------------------------------- 4. 最小割
def test_readme_min_cut_matches_levels():
    """「最小割 X 格」= min_cut_size(出生格 → 第一个数据节点)。"""
    cells = _row("最小割", len(LEVELS) + 1)
    for lv, cell in zip(LEVELS, cells[1:]):
        grid = lv.build()
        want = AR.min_cut_size(grid, AR._cell(lv.spawn),
                               AR._cell(lv.terminals[0]))
        got = _first_int(cell)
        assert got == want, (
            f"第 {lv.idx + 1} 关的「最小割」写着 {got} 格，"
            f"脚本量出来是 {want} 格 —— 别把「有几道门」当成「最小割」")
    print("[OK] 五关「最小割」与脚本逐关一致")


# ---------------------------------------------------------------- 5. 门禁用
def test_audit_script_runs_clean():
    """审计脚本自己能跑完并返回 0（CI 把它当门禁用）。"""
    assert AR.main() == 0
    print("[OK] audit_routes.py 跑通，退出码 0")


def main() -> int:
    test_route_definition_is_self_consistent()
    test_readme_shortest_route_matches_levels()
    test_single_level_row_agrees_with_table()
    test_readme_min_cut_matches_levels()
    test_audit_script_runs_clean()
    print("\nW31 全部通过：难度表那两行，脚本说了算")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
