function base() {
  const app = getApp();
  return String((app && app.globalData && app.globalData.base) || wx.getStorageSync("duck.base") || "http://127.0.0.1:8787").replace(/\/$/, "");
}

function authHeader() {
  const token = wx.getStorageSync("duck.token");
  return token ? { Authorization: "Bearer " + token } : {};
}

function detailOf(data, fallback) {
  if (data && typeof data.detail === "string") return data.detail;
  return fallback;
}

function request(path, options) {
  const opts = options || {};
  return new Promise((resolve, reject) => {
    wx.request({
      url: base() + path,
      method: opts.method || "GET",
      data: opts.data,
      header: Object.assign({ "Content-Type": "application/json" }, authHeader(), opts.header || {}),
      success(res) {
        if (res.statusCode === 401) {
          wx.removeStorageSync("duck.token");
          reject(new Error(detailOf(res.data, "请先登录")));
          return;
        }
        if (res.statusCode >= 400) {
          reject(new Error(detailOf(res.data, "请求失败")));
          return;
        }
        resolve(res.data);
      },
      fail(err) {
        reject(new Error((err && err.errMsg) || "连不上服务器"));
      },
    });
  });
}

function decodeChunk(data) {
  if (typeof TextDecoder !== "undefined") {
    return new TextDecoder("utf-8").decode(data);
  }
  const bytes = new Uint8Array(data);
  let text = "";
  for (let i = 0; i < bytes.length; i += 1) text += String.fromCharCode(bytes[i]);
  try {
    return decodeURIComponent(escape(text));
  } catch (error) {
    return text;
  }
}

function takeEvents(buffer) {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() || "";
  const events = [];
  parts.forEach((part) => {
    const line = part.split("\n").find((item) => item.indexOf("data:") === 0);
    if (!line) return;
    try {
      events.push(JSON.parse(line.slice(5).trim()));
    } catch (error) {
      // 半包留到下一次。
    }
  });
  return { rest, events };
}

function streamChat(conversationId, content, onEvent) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    let settled = false;
    const task = wx.request({
      url: base() + "/api/conversations/" + conversationId + "/chat",
      method: "POST",
      enableChunked: true,
      data: { content },
      header: Object.assign({ "Content-Type": "application/json" }, authHeader()),
      success() {
        const parsed = takeEvents(buffer + "\n\n");
        parsed.events.forEach(onEvent);
        settled = true;
        resolve();
      },
      fail(err) {
        if (settled) return;
        settled = true;
        reject(new Error((err && err.errMsg) || "发送失败"));
      },
    });
    if (task && task.onChunkReceived) {
      task.onChunkReceived((res) => {
        buffer += decodeChunk(res.data);
        const parsed = takeEvents(buffer);
        buffer = parsed.rest;
        parsed.events.forEach(onEvent);
      });
    }
  });
}

module.exports = {
  base,
  request,
  streamChat,
};
