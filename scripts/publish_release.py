"""建 / 更新一个 Release 的元数据（标题、正文、发布状态），不带附件。

    python scripts/publish_release.py --tag v1.8.7 \
        --title-file rel/title.txt --body-file rel/notes_full.md

附件由 `gh release upload --clobber` 单独负责 —— 两件事分开，
因为它们的失败模式完全不同：上传是带宽问题，改元数据是 API 语义问题。

## 为什么不用 `gh release edit`

踩出来的。这个仓库上，对 release 发一个**不带 `tag_name` 的 PATCH** 会稳定 500：

    PATCH {name}                    -> 500
    PATCH {body}                    -> 500
    PATCH {name, body, draft}       -> 500
    PATCH {name, body, draft, tag_name} -> OK

`gh release edit TAG --title ... --notes-file ...` 用的是 release **id** 定位，
payload 里不带 `tag_name` —— 于是它每次都 500，而同样的字段手动带上 tag_name 就过了。

诱因大概率是"删掉 tag 再重新打"：release 内部记的那条 ref 关系脏了，
服务端更新时要重新按 tag 解析才能定位，不带就连不上。
（这也解释了第一次 `gh release create` 为什么建完 draft、传完附件，
最后"转已发布"那一步 500 —— 同一条路径。）

所以这里显式带 `tag_name`：**这个字段不是冗余，是定位方式。**
反过来讲，也记住一条：`gh` 封装得越好，出这种"封装替你做了一个你没想到的决定"
的时候越难查 —— 因为报错只有一行 HTTP 500，没有 payload。

## 幂等

先按 tag 查：
- 查到 → PATCH（带上 tag_name、name、body、draft=false）
- 404  → POST 新建（同样是 published 态）

两种情况都收敛到"存在且已发布"，所以重跑安全。
如果上次停在 draft（附件在、版本没对外），PATCH 里的 `draft=false` 会把它推出去。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _token() -> str:
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v
    print("缺少 GH_TOKEN / GITHUB_TOKEN", file=sys.stderr)
    raise SystemExit(2)


def _repo() -> str:
    v = os.environ.get("GITHUB_REPOSITORY")
    if v:
        return v
    print("缺少 GITHUB_REPOSITORY（形如 owner/repo）", file=sys.stderr)
    raise SystemExit(2)


def api(method: str, path: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=body, method=method,
        headers={
            "Authorization": "Bearer " + _token(),
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def tag_exists(repo: str, tag: str) -> bool:
    try:
        api("GET", f"/repos/{repo}/git/ref/tags/{tag}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="建/更新 Release 元数据（不带附件）")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--title-file", required=True)
    ap.add_argument("--body-file", required=True)
    a = ap.parse_args()

    repo = _repo()
    tag = a.tag
    title = Path(a.title_file).read_text(encoding="utf-8").strip()
    body = Path(a.body_file).read_text(encoding="utf-8")

    if not tag_exists(repo, tag):
        # 不能靠 POST /releases 顺手建 tag：那样 tag 会指到默认分支 HEAD，
        # 悄悄给出一个"看起来成功了、其实指错提交"的发布。
        print(f"tag {tag} 不存在，拒绝创建 Release", file=sys.stderr)
        return 1

    # tag_name 必须带：见文件头。它同时决定了"更新哪一个"。
    payload = {"tag_name": tag, "name": title, "body": body,
               "draft": False, "prerelease": False}

    try:
        cur = api("GET", f"/repos/{repo}/releases/tags/{tag}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        rel = api("POST", f"/repos/{repo}/releases", payload)
        action = "新建"
    else:
        rel = api("PATCH", f"/repos/{repo}/releases/{cur['id']}", payload)
        action = "更新"

    print(f"[OK] {action} Release {rel['tag_name']}  draft={rel['draft']}  {rel['html_url']}")
    print(f"     正文 {len(body)} 字符，附件 {len(rel.get('assets', []))} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
