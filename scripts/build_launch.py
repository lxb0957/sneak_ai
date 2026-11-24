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
    python scripts/build_launch.py            # 组装 dist/launch/（含便携包，50 MB）
    python scripts/build_launch.py --check    # 只检查，不写
    python scripts/build_launch.py --web-only # 组装 dist/launch_web/（不含 exe，24 MB）
    python scripts/build_launch.py --pages    # 组装 dist/pages/（GitHub Pages 用，50 MB）

`--web-only` 是给 Cloudflare Pages 这类「单文件上限 25 MB」的托管用的：便携包 26.3 MB
会超限，所以干脆不放，下载按钮改指向 GitHub Releases。上传这些平台用
`dist/launch_web.zip`（见 --zip）。

`--pages` 产出 GitHub Pages 用户主页（`lxb0957.github.io`）用的目录：**便携包一起放**，
这样下载和在线玩都在同一个域名下，不依赖 `github.com` 网页（国内常打不开）。
GitHub Pages 没有那道 WAF，所以恢复正常的 `.zip` 后缀；再加 `.nojekyll` 关掉 Jekyll
（否则 `_` 开头的文件会被吞）。部署：`cd dist/pages && git add -A && git push`。
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


GH_RELEASES = "https://github.com/lxb0957/sneak_ai/releases/latest"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    ap.add_argument("--web-only", action="store_true", help="不含 26 MB 便携包（单文件上限 25 MB 的托管用）")
    ap.add_argument("--pages", action="store_true", help="产出 GitHub Pages 目录 dist/pages（含便携包，正常 .zip 名）")
    ap.add_argument("--zip", action="store_true", help="另外打一个 zip，方便网页版拖拽上传")
    args = ap.parse_args()

    if args.pages:
        out = ROOT / "dist" / "pages"
    else:
        out = ROOT / "dist" / ("launch_web" if args.web_only else "launch")

    missing = [p for p in (WEB_SITE / "index.html", LANDING / "index.html") if not p.exists()]
    if missing:
        print("缺文件：" + "、".join(str(m) for m in missing))
        print("先跑 python scripts/build_web.py --verify")
        return 1
    zip_path = newest_release()
    print(f"网页版: {WEB_SITE}")
    print(f"便携包: {zip_path.name} ({zip_path.stat().st_size / 1048576:.1f} MB)"
          + ("（--web-only，不打包进去）" if args.web_only else ""))
    if args.check:
        return 0

    sync_tree(LANDING, out, skip=("build", "assets", "sneak_ai"))
    sync_tree(WEB_SITE, out / "play", skip=("cdn",))            # 坑 1：cdn 提到根
    sync_tree(WEB_SITE / "cdn", out / "cdn")
    (out / "play" / "web.tar.gz").unlink(missing_ok=True)       # 与 web.pkg 重复
    if not (out / "cdn" / "0.9.3" / "pythons.js").exists():
        raise SystemExit("cdn/0.9.3/pythons.js 不在站点根，pygbag 会 404")

    if args.web_only:
        # 落地页的下载按钮改成 GitHub Releases，别留一个 404 的本地路径
        page = out / "index.html"
        s = page.read_text(encoding="utf-8")
        s = s.replace(
            'href="download/stealth-protocol-win64" download="StealthProtocol-v1.8.14-win64.zip"',
            f'href="{GH_RELEASES}"',
            1,
        )
        s = s.replace("下载 Windows 版 · 26 MB", "下载 Windows 版", 1)
        page.write_text(s, encoding="utf-8")
    else:
        (out / "download").mkdir(parents=True, exist_ok=True)
        if args.pages:
            # Pages 没有 WAF，用正常 .zip 名，并把按钮指过去（同源下载，不依赖 github.com）
            shutil.copy2(zip_path, out / "download" / ZIP_NAME)
            (out / ".nojekyll").write_text("", encoding="utf-8")
            page = out / "index.html"
            s = page.read_text(encoding="utf-8")
            s = s.replace(
                'href="download/stealth-protocol-win64" download="StealthProtocol-v1.8.14-win64.zip"',
                f'href="download/{ZIP_NAME}" download="{ZIP_NAME}"',
                1,
            )
            page.write_text(s, encoding="utf-8")
        else:
            shutil.copy2(zip_path, out / "download" / DL_NAME)  # 坑 2

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    biggest = max((f.stat().st_size, f.name) for f in out.rglob("*") if f.is_file())
    print(f"\n组装完成: {out}  ({size / 1048576:.1f} MB)")
    print(f"  最大单文件: {biggest[1]} {biggest[0] / 1048576:.1f} MB")
    dl = GH_RELEASES if args.web_only else "download/" + (ZIP_NAME if args.pages else DL_NAME)
    print("  index.html 下载按钮 -> " + dl)
    if args.pages:
        print("  部署: cd dist/pages && git add -A && git commit && git push")

    if args.zip:
        z = ROOT / "dist" / (out.name + ".zip")
        z.unlink(missing_ok=True)
        shutil.make_archive(str(z.with_suffix("")), "zip", root_dir=out)
        print(f"  上传包: {z}  ({z.stat().st_size / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
