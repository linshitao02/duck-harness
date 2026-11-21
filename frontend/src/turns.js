import { toolPhrase } from "./activity";

function parseTime(value) {
  if (!value) return null;
  if (typeof value === "number") return value;
  const time = Date.parse(String(value).replace(" ", "T"));
  return Number.isNaN(time) ? null : time;
}

export function formatDuration(ms) {
  const total = Math.max(1, Math.round(Number(ms) / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes <= 0) return `${seconds} 秒`;
  return `${minutes} 分 ${seconds} 秒`;
}

export function workDuration(turn, now = Date.now()) {
  const start = turn.startedAt || parseTime(turn.createdAt);
  const end = turn.streaming ? now : parseTime(turn.endedAt) || now;
  if (!start || !end || end <= start) return "片刻";
  return formatDuration(end - start);
}

export function toolLine(tool) {
  return toolPhrase(tool).replace(/^正在/, "已");
}

export function exploreSummary(tools) {
  const files = new Set();
  let searches = 0;
  for (const tool of tools || []) {
    const name = tool?.name || "";
    const args = tool?.arguments || {};
    if (name === "read_file" || name === "glob_files" || name === "list_dir") {
      files.add(String(args.path || args.pattern || name));
    }
    if (name === "grep" || name === "glob_files" || name === "web_search" || name === "web_fetch") searches += 1;
  }
  const parts = [];
  if (files.size) parts.push(`查看了 ${files.size} 个文件`);
  if (searches) parts.push(`搜索了 ${searches} 次`);
  return parts.join("，");
}

function liveSegments(message) {
  const segments = [...(message.segments || [])];
  if (message.segment) segments.push(message.segment);
  if (segments.length) return segments.map((item) => ({ ...item, status: message.status, createdAt: message.created_at }));
  return [
    {
      content: message.content || "",
      reasoning: message.reasoning || "",
      tools: message.tools || [],
      attachments: message.attachments || [],
      approval: message.approval,
        question: message.question,
        card: message.card || null,
        status: message.status,
        createdAt: message.created_at,
        id: message.id,
    },
  ];
}

function intakeCard(bucket) {
  const host = [...bucket.messages].reverse().find((item) => item.card && item.card.question);
  if (!host) return null;
  return { ...host.card, message_id: host.id };
}

function presentTurn(bucket) {
  const segments = bucket.messages.flatMap((message) => {
    if (message.status === "streaming" || message.segment || (message.segments || []).length) return liveSegments(message);
    return [
      {
        content: message.content || "",
        reasoning: message.reasoning || "",
        tools: message.tools || [],
        attachments: message.attachments || [],
        approval: message.approval,
        question: message.question,
        card: message.card || null,
        status: message.status,
        createdAt: message.created_at,
        id: message.id,
      },
    ];
  });
  const streaming = segments.some((item) => item.status === "streaming");
  let process = [];
  let answer = [];
  if (streaming) {
    const last = segments[segments.length - 1];
    if (segments.length > 1 && last && !(last.tools || []).length) {
      process = segments.slice(0, -1);
      answer = [last];
    } else {
      process = segments;
    }
  } else if (segments.length > 1 && !(segments[segments.length - 1].tools || []).length) {
    process = segments.slice(0, -1);
    answer = [segments[segments.length - 1]];
  } else if ((segments[segments.length - 1]?.tools || []).length) {
    process = segments;
  } else {
    answer = segments;
  }
  const tools = process.flatMap((item) => item.tools || []);
  const notes = process.map((item) => String(item.content || "").trim()).filter(Boolean);
  const attachments = [];
  const seen = new Set();
  for (const item of segments) {
    for (const file of item.attachments || []) {
      if (!file || seen.has(file.id)) continue;
      seen.add(file.id);
      attachments.push(file);
    }
  }
  const created = segments.map((item) => item.createdAt).filter(Boolean);
    return {
    kind: "turn",
    id: bucket.messages[0]?.id || "turn",
    speaker: bucket.speaker || bucket.messages.find((item) => item.speaker)?.speaker || "",
    streaming,
    question: bucket.question || "",
    notes,
    bodyNotes: streaming && !answer.length ? notes.slice(0, -1) : notes,
    tools,
    diffs: segments.flatMap((item) => (item.tools || []).filter((tool) => tool.diff)),
    attachments,
    approval: [...segments].reverse().find((item) => item.approval)?.approval || null,
    questionCard: [...segments].reverse().find((item) => item.question)?.question || null,
    intake: intakeCard(bucket),
    answer: answer.map((item) => String(item.content || "").trim()).filter(Boolean).join("\n\n"),
    reasoning: answer.map((item) => item.reasoning).filter(Boolean).join("\n"),
    explore: exploreSummary(tools),
    startedAt: bucket.startedAt || null,
    createdAt: created[0] || "",
    endedAt: created[created.length - 1] || "",
  };
}

export function groupThread(messages) {
  const items = [];
  let bucket = null;
  const flush = () => {
    if (!bucket?.messages.length) {
      bucket = null;
      return;
    }
    items.push(presentTurn(bucket));
    bucket = null;
  };
  for (const message of messages || []) {
    if (message.role === "divider") {
      flush();
      items.push({ kind: "divider", id: message.id, message });
      continue;
    }
    if (message.role === "user") {
      flush();
      items.push({ kind: "user", id: message.id, message });
      bucket = { messages: [], question: message.content || "", startedAt: message.startedAt || null };
      continue;
    }
    if (message.role === "assistant" && message.card) {
      flush();
      items.push(presentTurn({ messages: [message], question: "", startedAt: parseTime(message.created_at), speaker: message.speaker || "" }));
      bucket = null;
      continue;
    }
    if (message.role !== "assistant") continue;
    const speaker = message.speaker || "";
    if (bucket?.messages.length && speaker && bucket.speaker && speaker !== bucket.speaker) {
      flush();
    }
    if (!bucket) bucket = { messages: [], question: "", startedAt: message.startedAt || null, speaker };
    if (!bucket.startedAt && message.startedAt) bucket.startedAt = message.startedAt;
    if (!bucket.speaker && speaker) bucket.speaker = speaker;
    bucket.messages.push(message);
  }
  flush();
  return items;
}
