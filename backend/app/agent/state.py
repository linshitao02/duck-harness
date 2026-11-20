"""进程内的短生命周期状态：文件读取戳、后台命令、计划模式回退。"""

READS: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
PLAN_RETURN: dict[int, str] = {}


def tag_job(job: dict, ctx) -> None:
    job["conversation_id"] = getattr(ctx, "conversation_id", None)
    job["turn_token"] = getattr(ctx, "turn_token", "") or ""
    # 子代理有自己的 agent_run_id。后台任务只通知派出它的那一层，避免子代理把自己正在跑的任务当成还没结束。
    job["owner_run"] = getattr(ctx, "agent_run_id", "") or ""
    job["notified"] = False


def same_conversation(job: dict, ctx) -> bool:
    current = getattr(ctx, "conversation_id", None)
    if current is None:
        return True
    return job.get("conversation_id") == current


def _owns(job: dict, ctx) -> bool:
    return (
        job.get("conversation_id") == getattr(ctx, "conversation_id", None)
        and job.get("turn_token", "") == (getattr(ctx, "turn_token", "") or "")
        and job.get("owner_run", "") == (getattr(ctx, "agent_run_id", "") or "")
    )


def jobs_pending(ctx) -> bool:
    return any(_owns(job, ctx) and not job.get("done") for job in JOBS.values())


def take_job_notices(ctx) -> str:
    lines = []
    for job_id, job in list(JOBS.items()):
        if not _owns(job, ctx) or not job.get("done") or job.get("notified"):
            continue
        job["notified"] = True
        kind = "子代理" if job.get("kind") == "agent" else "命令"
        body = str(job.get("text") or "（无输出）")
        if len(body) > 4000:
            body = body[:4000] + "\n…"
        lines.append(f"#{job_id} {kind} 已结束，退出码 {job.get('code')}\n{body}")
    return "\n\n".join(lines)
