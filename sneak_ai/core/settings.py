"""设置持久化：目前只有"音效开关"，但结构上留好了加键位/亮度的位置。

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


def save(muted: bool) -> None:
    """写静音偏好。写失败就静默放弃 —— 偏好丢了不致命，游戏挂了才致命。"""
    try:
        with open(path(), "w", encoding="utf-8") as f:
            json.dump({"muted": bool(muted)}, f)
    except OSError:
        pass
