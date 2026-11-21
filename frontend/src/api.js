import katex from "katex";

async function parseError(response) {
  const text = await response.text();
  try {
    const body = JSON.parse(text);
    if (typeof body.detail === "string") return body.detail;
    return text || response.statusText;
  } catch {
    return text || response.statusText;
  }
}

export async function api(url, options = {}) {
  const headers = { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) };
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) throw new Error(await parseError(response));
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response;
}

export function streamChat(conversationId, body, onEvent, signal) {
  return fetch(`/api/conversations/${conversationId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }).then(async (response) => {
    if (!response.ok || !response.body) throw new Error(await parseError(response));
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((item) => item.startsWith("data:"));
        if (!line) continue;
        onEvent(JSON.parse(line.slice(5).trim()));
      }
    }
  });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function looksLikeMath(body) {
  const text = body.trim();
  if (!text || text.length > 1000) return false;
  if (/[\\^_={}<>]/.test(text)) return true;
  if (/\s/.test(text) || /[A-Za-z]{5,}/.test(text)) return false;
  return /^[A-Za-z0-9+\-*/(),.<>]+$/.test(text);
}

function typeset(source, display) {
  const body = String(source || "").trim();
  if (!body) return "";
  try {
    const html = katex.renderToString(body, {
      displayMode: display,
      throwOnError: false,
      strict: "ignore",
      trust: false,
      output: "html",
    });
    if (html.includes("katex-error")) return "";
    return html;
  } catch {
    return "";
  }
}

export function renderText(value) {
  const slots = [];
  const hold = (html) => {
    const token = `\u0000${slots.length}\u0000`;
    slots.push(html);
    return token;
  };
  let text = String(value || "");
  text = text.replace(/```([\s\S]*?)```/g, (_, code) => hold(`<pre><code>${escapeHtml(code.trim())}</code></pre>`));
  text = text.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code>${escapeHtml(code)}</code>`));
  const blocks = [
    [/\$\$([\s\S]+?)\$\$/g, true],
    [/\\\[([\s\S]+?)\\\]/g, true],
    [/\\\(([\s\S]+?)\\\)/g, false],
  ];
  for (const [pattern, display] of blocks) {
    text = text.replace(pattern, (full, body) => {
      const html = typeset(body, display);
      return html ? hold(html) : full;
    });
  }
  text = text.replace(/(?<!\$)\$(?!\$)([^$\n]+)\$(?!\$)/g, (full, body) => {
    if (!looksLikeMath(body)) return full;
    const html = typeset(body, false);
    return html ? hold(html) : full;
  });
  text = escapeHtml(text)
    .replace(/\n{3,}/g, "\n\n")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => slots[Number(index)]);
}

export function formatSize(size) {
  const value = Number(size) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
