function clip(value, limit = 18) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? text.slice(0, limit) : text;
}

function fileName(path) {
  const text = String(path || "").replace(/\\/g, "/");
  return text.split("/").filter(Boolean).pop() || text;
}

export function toolPhrase(tool) {
  const args = tool?.arguments || {};
  const name = tool?.name || "";
  if (name === "web_search") return `正在搜索${clip(args.query)}`;
  if (name === "web_fetch") return "正在打开网页";
  if (name === "read_file") return `正在查看${clip(fileName(args.path))}`;
  if (name === "grep" || name === "glob_files") return `正在查找${clip(args.pattern)}`;
  if (name === "list_dir") return "正在查看目录";
  if (name === "bash") return args.description ? `正在${clip(args.description)}` : "正在执行命令";
  if (name === "edit_file" || name === "write_file") return `正在修改${clip(fileName(args.path))}`;
  if (name === "remember") return "正在记下";
  if (name === "spawn_agent") return "正在处理子任务";
  if (name.startsWith("mcp__")) return "正在调用工具";
  return name ? `正在处理${name}` : "正在处理";
}

export function taskPhrase(text) {
  const raw = String(text || "");
  const compact = raw.replace(/\s+/g, "");
  if (/word|docx/i.test(raw) || (/文档/.test(compact) && /生成|写|做/.test(compact))) return "正在生成 Word 文档";
  return "";
}

export function workPhrase(text) {
  const task = taskPhrase(text);
  if (task) return task;
  const compact = String(text || "").replace(/\s+/g, "");
  if (compact.replace(/[，。！？、,.!?:：；;]/g, "").length < 8) return "";
  let body = compact.replace(/[，。！？、,.!?:：；;“”"「」]/g, "");
  body = body.replace(/^(请你|请帮我|帮我|麻烦你|麻烦|请|你能不能|能不能)/, "");
  body = body.replace(/^(结合你前面聊的|结合前面|根据前面|关于)/, "");
  body = body.replace(/^(分析一下|分析|梳理一下|梳理|总结一下|总结|介绍一下|介绍|说明一下|说明|看一下|看看|写一下|写|查一下|查|搜一下|搜索)/, "");
  body = body.replace(/(重点要注意|要注意|需要注意).*$/, "");
  body = body.slice(0, 16);
  if (!body) return "";
  if (/分析|审查|评估|梳理|对比|总结/.test(compact)) return `正在分析${body}`;
  if (/写|起草|生成|整理/.test(compact)) return `正在整理${body}`;
  if (/查|搜|找/.test(compact)) return `正在查找${body}`;
  return `正在处理${body}`;
}

export function activityOf(message, question) {
  if (!message || message.role !== "assistant" || message.status !== "streaming") return null;
  const task = taskPhrase(question);
  if (task) return { kind: "pause", text: task };
  if (String(message.content || "").trim()) return null;
  const running = (message.tools || []).find((tool) => tool.running);
  if (running) return { kind: "pause", text: toolPhrase(running) };
  if (message.reasoning) return { kind: "pause", text: "正在思考" };
  if (message.phase === "working") {
    const phrase = workPhrase(question);
    if (phrase) return { kind: "pause", text: phrase };
  }
  return { kind: "typing", text: "正在输入..." };
}
