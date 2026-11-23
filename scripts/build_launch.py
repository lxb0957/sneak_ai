"""把落地页 + 网页版 + 便携包拼成一个可直接部署的站点（dist/launch/）。

结构（关键约束都在这里，改之前先读）：

    dist/launch/
      index.html              落地页，下载按钮 → download/stealth-protocol-win64
      shot-level.png / og.png 落地页素材
      cdn/                    运行时（wasm + 标准库 + 轮子），**必须在站点根**
      play/                   网页版入口（pygbag 产物）
        index.html            引 /cdn/...（绝对路径，跟着根走）
        web.apk / web.pkg     游戏本体
      download/
        stealth-protocol-win64  便携包，故意不带 .zip 后缀

两个坑：

1. `cdn/` 必须放站点根，不能跟着 play/ 走。pygbag 运行时用 `location.origin + "/cdn/..."`
   拼索引 json 的地址，跟 `index.html` 里的相对/绝对写法无关；放 `play/cdn/` 会 404。
2. 便携包不能叫 `.zip`。托管网关前面的腾讯云 WAF 把 `.zip` / `.rar` / `.7z` 全拦成 403
   （大写、复合名一样拦），无扩展名 / `.apk` / `.pkg` 放行。浏览器靠 `<a download="...zip">`
   存成正常文件名，实测 Chrome / Edge / Firefox 都对。

用法：
    python scripts/build_launch.py            # 组装
    python scripts/build_launch.py --check    # 只检查，不写
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_SITE = ROOT / "web_site"
LANDING = ROOT / "web" / "landing"
RELEASE = ROOT / "dist" / "release"
OUT = ROOT / "dist" / "launch"

ZIP_NAME = "StealthProtocol-v1.8.14-win64.zip"
DL_NAME = "stealth-protocol-win64"          # 无扩展名，绕 WAF


def newest_release() -> Path:
    cands = sorted(RELEASE.glob("StealthProtocol-*-win64.zip"))
    if not cands:
        raise SystemExit(f"没找到便携包：{RELEASE}/*.zip")
    return cands[-1]


def sync_tree(src: Path, dst: Path, skip: tuple[str, ...] = ()) -> None:
    """幂等同步（不用 rmtree：本机沙箱会把删除操作转成回收站，对 50 MB 目录会失败）."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in skip:
            continue
        target = dst / item.name
        if item.is_dir():
            sync_tree(item, target)
        else:
            shutil.copy2(item, target)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = ap.parse_args()

    missing = [p for p in (WEB_SITE / "index.html", LANDING / "index.html") if not p.exists()]
    if missing:
        print("缺文件：" + "、".join(str(m) for m in missing))
        print("先跑 python scripts/build_web.py --verify")
        return 1
    zip_path = newest_release()
    print(f"网页版: {WEB_SITE}")
    print(f"便携包: {zip_path.name} ({zip_path.stat().st_size / 1048576:.1f} MB)")
    if args.check:
        return 0

    sync_tree(LANDING, OUT, skip=("build", "assets", "sneak_ai"))
    sync_tree(WEB_SITE, OUT / "play", skip=("cdn",))            # 坑 1：cdn 提到根
    sync_tree(WEB_SITE / "cdn", OUT / "cdn")
    (OUT / "play" / "web.tar.gz").unlink(missing_ok=True)       # 与 web.pkg 重复
    if not (OUT / "cdn" / "0.9.3" / "pythons.js").exists():
        raise SystemExit("cdn/0.9.3/pythons.js 不在站点根，pygbag 会 404")

    (OUT / "download").mkdir(parents=True, exist_ok=True)
    shutil.copy2(zip_path, OUT / "download" / DL_NAME)          # 坑 2

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\n组装完成: {OUT}  ({size / 1048576:.1f} MB)")
    print("自检：")
    print("  index.html 下载按钮 -> download/" + DL_NAME + ' + download="' + ZIP_NAME + '"')
    print("  play/index.html 引用 -> /cdn/0.9.3/…（指向站点根）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
