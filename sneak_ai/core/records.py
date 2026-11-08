"""最佳记录持久化：JSON 落在项目根目录，不依赖任何第三方库。

故意做得极简（读失败就当没有记录），因为"存档"不是这个项目的重点，
但"有记录可追"是玩家愿意再玩一局的最短路径。

存档里其实是**两套纪录**，可比范围不同，不能混在一起：

- 整局纪录（顶层字段）：从第 1 关起步、一路打到底的那一条，跨五关累计。
- 单关纪录（`per_level`）：只认"从第 N 关起步、且打的就是第 N 关"的成绩。

为什么单关纪录必须限定"起步那一关"：从第 1 关连打到第 3 关时，
`elapsed` 里已经含了前两关的时间，拿去和"直接挑战第 3 关"的成绩比，
练习成绩永远低于正式成绩 —— 那不是玩家变差了，是两个项目不同。
所以只有 `level_index == _start_level` 的那一关才进单关榜（判定在 Game 里，
这里只负责校验和落盘）。
"""

import datetime
import json
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .. import constants as C

# 时间戳格式与写入点共用同一个常量：写的是 "%Y-%m-%d %H:%M"，
# 读的时候也按这个形状校验，两边各写一份字符串迟早会不一致。
WHEN_FMT = "%Y-%m-%d %H:%M"

# 单关纪录在存档里的字段名。单独一个字段而不是塞进主记录，是因为两者的
# 可比范围不同：整局纪录跨五关，单关纪录只认第几关，混在一起会互相覆盖。
PER_LEVEL_KEY = "per_level"

# 单关纪录的键是关卡序号的字符串。上限取得比关卡数宽，是为了"关卡数变了"
# 也不丢内容 —— 但必须有上限：这个字段来自外部文件，一个手改的
# `"per_level": {"-1": ...}` 不该被当成有效关卡画到菜单上。
MAX_LEVEL_KEY = 32

# 允许测试覆盖（避免测试污染真实记录文件）
RECORD_FILE: Optional[Path] = None
# 允许入口覆盖数据目录（打包成 exe 后要落在可执行文件旁边，见 __main__）
DATA_DIR: Optional[Path] = None


def _running_from_scripts() -> bool:
    """当前进程是不是从 scripts/ 目录里跑出来的（测试 / benchmark / 录 GIF）。

    必须判这个，是因为它**真的污染过玩家的存档**：
    `test_gameplay` 里有一局 0.1 秒的假通关，得分 1000 分，比任何真实成绩都高，
    于是玩家自己的最佳纪录被这一局顶掉了 ——
    之后无论怎么打，结算页都显示"最佳 1000 分 / S 评级"且永远不是新纪录。

    约定"测试自己设置 RECORD_FILE"只写在注释里是不够的，
    没人会记得加这一行。所以改成入口自动隔离：
    从 scripts/ 启动就写临时文件，玩家永远看不到这些成绩。
    """
    try:
        entry = Path(sys.argv[0]).resolve()
        if entry.parent.name == "scripts":
            return True
    except Exception:
        pass
    try:
        return Path.cwd().resolve().name == "scripts"
    except Exception:
        return False


def path() -> Path:
    if RECORD_FILE is not None:
        return Path(RECORD_FILE)
    if DATA_DIR is not None:
        return Path(DATA_DIR) / C.RECORD_PATH
    if _running_from_scripts():
        return Path(tempfile.gettempdir()) / "sneak_ai_records_test.json"
    return Path(__file__).resolve().parents[2] / C.RECORD_PATH


def _as_int(value, default: int = 0) -> int:
    """把任意内容安全地转成整数。

    为什么需要它：`submit()` 原本直接 `int(old.get("score"))`。
    records.json 只要被人手改过、或者被旧版本写成 `"score": null`，
    `int(None)` 就会抛 TypeError —— 而且是在**通关那一瞬间**抛的，
    表现为"结算画面刚出来，窗口就没了"。存档是玩家唯一会碰到的外部数据，
    对它的每一个假设都可能被打破。
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _as_when(value) -> str:
    """把任意内容规整成时间戳字符串；不认识的形状一律当"没有"。

    校验形状而不是原样透传，是因为这个字符串会**直接画到结算页上**：
    存档被人手改过、或者将来字段被挪用作别的用途，屏幕上就会出现
    "最佳纪录 创于 待定" 这种东西。长度 + 分隔符位置对得上才认。
    """
    if not isinstance(value, str):
        return ""
    s = value.strip()
    if (len(s) == 16 and s[4] == "-" and s[7] == "-"
            and s[10] == " " and s[13] == ":"):
        return s
    return ""


def _mtime_when() -> str:
    """老存档没有时间戳：用文件修改时间兜底。

    `records.json` 只在**刷新最佳纪录**的那一处被写入（`submit()`），
    所以它的 mtime 近似等于纪录诞生的时刻。这比在界面上显示"未知"有用，
    也不必为了补一个字段去改写玩家的存档 —— 存档是唯一只属于他的东西，
    能不动就不动。
    """
    try:
        ts = path().stat().st_mtime
    except OSError:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime(WHEN_FMT)
    except (OSError, OverflowError, ValueError):
        return ""


def _clean_level(value) -> dict:
    """把一条单关纪录规整成界面能直接用的形状；不像就返回空 dict。

    只留界面真会读的字段（分数/用时/评级/发现/击倒/时间），不把整条
    `record()` 原样塞进去：单关纪录没有"打了几关"这种概念，
    留着它迟早会被哪个界面当成"整局纪录"读出来。
    """
    if not isinstance(value, dict):
        return {}
    score = _as_int(value.get("score"), 0)
    if score <= 0:
        return {}
    t = value.get("time")
    return {
        "score": score,
        "time": float(t) if isinstance(t, (int, float)) else 0.0,
        "spotted": _as_int(value.get("spotted"), 0),
        "kills": _as_int(value.get("kills"), 0),
        "rank": value["rank"] if isinstance(value.get("rank"), str) else "-",
        "when": _as_when(value.get("when")),
    }


def _clean_per_level(value) -> dict:
    """规整整个 `per_level` 字段：坏键、坏值一律丢掉，绝不抛异常。

    这个字段是**玩家可以直接编辑的文件**的一部分。一个手写的 `"-1"`、
    一个被旧版本写成 `null` 的条目，都不该让菜单在启动时崩掉 ——
    存档的唯一职责是"读不出来就当没有"，不是"报告用户文件坏了"。
    """
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.isdigit():
            continue
        idx = int(key)
        if not 0 <= idx <= MAX_LEVEL_KEY:
            continue
        row = _clean_level(item)
        if row:
            out[str(idx)] = row
    return out


def level_best(best: dict, idx: int) -> dict:
    """取第 idx 关的最佳纪录；没有就返回空 dict（调用方按"暂无"处理）。"""
    per = (best or {}).get(PER_LEVEL_KEY) or {}
    return per.get(str(idx)) or {}


def _write(data: dict) -> bool:
    try:
        with open(path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, TypeError):
        return False
    return True


def load() -> dict:
    """读最佳记录。任何格式问题都当"没有记录"，绝不让坏存档挡住游戏。"""
    try:
        with open(path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # 字段类型也一并规整：HUD 会拿这些值做格式化，
    # 一个字符串 score 会让结算页画出 "1000abc" 这种东西
    clean = {}
    if "score" in data:
        clean["score"] = _as_int(data.get("score"), 0)
    if "time" in data:
        t = data.get("time")
        clean["time"] = float(t) if isinstance(t, (int, float)) else 0.0
    if "spotted" in data:
        clean["spotted"] = _as_int(data.get("spotted"), 0)
    if "kills" in data:
        clean["kills"] = _as_int(data.get("kills"), 0)
    if isinstance(data.get("rank"), str):
        clean["rank"] = data["rank"]
    # 风格分明细：老版本存档里没有这两个字段，缺了就是空列表 / 0，
    # 结算页与"最佳纪录"都按"没有明细"处理，不会 KeyError。
    style = data.get("style")
    if isinstance(style, list):
        rows = []
        for item in style:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                rows.append({"name": item["name"],
                             "value": _as_int(item.get("value"), 0)})
        clean["style"] = rows
    else:
        clean["style"] = []
    clean["style_total"] = _as_int(data.get("style_total"), 0)
    clean["when"] = _as_when(data.get("when"))
    # 老版本存档没有 when 字段：用文件 mtime 补一个"大概是什么时候"。
    # 只在真有成绩时补 —— 空存档不该在菜单上显示"最佳纪录 创于某时"。
    if not clean["when"] and clean.get("score"):
        clean["when"] = _mtime_when()
    # 这条纪录是跑了几关的成绩。单关时代的存档没有这个字段（读到 0），
    # 而"一关 97 秒 / 612 分"和"两关 121 秒 / 800 分"根本不是一个项目，
    # 摆在一起比会让人以为打多了反而变差。界面据此加一句"旧版"，不改存档。
    clean["levels"] = _as_int(data.get("levels"), 0)
    # 老存档没有这个字段 —— 而"没有这个字段"只可能来自单关时代（那时游戏只有一关），
    # 所以缺失等于 1，而不是"未知"。不补这一条的话，玩家那份 612 分的旧纪录
    # 会带着"看起来和两关成绩同一量级"的样子继续挂在菜单上。
    if not clean["levels"] and clean.get("score"):
        clean["levels"] = 1
    # 单关纪录：老存档（v1.8.7 之前）没有这个字段，读到的是空表 ——
    # 界面上表现为"每关都还没有成绩"，而不是"存档坏了"。
    clean[PER_LEVEL_KEY] = _clean_per_level(data.get(PER_LEVEL_KEY))
    return clean


def legacy_label(best: dict, total_levels: int) -> str:
    """纪录是不是"不同关卡数"时代的产物；是就返回一句说明，否则空串。"""
    n = _as_int((best or {}).get("levels"), 0)
    if not n or n == total_levels:
        return ""
    return f"旧版 {n} 关"


def when_label(best: dict) -> str:
    """给界面用的时间短语（"创于 2026-10-31 15:52"）；没有就返回空串。

    放在这里而不是让菜单和结算页各拼一遍：两处显示必须永远一致，
    否则总有一天会变成"菜单写着 10-31、结算页写着 11-01"。
    """
    when = _as_when((best or {}).get("when"))
    return f"创于 {when}" if when else ""


def submit(record: dict) -> bool:
    """提交本局成绩；刷新了最佳记录返回 True。

    0 分永远不算纪录：失败局现在返回 0 分，而 `0 <= -1` 是 False ——
    不显式拦住的话，第一个失败局就会把"暂无纪录"写成"0 分 / C 评级"，
    之后玩家每次通关都被它判成"新纪录"，最佳纪录也就没有意义了。
    """
    score = _as_int(record.get("score"))
    if score <= 0:
        return False
    old = load()
    if score <= _as_int(old.get("score"), -1):
        return False
    # 整局纪录和单关纪录写在同一个文件里，**必须互相保留**。
    # 这里如果直接 `json.dump(record)`，玩家每破一次整局纪录，
    # 辛苦刷出来的单关成绩就全被清空 —— 而且不会有任何报错，
    # 只表现为"菜单里那些关的纪录又没了"。两个写入口都做合并，缺一不可。
    merged = dict(record)
    if old.get(PER_LEVEL_KEY):
        merged[PER_LEVEL_KEY] = old[PER_LEVEL_KEY]
    return _write(merged)


def submit_level(level_index: int, record: dict) -> bool:
    """提交一条单关成绩；刷了该关最佳返回 True。

    "哪一关的成绩"由调用方担保（必须是起步关），这里只做两件校验：
    分数 > 0、关卡序号在合理范围内。分数比较和整局纪录同口径 ——
    都用 `score()`，所以"慢但干净"的一局有可能赢过"快但开枪"的一局，
    这正是计分公式想说的事，不该在纪录层另立一套。
    """
    score = _as_int(record.get("score"))
    if score <= 0:
        return False
    idx = _as_int(level_index, -1)
    if not 0 <= idx <= MAX_LEVEL_KEY:
        return False
    old = load()
    per = dict(old.get(PER_LEVEL_KEY) or {})
    if score <= _as_int((per.get(str(idx)) or {}).get("score"), -1):
        return False
    row = _clean_level(record)
    if not row:
        return False
    per[str(idx)] = row
    # 同样要合并：只替换 per_level，整局纪录原样写回。
    merged = {k: v for k, v in old.items() if k != PER_LEVEL_KEY}
    merged[PER_LEVEL_KEY] = per
    return _write(merged)
