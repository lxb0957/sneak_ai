"""设置持久化：音效开关 + 辅助模式，结构上留好了加键位/亮度的位置。

为什么静音状态要落盘：玩家上一局按了 M，这一局开局又响起 BGM/枪声，
他会认为是 bug —— "我记得我关了"。偏好被记住 = 游戏尊重玩家。

读写策略和 records.py 完全一致：
- 读失败 / 格式不对 → 当默认值，绝不让坏文件挡住游戏；
- 从 scripts/ 启动（测试 / benchmark / 录 GIF）自动落到临时目录，
  不污染玩家的真实设置 —— 这个坑在 records.py 上真的踩过。
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .. import constants as C

# 允许测试覆盖（避免测试污染真实设置文件）
SETTINGS_FILE: Optional[Path] = None
# 允许入口覆盖数据目录（打包成 exe 后要落在可执行文件旁边，见 __main__）
DATA_DIR: Optional[Path] = None


def _running_from_scripts() -> bool:
    try:
        if Path(sys.argv[0]).resolve().parent.name == "scripts":
            return True
    except Exception:
        pass
    try:
        return Path.cwd().resolve().name == "scripts"
    except Exception:
        return False


def path() -> Path:
    if SETTINGS_FILE is not None:
        return Path(SETTINGS_FILE)
    if DATA_DIR is not None:
        return Path(DATA_DIR) / C.SETTINGS_PATH
    if _running_from_scripts():
        return Path(tempfile.gettempdir()) / "sneak_ai_settings_test.json"
    return Path(__file__).resolve().parents[2] / C.SETTINGS_PATH


def _load() -> dict:
    try:
        with open(path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_muted() -> bool:
    """读静音偏好。任何异常都当"未静音"。"""
    return _load().get("muted") is True


def load_assist() -> bool:
    """读辅助模式偏好。只有真正的 `true` 才算开 —— 坏文件不会替玩家开辅助。"""
    return _load().get("assist") is True


def load_taught() -> set:
    """读"已经说过的引导提示"。只认字符串列表，坏数据当没说过。"""
    raw = _load().get("taught")
    if not isinstance(raw, list):
        return set()
    return {k for k in raw if isinstance(k, str)}


def mark_taught(key: str) -> None:
    """记下一条说过的提示。落盘失败不影响这一局。"""
    keys = load_taught()
    keys.add(str(key))
    save(taught=keys)


def save(muted: bool = None, *, assist: bool = None,
         taught=None) -> None:
    """写偏好，**按键合并**，不是整份覆盖。

    为什么必须合并：这个文件里已经有不止一个键了。整份覆盖会让"按 A 开关
    辅助模式"顺手把玩家上次关掉的音效打开 —— 那是另一种"我记得我关了"。
    两个参数都是 None 时什么都不写（调用点漏传不该清空存档）。
    写失败就静默放弃 —— 偏好丢了不致命，游戏挂了才致命。
    """
    if muted is None and assist is None and taught is None:
        return
    data = _load()
    if muted is not None:
        data["muted"] = bool(muted)
    if assist is not None:
        data["assist"] = bool(assist)
    if taught is not None:
        # 排序写盘：文件 diff 里能看出"多说了哪一句"，而不是整行重排
        data["taught"] = sorted({str(k) for k in taught})
    try:
        with open(path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass
