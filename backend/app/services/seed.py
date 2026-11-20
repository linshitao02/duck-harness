from datetime import datetime, timedelta
from pathlib import Path

from docx import Document
from sqlalchemy import func, select

from ..config import UPLOAD_DIR
from ..models import Attachment, Conversation, Message, User


DEMOS = [
    {
        "title": "Agent开发方案",
        "preview": "Sent 1 document",
        "icon_key": "spark",
        "icon_color": "#ff4b8d",
        "thread": "proposal",
    },
    {
        "title": "看代码",
        "preview": "是的。你当前检出的这个目录...",
        "icon_key": "code",
        "icon_color": "#ff7a1a",
        "user": "帮我看一下当前目录里有什么。",
        "assistant": "是的。你当前检出的这个目录里，后端、前端和桌面启动器是分开的。我可以按文件把入口和数据流标出来。",
    },
    {
        "title": "趋势预测",
        "preview": "在国内企业里，目前还是 MyS...",
        "icon_key": "trend",
        "icon_color": "#ff4fa3",
        "user": "企业里现在更常用哪种数据库？",
        "assistant": "在国内企业里，目前还是 MySQL 这一类关系库最常见。真要上预测，先把指标口径和历史窗口定下来。",
    },
    {
        "title": "嵌入式",
        "preview": "数字输入只认“开或关”，模拟输...",
        "icon_key": "chip",
        "icon_color": "#3b82f6",
        "user": "数字输入和模拟输入有什么差别？",
        "assistant": "数字输入只认“开或关”，模拟输入则是一段连续电压。采样前要先确认量程和参考电压。",
    },
    {
        "title": "网站爬取",
        "preview": "有，最接近你现在这种按章节...",
        "icon_key": "spider",
        "icon_color": "#8b5cf6",
        "user": "有没有按章节抓取的办法？",
        "assistant": "有，最接近你现在这种按章节往下翻的方式：先定位目录，再逐章抓正文，失败的章节单独重试。",
    },
    {
        "title": "Grok Bot",
        "preview": "滴答清单，适合安排每日任务...",
        "icon_key": "bot",
        "icon_color": "#111111",
        "user": "帮我把每天要做的事排一下。",
        "assistant": "滴答清单，适合安排每日任务。你要是想留在这个对话里，我也可以直接把任务写进当前会话的待办。",
    },
    {
        "title": "工作咨询",
        "preview": "确实有一部分 AI 开发工程师...",
        "icon_key": "work",
        "icon_color": "#60a5fa",
        "user": "现在招 AI 开发工程师，实际在做什么？",
        "assistant": "确实有一部分 AI 开发工程师的日常不是训练模型，而是把模型、工具和现有业务系统接成能交付的流程。",
    },
    {
        "title": "C++学习",
        "preview": "你现在学 C++ 到哪一步了？",
        "icon_key": "cpp",
        "icon_color": "#9ca3af",
        "user": "我想继续学 C++。",
        "assistant": "你现在学 C++ 到哪一步了？指针、类和标准库这三块，我们可以按你卡壳的位置往下拆。",
    },
    {
        "title": "solidworks导入",
        "preview": "对：gb_part 是零件（部件），...",
        "icon_key": "cad",
        "icon_color": "#d97706",
        "user": "gb_part 在装配里是什么意思？",
        "assistant": "对：gb_part 是零件（部件），gb_asm 才是装配。导入时先确认单位和坐标系，再检查是否有缺失的配合。",
    },
    {
        "title": "视频制作",
        "preview": "炫酷版 v2 已经放进桌面 拉着拉...",
        "icon_key": "video",
        "icon_color": "#a1a1aa",
        "user": "视频第二版放到哪了？",
        "assistant": "炫酷版 v2 已经放进桌面。拉着时间轴看字幕和转场就行，需要的话我可以再导出一版更短的。",
    },
    {
        "title": "行业AI方案设计",
        "preview": "对，妙搭跑在飞书云上，默认...",
        "icon_key": "industry",
        "icon_color": "#fb7185",
        "user": "这个方案是跑在客户自己的机器上吗？",
        "assistant": "对，妙搭跑在飞书云上，默认不把业务数据落到你这台电脑。要改成本地部署的话，得把账号、知识和接口边界重新划一遍。",
    },
]


def seed_if_empty(db) -> None:
    if db.get(User, 1) is None:
        db.add(User(id=1, name="用户"))
    count = db.scalar(select(func.count()).select_from(Conversation)) or 0
    if count:
        db.commit()
        return
    now = datetime.now()
    for index, spec in enumerate(DEMOS):
        conversation = Conversation(
            title=spec["title"],
            preview=spec["preview"],
            icon_key=spec["icon_key"],
            icon_color=spec["icon_color"],
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace="",
            updated_at=now - timedelta(minutes=index),
            created_at=now - timedelta(days=1, minutes=index),
        )
        db.add(conversation)
        db.flush()
        if spec.get("thread") == "proposal":
            _seed_proposal(db, conversation)
        else:
            db.add(Message(conversation_id=conversation.id, role="user", content=spec["user"], status="done"))
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=spec["assistant"],
                    status="done",
                )
            )
    db.commit()


def _seed_proposal(db, conversation: Conversation) -> None:
    user = (
        "请你根据这个AI智能体开发的岗位的要求，帮我结合市面上的主流的能落地的"
        "满足这个岗位的要求的，请你给我写一个这种的方案，要是word，我要的是主流"
        "的能落地的，要把这上面的岗位要求都要满足。"
    )
    db.add(Message(conversation_id=conversation.id, role="user", content=user, status="done"))
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=(
                "我先按这份「AI智能体开发工程师 / Agent工程师」岗位的全部职责和任职要求，"
                "用目前能真正落地的主流技术栈写一份完整方案，做成 Word 给你。"
            ),
            status="done",
        )
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content="正在按岗位全部职责写这套可落地方案，做成 Word 后发给你，稍等一会儿。",
            status="done",
        )
    )
    db.add(Message(conversation_id=conversation.id, role="divider", content="新", status="done", in_context=False))
    filename = "AI多Agent智能研发体系落地方案.docx"
    path = _write_sample_docx(conversation.id, filename)
    done = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=f"方案已经写好，Word 在下面：《AI 多 Agent 智能研发体系落地方案》，大约 {path.stat().st_size // 1024 or 1} KB。",
        status="done",
    )
    db.add(done)
    db.flush()
    db.add(
        Attachment(
            conversation_id=conversation.id,
            message_id=done.id,
            filename=filename,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size=path.stat().st_size,
            path=str(path),
        )
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=(
                "按这份岗位的 8 项职责和任职要求，用目前能落地的主流技术写了整套体系："
                "用可编排的状态图做流程，用 MCP 接工具，在隔离环境里执行编码，"
                "再用浏览器自动化、质量门禁和 CI/CD 把「需求沟通到数据复盘」串起来。"
                "文档里有各 Agent 的职责与输入输出、一份仓储补货阈值调整的贯穿示例，"
                "以及大约 12 周的分期实施路径。生产发布按风险分级："
                "低风险可自动上线并带自动回滚，数据迁移、权限、支付等仍保留人工审批，避免“能跑即上线”。"
            ),
            status="done",
        )
    )


def _write_sample_docx(conversation_id: int, filename: str) -> Path:
    folder = UPLOAD_DIR / str(conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    document = Document()
    document.add_heading("AI 多 Agent 智能研发体系落地方案", level=1)
    document.add_paragraph("这是一份示例文档，用来在界面上展示会话附件。正式方案可以由助手重新生成。")
    for title in ("岗位职责对照", "技术选型", "十二周路径", "发布与回滚"):
        document.add_heading(title, level=2)
        document.add_paragraph("这里保留结构，具体内容按当次岗位要求生成。")
    document.save(target)
    return target
