"""生成一次发布要用的正文与标题。

用法：
    python scripts/release_notes.py --tag v1.8.7 --out-dir dist/rel
    # 产出 dist/rel/notes.md 和 dist/rel/title.txt

## 两条来源，按优先级

1. **`docs/releases/<tag>.md` 存在就用它**（人写的）。首行写 `# 一句话说这一版干嘛`，
   这行会被抽出来当 Release 标题。
2. 不存在就**按上一个 tag 之后的 commit 自动生成**。

为什么保留这两条而不是只做自动生成：commit 列表能回答"改了哪些行"，
回答不了"这一版是干嘛的"。Release 页面绝大多数人是先看标题和第一段，
再决定要不要下载 —— 那一段必须是**写的**，不能是**拼的**。
自动生成存在的意义，是保证"忘了写 notes 也不会发出一版空正文"。

## 为什么标题要单独一个文件

`gh release create` 的 `--title` 和 `--notes-file` 是两个参数。
标题里有中文、有破折号，直接塞进 shell 变量再拼进命令行，在 Windows runner 上
会被 cp936 / cp65001 来回转码咬到。写成文件让 gh 自己去读，只把文件名告诉它。
"""

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES_DIR = ROOT / "docs" / "releases"

INSTALL_TAIL = """
## 怎么装

下载下面的 `StealthProtocol-{tag}-win64.exe`，双击即玩，不用装 Python。

- 首次启动会解包到临时目录，慢 1~2 秒是正常的，之后恢复正常
- 存档（`records.json`）和设置（`settings.json`）写在 **exe 旁边**，不在系统目录
- 想先确认它能在这台机器上跑：`StealthProtocol-{tag}-win64.exe --selfcheck`
  （跳过菜单，五关各跑 120 帧，退出码就是结论）
"""


def _git(*args: str) -> str:
    r = subprocess.run(("git",) + args, cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def previous_tag(tag: str) -> str:
    """本 tag 之前最近的一个 tag。第一个 tag 时返回空串（Git 会非零退出）。"""
    return _git("describe", "--tags", "--abbrev=0", f"{tag}^")


def commits_since(prev: str, tag: str) -> list:
    rng = f"{prev}..{tag}" if prev else tag
    out = _git("log", "--no-merges", "--pretty=format:%s%x1f%h", rng)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        subject, _, sha = line.partition("\x1f")
        rows.append((subject.strip(), sha.strip()))
    return rows


def auto_body(tag: str, prev: str, commits: list) -> str:
    if commits:
        lines = [f"## 改动（{len(commits)} 个提交）", ""]
        for subject, sha in commits:
            lines.append(f"- {subject}（{sha}）")
        body = "\n".join(lines)
    else:
        body = "## 改动\n\n（这个 tag 和上一个 tag 之间没有提交）"

    span = f"{prev} → {tag}" if prev else f"首个发布（{tag}）"
    return f"```\n{span}\n```\n\n{body}\n{INSTALL_TAIL.format(tag=tag)}"


def written_body(tag: str) -> tuple:
    """读手写 notes。返回 (body, 首行标题或空串)。"""
    path = NOTES_DIR / f"{tag}.md"
    if not path.exists():
        return None, ""
    text = path.read_text(encoding="utf-8")
    title = ""
    m = re.match(r"^#\s+(.+?)\s*$", text.splitlines()[0] if text else "")
    if m:
        title = m.group(1)
        # 正文里去掉这一行（它已经变成 Release 标题了），保留其余结构
        text = "\n".join(text.splitlines()[1:]).lstrip("\n")
    return text, title


def build_title(tag: str, headline: str) -> str:
    if not headline:
        return tag
    if headline.startswith(tag):
        return headline
    return f"{tag} —— {headline}"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Release 正文与标题")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-dir", default="dist/rel")
    ap.add_argument("--print", action="store_true", help="只打印，不写文件")
    a = ap.parse_args()

    body, headline = written_body(a.tag)
    source = "docs/releases"
    if body is None:
        prev = previous_tag(a.tag)
        body = auto_body(a.tag, prev, commits_since(prev, a.tag))
        source = f"git log（{prev or '起点'} → {a.tag}）"
    title = build_title(a.tag, headline)

    if a.print:
        print(f"[title] {title}\n[source] {source}\n")
        print(body)
        return 0

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "notes.md").write_text(body, encoding="utf-8", newline="\n")
    (out / "title.txt").write_text(title, encoding="utf-8", newline="\n")
    print(f"[OK] {a.tag} 标题：{title}")
    print(f"     正文来源：{source}（{len(body)} 字符）")
    print(f"     写到：{out / 'notes.md'} / {out / 'title.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
