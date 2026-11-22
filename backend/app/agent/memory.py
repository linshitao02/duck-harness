"""长期记忆和会话笔记。

长期记忆放在工作区 `.harness/memory/`：MEMORY.md 只做索引，每条记忆一个带 frontmatter 的主题文件。
会话笔记放在 `.harness/session_memory/{会话}.md`，按固定栏目更新，压缩时用来保住当前状态。
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("harness.memory")

MEMORY_TYPES = ("user", "feedback", "project", "reference")
TYPE_LABELS = {"user": "用户", "feedback": "反馈", "project": "项目", "reference": "参考"}
ENTRYPOINT = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
SESSION_INIT_CHARS = 16_000
SESSION_UPDATE_CHARS = 8_000
SESSION_TOOLS_BETWEEN = 3
SECTION_CHAR_CAP = 4_000

SESSION_SECTIONS: tuple[tuple[str, str], ...] = (
    ("会话标题", "_用 5 到 10 个词写清这次会话在做什么，信息要密，不要空话_"),
    ("当前状态", "_现在正在做什么？还没完成的事。下一步。_"),
    ("任务说明", "_用户要做什么？有哪些设计决定或背景_"),
    ("文件和函数", "_重要文件里有什么，为什么相关_"),
    ("常用流程", "_通常按什么顺序跑哪些命令，输出怎么看_"),
    ("错误和纠正", "_遇到过什么错误，怎么修好的。用户纠正了什么。哪些做法失败了不要再试_"),
    ("代码和系统", "_重要组件怎么配合_"),
    ("经验", "_什么有效，什么无效，要避开什么。不要重复其他栏目_"),
    ("关键结果", "_用户要的具体答案、表格或文档，把完整结果写在这里_"),
    ("工作记录", "_一步步做了什么，每步很短_"),
)


def memory_dir(workspace: Path) -> Path:
    return Path(workspace) / ".harness" / "memory"


def session_memory_path(workspace: Path, conversation_id: int) -> Path:
    return Path(workspace) / ".harness" / "session_memory" / f"{conversation_id}.md"


def _state_path(workspace: Path, conversation_id: int) -> Path:
    return session_memory_path(workspace, conversation_id).with_suffix(".state.json")


def _slug(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]+', "-", name).strip(" .")
    return (cleaned or "memory")[:80]


def _yaml(value: str) -> str:
    text = (value or "").replace("\n", " ").strip()
    if re.search(r'[:#"]', text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        raw = value.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        meta[key.strip()] = raw
    return meta, parts[2].strip()


def age_days(mtime: float) -> int:
    return max(0, int((datetime.now().timestamp() - mtime) // 86_400))


def age_label(mtime: float) -> str:
    days = age_days(mtime)
    if days == 0:
        return "今天"
    if days == 1:
        return "昨天"
    return f"{days} 天前"


def in_topic_memory(path: Path) -> bool:
    parts = path.parts
    for index, part in enumerate(parts[:-1]):
        if part == ".harness" and parts[index + 1] == "memory":
            return True
    return False


def freshness_prefix(path: Path) -> str:
    """超过一天的主题记忆，读的时候标出它是当时的观察。"""
    if path.name == ENTRYPOINT or not in_topic_memory(path) or not path.is_file():
        return ""
    try:
        days = age_days(path.stat().st_mtime)
    except OSError:
        return ""
    if days <= 1:
        return ""
    return (
        "<system-reminder>"
        f"这条记忆写于 {days} 天前。记忆是当时的观察，不是当前状态。"
        "涉及文件、函数或参数时，先核对现在的代码，再当成事实。"
        "</system-reminder>\n"
    )


def _records(workspace: Path) -> list[dict]:
    folder = memory_dir(workspace)
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.md")):
        if path.name == ENTRYPOINT:
            continue
        try:
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except OSError:
            continue
        memory_type = meta.get("type") if meta.get("type") in MEMORY_TYPES else "user"
        name = meta.get("name") or path.stem
        description = (meta.get("description") or body.splitlines()[0] if body else name).strip()
        found.append(
            {
                "slug": path.stem,
                "name": name,
                "description": description[:150],
                "type": memory_type,
                "type_label": TYPE_LABELS[memory_type],
                "body": body,
                "filename": path.name,
                "path": path,
                "age": age_label(mtime),
                "days": age_days(mtime),
            }
        )
    return found


def _index_text(records: list[dict]) -> str:
    lines = []
    for item in records:
        lines.append(f"- [{item['name']}]({item['filename']}) — {item['description']}（{item['age']}）")
    return "\n".join(lines)


def truncate_entrypoint(raw: str, max_lines: int = MAX_ENTRYPOINT_LINES, max_bytes: int = MAX_ENTRYPOINT_BYTES) -> str:
    trimmed = (raw or "").strip()
    if not trimmed:
        return ""
    lines = trimmed.split("\n")
    too_long = len(lines) > max_lines
    too_big = len(trimmed) > max_bytes
    if not too_long and not too_big:
        return trimmed
    clipped = "\n".join(lines[:max_lines]) if too_long else trimmed
    if len(clipped) > max_bytes:
        cut = clipped.rfind("\n", 0, max_bytes)
        clipped = clipped[: cut if cut > 0 else max_bytes]
    if too_big and not too_long:
        reason = f"{len(trimmed)} 字节，上限 {max_bytes} 字节"
    elif too_long and not too_big:
        reason = f"{len(lines)} 行，上限 {max_lines} 行"
    else:
        reason = f"{len(lines)} 行、{len(trimmed)} 字节"
    return clipped + f"\n\n> 警告：MEMORY.md 超过了限制（{reason}）。只载入了一部分。索引每行保持在大约 200 字以内，细节放到主题文件。"


def sync_index(workspace: Path) -> str:
    folder = memory_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    text = _index_text(_records(workspace))
    target = folder / ENTRYPOINT
    current = ""
    if target.is_file():
        try:
            current = target.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
    if current != text.strip():
        target.write_text((text + "\n") if text else "", encoding="utf-8")
    return text


def _find(workspace: Path, name: str) -> dict | None:
    wanted = (name or "").strip()
    if not wanted:
        return None
    slug = _slug(wanted)
    for item in _records(workspace):
        if item["slug"] == slug or item["name"] == wanted or item["filename"] == wanted:
            return item
    return None


def save_memory(workspace: Path, name: str, body: str, memory_type: str = "user", description: str = "") -> dict:
    title = (name or "").strip() or (body or "").strip().splitlines()[0][:40]
    content = (body or "").strip()
    if not title or not content:
        raise ValueError("记忆需要名称和正文")
    kind = (memory_type or "user").strip().lower()
    if kind not in MEMORY_TYPES:
        raise ValueError("type 只能是 user、feedback、project、reference")
    hook = (description or content.splitlines()[0]).strip().replace("\n", " ")[:150]
    folder = memory_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_slug(title)}.md"
    path.write_text(
        "---\n"
        f"name: {_yaml(title)}\n"
        f"description: {_yaml(hook)}\n"
        f"type: {kind}\n"
        "---\n\n"
        f"{content}\n",
        encoding="utf-8",
    )
    sync_index(workspace)
    found = _find(workspace, title)
    if found is None:
        raise ValueError("记忆没有写进去")
    return found


def forget_memory(workspace: Path, name: str) -> str:
    found = _find(workspace, name)
    if found is None:
        known = "、".join(item["name"] for item in _records(workspace)) or "（无）"
        raise ValueError(f"没有这条记忆。现有：{known}")
    found["path"].unlink(missing_ok=True)
    sync_index(workspace)
    return found["name"]


def read_memory(workspace: Path, name: str) -> str:
    found = _find(workspace, name)
    if found is None:
        known = "、".join(item["name"] for item in _records(workspace)) or "（无）"
        raise ValueError(f"没有这条记忆。现有：{known}")
    note = freshness_prefix(found["path"])
    return (
        f"{note}{found['name']}（{found['type_label']}，{found['age']}）\n"
        f"{found['description']}\n\n{found['body']}"
    )


def memory_prompt(workspace: Path) -> str:
    """系统提示里只放索引和行为规则，不放每条记忆的正文。"""
    folder = memory_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    index = truncate_entrypoint(sync_index(workspace))
    if not index:
        index = "索引还是空的。保存新记忆后，这里会出现一行指针。"
    root = folder.resolve()
    return f"""你有一套持久的文件记忆，目录是 `{root}`。这个目录已经存在，直接写入，不要先 mkdir，也不要先检查它在不在。

以后的会话还要用到的事，才放进这套记忆：这个用户是谁、希望怎么合作、哪些做法要避开或继续、以及当前代码和 git 看不出来的工作背景。

用户明确说记住什么，就立刻按最合适的类型保存。用户说忘掉什么，就找到对应条目并删除。

## 记忆类型

<types>
<type>
    <name>user</name>
    <description>用户的角色、目标、职责和知识背景。用来按这个人的情况调整以后的做法。不要记下对用户的负面评价，也不要记和工作无关的事。</description>
    <when_to_save>了解到用户的角色、偏好、职责或知识背景时</when_to_save>
    <how_to_use>解释和协作方式要贴着这个人。例如对熟悉后端的人讲前端，就用他已经知道的概念来对照。</how_to_use>
</type>
<type>
    <name>feedback</name>
    <description>用户教过你怎么做：既包括叫停的做法，也包括他确认有效的做法。只记纠正、不记确认，以后会越做越保守。正文先写规则，再写 **Why:** 和 **How to apply:**。知道原因，才能判断边界情况。</description>
    <when_to_save>用户纠正做法（不要这样、停下来）或确认一个并不显然的做法是对的（就这样、继续）时。把以后还适用的部分记下来，并写上为什么。</when_to_save>
    <how_to_use>让用户不用把同一条要求说第二遍。</how_to_use>
</type>
<type>
    <name>project</name>
    <description>进行中的工作、目标、事故或决定，而且这些不能从代码和 git 历史里看出来。相对日期要写成绝对日期。正文先写事实，再写 **Why:** 和 **How to apply:**。这类记忆过时得快，原因用来判断它还有没有用。</description>
    <when_to_save>知道谁在做什么、为什么、什么时候之前要完成时</when_to_save>
    <how_to_use>用来理解用户这次要求背后的限制，再决定怎么建议。</how_to_use>
</type>
<type>
    <name>reference</name>
    <description>外部系统里信息放在哪里。例如缺陷在哪个看板、告警看哪个面板。</description>
    <when_to_save>知道某个外部资源是干什么的时</when_to_save>
    <how_to_use>用户提到外部系统，或答案可能在外部系统里时</how_to_use>
</type>
</types>

## 不要存进记忆

- 代码写法、约定、架构、文件路径、项目结构。这些读当前项目就能得到。
- Git 历史、最近改动、谁改的。以 git log 和 git blame 为准。
- 调试步骤和修复配方。修法在代码里，上下文在提交说明里。
- 已经写在项目说明里的内容。
- 这一轮的临时进度、正在做的步骤、当前对话里的过程。

即使用户明确要求保存，上面这些也不要整段存进去。用户要你保存一份列表或活动摘要时，先问哪一点出乎意料或不显然，只记那一点。

## 怎么保存

保存分两步，`remember` 会一起做完：

1. 把记忆写到自己的文件，例如 `user_role.md`、`feedback_testing.md`。frontmatter 用下面的格式：

```markdown
---
name: 记忆名称
description: 一句话，以后靠它判断这条记不记得起来，所以要具体
type: user、feedback、project、reference 之一
---

正文。feedback 和 project 先写规则或事实，再写 **Why:** 和 **How to apply:**。
```

2. 在 `MEMORY.md` 加一行指针。`MEMORY.md` 是索引，不是记忆正文。每行一条，大约 150 字以内：`- [标题](file.md) — 一句话`。它没有 frontmatter。不要把记忆正文写进 `MEMORY.md`。

- `MEMORY.md` 每次都会载入对话。超过 {MAX_ENTRYPOINT_LINES} 行或 {MAX_ENTRYPOINT_BYTES} 字节的部分不会载入，所以索引要短。
- name、description、type 要和正文一致。
- 按主题组织，不要按时间堆。
- 错了或过时了就改掉或删掉。
- 不要记重复的。先看索引里有没有能更新的，再写新的。同名再次 remember 就是更新。

## 什么时候去读记忆

- 记忆看起来相关，或用户提到以前对话里的工作时。
- 用户明确要求检查、回忆或记住时，必须去读。
- 用户说忽略记忆或不要用记忆时，把 MEMORY.md 当作空的。不要使用、引用、对照或提起记忆里的内容。
- 记忆会过时。它记录的是当时的情况。拿它来回答或建立假设之前，先核对当前文件或资源。和眼前看到的不一致时，以眼前为准，并更新或删掉过时的那条。

## 根据记忆提建议之前

记忆里点名的函数、文件或参数，只说明它在写下时存在，之后可能改名、删除，或根本没合并进来。

- 记忆里有文件路径：先确认文件还在。
- 记忆里有函数或参数：先搜索。
- 用户正要按你的建议去做，而不只是在问历史：先核对。

“记忆里说有 X”不等于“现在还有 X”。

记忆里对仓库状态的概括是冻结的。用户问的是最近或当前的状态时，用 git log 或读代码，不要拿那份旧概括来回答。

## 记忆和其他需要留下来的东西

记忆能在以后的会话里想起，所以不要拿它存只对这一轮有用的信息。

- 还没和用户对齐的做法，写到当前会话自己的计划文件，不要写进记忆。计划已经在这次对话里、做法又变了，就改这份计划，不要改其他会话的计划。
- 这一轮要拆开的步骤和进度，用 task_write，不要写进记忆。

## 查找以前的上下文

先在记忆目录里搜主题文件：grep 的 path 用 `{root}`，glob 用 `*.md`。搜索词用错误信息、文件路径或函数名，不要用很宽的词。索引里只有一行，相关的正文用 read_memory 或 read_file 再读。

## MEMORY.md

{index}
"""


def import_legacy(db, workspace: Path) -> None:
    """把升级前存在数据库里的全局记忆，导入这个工作区一次。"""
    folder = memory_dir(workspace)
    marker = folder / ".imported"
    if marker.exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    if any(path.name != ENTRYPOINT for path in folder.glob("*.md")):
        marker.write_text("skip\n", encoding="utf-8")
        return
    from sqlalchemy import select

    from ..models import Memory

    from ..access import current_user_id

    statement = select(Memory).order_by(Memory.id.asc())
    actor = current_user_id()
    if actor:
        statement = statement.where(Memory.user_id == actor)
    rows = db.scalars(statement).all()
    for row in rows:
        title = (row.title or row.content or "记忆")[:40]
        try:
            save_memory(workspace, title, row.content or title, "user", title)
        except ValueError:
            continue
    marker.write_text("ok\n", encoding="utf-8")


def session_template() -> str:
    blocks = [f"# {title}\n{italic}" for title, italic in SESSION_SECTIONS]
    return "\n\n".join(blocks) + "\n"


def _section_bodies(text: str) -> dict[str, str]:
    titles = {title for title, _italic in SESSION_SECTIONS}
    found: dict[str, list[str]] = {}
    current = ""
    for line in (text or "").splitlines():
        match = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        title = match.group(1).strip() if match else ""
        if title in titles:
            current = title
            found.setdefault(current, [])
            continue
        if current:
            found[current].append(line)
    bodies = {}
    for title, lines in found.items():
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and lines[0].strip().startswith("_") and lines[0].strip().endswith("_"):
            lines.pop(0)
        bodies[title] = "\n".join(lines).strip()[:SECTION_CHAR_CAP]
    return bodies


def rebuild_notes(model_text: str, previous: str) -> str:
    fresh = _section_bodies(_strip_fence(model_text))
    old = _section_bodies(previous)
    blocks = []
    for title, italic in SESSION_SECTIONS:
        body = fresh.get(title)
        if not body:
            body = old.get(title, "")
        blocks.append(f"# {title}\n{italic}" + (f"\n{body}" if body else ""))
    return "\n\n".join(blocks) + "\n"


def _strip_fence(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
    return re.sub(r"\n```$", "", stripped).strip()


def notes_have_content(text: str) -> bool:
    return any(body.strip() for body in _section_bodies(text).values())


def session_notes_for_prompt(workspace: Path, conversation_id: int | None) -> str:
    if not conversation_id:
        return ""
    path = session_memory_path(workspace, conversation_id)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not notes_have_content(text):
        return ""
    return f"笔记文件：{path}\n\n{text.strip()[:12_000]}"


def message_stats(messages: list[dict]) -> tuple[int, int, bool, bool]:
    chars = 0
    tools = 0
    has_assistant = False
    last_calls: list | None = None
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    chars += len(str(part.get("text") or ""))
        if message.get("role") == "assistant":
            calls = message.get("tool_calls") or []
            has_assistant = True
            last_calls = calls
            tools += len(calls)
    return chars, tools, bool(last_calls), has_assistant


def should_extract(
    chars: int,
    tool_count: int,
    last_has_tools: bool,
    state: dict,
    has_assistant: bool = True,
) -> bool:
    """长度够了，并且这一轮已经有助手回复。没有助手时不算“这一轮说完了”。

    上一次如果没写出栏目正文，同一次快照不再调用模型。工具还在继续时，要再等 3 次工具调用。
    助手已经结束、没有工具调用时，可以再写一次，不必再攒 8000 字。
    """
    if not has_assistant:
        return False
    initialized = bool(state.get("initialized"))
    if chars < SESSION_INIT_CHARS and not initialized:
        return False
    if state.get("empty_chars") == chars and state.get("empty_tools") == tool_count:
        return False
    growth = chars - int(state.get("chars") or 0)
    tool_growth = tool_count - int(state.get("tools") or 0)
    if not initialized:
        if "empty_chars" in state and last_has_tools:
            since_tools = tool_count - int(state.get("empty_tools") or 0)
            if since_tools < SESSION_TOOLS_BETWEEN:
                return False
        return bool(tool_growth >= SESSION_TOOLS_BETWEEN or not last_has_tools)
    met_growth = growth >= SESSION_UPDATE_CHARS
    met_tools = tool_growth >= SESSION_TOOLS_BETWEEN
    return bool(met_growth and (met_tools or not last_has_tools))


def _load_state(workspace: Path, conversation_id: int) -> dict:
    path = _state_path(workspace, conversation_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(workspace: Path, conversation_id: int, chars: int, tools: int) -> None:
    path = _state_path(workspace, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"initialized": True, "chars": chars, "tools": tools}, ensure_ascii=False),
        encoding="utf-8",
    )


def _save_empty_attempt(workspace: Path, conversation_id: int, chars: int, tools: int) -> None:
    """模型没写出栏目正文。记下这次快照，但不把笔记标成已更新。"""
    path = _state_path(workspace, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state(workspace, conversation_id)
    state["empty_chars"] = chars
    state["empty_tools"] = tools
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _transcript(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        role = message.get("role") or ""
        if role == "system":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        text = (content or "").strip()
        if text:
            lines.append(f"{role}: {text[:2000]}")
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = function.get("name") or call.get("name") or ""
            if name:
                lines.append(f"工具: {name}")
    return "\n".join(lines)[-12_000:]


async def maintain_session_memory(workspace, conversation_id, messages, provider, model: str, previous_summary: str = "") -> None:
    """这一轮助手回复已经放进 messages 之后再记笔记。没有助手回复、或没到阈值，就不调用模型。"""
    if not conversation_id or provider is None:
        return
    chars, tools, last_has_tools, has_assistant = message_stats(messages)
    state = _load_state(workspace, conversation_id)
    if not should_extract(chars, tools, last_has_tools, state, has_assistant):
        return
    path = session_memory_path(workspace, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else session_template()
    from .providers import collect_text

    prompt = (
        "下面这段说明不是用户的对话。不要在笔记里提到记笔记或这些说明。\n"
        "根据用户对话更新会话笔记。系统提示、项目说明和这份说明本身不要写进去。\n"
        "必须保留全部栏目标题，以及标题下面那行以下划线开头和结尾的斜体说明。只改斜体说明下面的内容。\n"
        "某一栏没有新的实质内容就留空，不要写“暂无”。每栏写具体的路径、函数、错误和命令。\n"
        "始终更新“当前状态”。不要新增栏目。\n\n"
        f"笔记文件：{path}\n"
        f"<current_notes>\n{current}\n</current_notes>\n\n"
    )
    if previous_summary:
        prompt += f"上一份压缩摘要里仍然成立的事实也要收进对应栏目：\n{previous_summary[:4000]}\n\n"
    prompt += "对话摘录：\n" + _transcript(messages)
    try:
        raw = await collect_text(provider, model, prompt, max_tokens=2000)
    except Exception:
        logger.exception("更新会话笔记失败")
        return
    updated = rebuild_notes(raw, current)
    if not notes_have_content(updated):
        _save_empty_attempt(workspace, conversation_id, chars, tools)
        return
    path.write_text(updated, encoding="utf-8")
    _save_state(workspace, conversation_id, chars, tools)


COMPACT_SYSTEM = """你的任务是为到目前为止的对话写一份详细摘要，供同一会话继续开发。只输出文字，不要调用任何工具。

先在 <analysis> 里按时间梳理，再在 <summary> 里给出正式摘要。analysis 不会进入后续上下文。

摘要必须使用下面九个栏目，用中文：

1. 用户要求：写清用户明确提出的目标和限制
2. 关键技术点：列出涉及的技术、约定和模式
3. 文件和代码：列出读过、改过或新建的文件。说明为什么重要，并附上关键代码片段
4. 错误和修复：遇到的错误，以及如何修好。用户如果纠正过做法，要写下来
5. 已解决的问题：已经做完的排查和仍在进行的排查
6. 用户原话：列出所有不是工具结果的用户消息
7. 未完成的任务：用户明确要求、尚未做完的事
8. 当前正在做的事：必须根据材料最后几条消息来写，包括文件名和代码。不要沿用上一份摘要里已经过时的说法
9. 下一步：只写和最近工作、用户最近要求直接相关的一步。如果最后几条消息显示文件已经写完或测试已经通过，就写这个结果，不要再写「尚未修改」或「先把文件重读一遍」

输出结构：
<analysis>
梳理过程
</analysis>
<summary>
1. 用户要求：
2. 关键技术点：
3. 文件和代码：
4. 错误和修复：
5. 已解决的问题：
6. 用户原话：
7. 未完成的任务：
8. 当前正在做的事：
9. 下一步：
</summary>
"""

_SUMMARY_BLOCK = re.compile(r"<summary>(.*?)</summary>", re.IGNORECASE | re.DOTALL)
_ANALYSIS_BLOCK = re.compile(r"<analysis>.*?</analysis>", re.IGNORECASE | re.DOTALL)
COMPACT_MAX_TOKENS = 8_000


def format_compact_summary(raw: str) -> str:
    """正式摘要只留 summary。没有标签时保留原文，方便测试和模型直接给正文。"""
    text = raw or ""
    found = _SUMMARY_BLOCK.search(text)
    if found:
        text = found.group(1)
    else:
        text = _ANALYSIS_BLOCK.sub("", text)
    text = text.strip()
    return text or "（没有生成摘要）"


def clip_transcript(rows: list[tuple[str, str]]) -> str:
    """靠后的消息多留一些，当前工作和下一步才不会被截掉。"""
    lines = []
    total = len(rows)
    for index, (role, content) in enumerate(rows):
        limit = 8_000 if index >= total - 6 else 2_000
        lines.append(f"{role}: {(content or '')[:limit]}")
    return "\n".join(lines)


def transcript_text(role: str, content: str | None, tool_calls: list | None) -> str:
    parts = [content or ""]
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name") or ""
        if name:
            parts.append(f"工具 {name}")
    return "\n".join(part for part in parts if part)


async def summarize_history(provider, model: str, material: str, fallback_model: str = "") -> str:
    from .providers import ModelFallback

    parts: list[str] = []

    async def pull(chosen: str, fallback: str) -> None:
        async for event in provider.stream(
            [
                {"role": "system", "content": COMPACT_SYSTEM},
                {"role": "user", "content": material},
            ],
            None,
            chosen,
            max_tokens=COMPACT_MAX_TOKENS,
            query_source="compact",
            fallback_model=fallback,
        ):
            if event.kind == "text":
                parts.append(event.text)

    try:
        await pull(model, fallback_model)
    except ModelFallback as exc:
        parts.clear()
        await pull(exc.fallback, "")
    return format_compact_summary("".join(parts))


def compact_source(workspace: Path, conversation_id: int | None, previous_summary: str, transcript: str) -> str:
    parts = []
    if previous_summary.strip():
        parts.append(
            "上一份摘要（仍然成立的事实要保留，已经过时的删掉。"
            "如果将要移出的对话末尾已经写了文件或测试通过，当前状态和下一步以这些最新消息为准）：\n"
            + previous_summary.strip()[:8000]
        )
    if conversation_id:
        path = session_memory_path(workspace, conversation_id)
        if path.is_file():
            try:
                notes = path.read_text(encoding="utf-8")
            except OSError:
                notes = ""
            if notes_have_content(notes):
                parts.append(
                    "当前会话笔记（当前状态、关键结果和未完成的事以此为准）：\n"
                    + notes.strip()[:8000]
                    + f"\n完整笔记在 {path}"
                )
    parts.append("将要移出上下文的对话：\n" + transcript)
    return "\n\n".join(parts)
