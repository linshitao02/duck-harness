const { request, streamChat } = require("../../utils/api");

function shown(items) {
  return (items || [])
    .filter((item) => item.role === "user" || item.role === "assistant")
    .filter((item) => String(item.content || "").trim() || item.speaker)
    .map((item, index) => ({
      key: String(item.id || index),
      role: item.role,
      speaker: item.speaker || "",
      content: item.content || "",
      pending: false,
    }));
}

Page({
  data: {
    id: 0,
    title: "会话",
    items: [],
    draft: "",
    sending: false,
    anchor: "bottom",
  },
  onLoad(query) {
    const title = decodeURIComponent(query.title || "会话");
    this.setData({ id: Number(query.id), title });
    wx.setNavigationBarTitle({ title });
    this.load();
  },
  stick() {
    this.setData({ anchor: "" });
    wx.nextTick(() => this.setData({ anchor: "bottom" }));
  },
  async load() {
    try {
      const data = await request("/api/conversations/" + this.data.id + "/messages");
      this.setData({ items: shown(data.items) });
      this.stick();
    } catch (error) {
      wx.showToast({ title: error.message || "加载失败", icon: "none" });
    }
  },
  onDraft(event) {
    this.setData({ draft: event.detail.value });
  },
  async send() {
    const content = String(this.data.draft || "").trim();
    if (!content || this.data.sending) return;
    const items = this.data.items.concat([
      { key: "user-" + Date.now(), role: "user", speaker: "", content, pending: false },
      { key: "assistant-" + Date.now(), role: "assistant", speaker: "", content: "", pending: true },
    ]);
    this.setData({ items, draft: "", sending: true });
    this.stick();
    const touch = (mutate) => {
      const next = this.data.items.slice();
      const index = next.length - 1;
      const last = Object.assign({}, next[index]);
      mutate(last, next);
      next[index] = last;
      this.setData({ items: next });
      this.stick();
    };
    try {
      await streamChat(this.data.id, content, (event) => {
        if (event.type === "speaker") {
          touch((last, next) => {
            if (last.speaker || last.content) {
              last.pending = false;
              next.push({
                key: "assistant-" + Date.now() + "-" + (event.name || ""),
                role: "assistant",
                speaker: event.name || "",
                content: "",
                pending: true,
              });
            } else {
              last.speaker = event.name || "";
            }
          });
          return;
        }
        if (event.type === "token") {
          touch((last) => {
            last.content += event.text || "";
            if (event.speaker) {
              last.speaker = last.speaker || event.speaker;
              last.pending = false;
            }
          });
          return;
        }
        if (event.type === "error") {
          touch((last) => {
            last.content = last.content || event.message || "发送失败";
            last.pending = false;
          });
        }
      });
      await this.load();
    } catch (error) {
      touch((last) => {
        last.content = last.content || error.message || "发送失败";
        last.pending = false;
      });
      wx.showToast({ title: error.message || "发送失败", icon: "none" });
    } finally {
      this.setData({ sending: false });
    }
  },
});
