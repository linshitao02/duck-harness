import { defineStore } from "pinia";
import { api, streamChat } from "./api";

let streamAbort = null;
let loadToken = 0;
let themeWatching = false;
let activityTimer = 0;
let layoutTimer = 0;

export const FONT_SIZES = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 22];
const FONT_STACKS = {
  grok: '"Microsoft YaHei UI", "PingFang SC", "Microsoft YaHei", "Noto Sans SC", "Segoe UI", sans-serif',
  dengxian: '"DengXian", "等线", "PingFang SC", "Microsoft YaHei", sans-serif',
  yahei: '"Microsoft YaHei", "微软雅黑", "PingFang SC", sans-serif',
  segoe: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  sourcehan: '"Source Han Sans SC", "Noto Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif',
  kaiti: '"KaiTi", "楷体", "STKaiti", serif',
  song: '"SimSun", "宋体", "Songti SC", serif',
};

function watchSystemTheme(store) {
  if (themeWatching) return;
  themeWatching = true;
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => store.applyAppearance());
}

function savedSidebarWidth() {
  const raw = localStorage.getItem("harness.sidebarWidth");
  const value = Number(raw);
  if (!raw || !Number.isFinite(value)) return 300;
  return Math.min(520, Math.max(220, Math.round(value)));
}

function phoneLayout() {
  return Boolean(window.matchMedia?.("(max-width: 800px)")?.matches) && !window.pywebview;
}

function compareConversations(a, b) {
  if (Boolean(a.pinned) !== Boolean(b.pinned)) return a.pinned ? -1 : 1;
  const order = Number(a.sort_order || 0) - Number(b.sort_order || 0);
  if (order) return order;
  const time = String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  if (time) return time;
  return Number(b.id) - Number(a.id);
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    conversations: [],
    activeId: null,
    messages: [],
    privateThreads: [],
    draft: "",
    streaming: false,
    intakeBusy: false,
    userName: "用户",
    username: "",
    settings: {},
    deepseekConfigured: false,
    deepseekKeySource: "",
    settingsOpen: false,
    models: { providers: [] },
    connectOpen: false,
    searchOpen: false,
    search: "",
    botOpen: false,
    toast: "",
    toastTimer: 0,
    pending: [],
    menuOpen: false,
    doctor: null,
    memories: [],
    tasks: [],
    mcp: [],
    skills: [],
    settingsTab: "general",
    settingsStamp: 0,
    mcpStatus: null,
    testing: "",
    testResult: "",
    sidebarOpen: phoneLayout() ? false : localStorage.getItem("harness.sidebarOpen") !== "0",
    sidebarWidth: savedSidebarWidth(),
  }),
  getters: {
    active(state) {
      return state.conversations.find((item) => item.id === state.activeId) || null;
    },
    sidebarItems(state) {
      return state.conversations.filter((item) => !item.hidden);
    },
    membersOf() {
      return (roomId) => {
        const id = Number(roomId);
        if (!id) return [];
        const group = this.conversations.find((item) => item.id === id);
        const ids = Array.isArray(group?.member_ids) ? group.member_ids.map(Number) : [];
        if (ids.length) {
          const byId = new Map(this.conversations.map((item) => [item.id, item]));
          return ids.map((item) => byId.get(item)).filter(Boolean);
        }
        return this.conversations.filter((item) => item.room_id === id);
      };
    },
  },
  actions: {
    rememberLayout() {
      localStorage.setItem("harness.sidebarOpen", this.sidebarOpen ? "1" : "0");
      localStorage.setItem("harness.sidebarWidth", String(this.sidebarWidth));
      const width = this.sidebarWidth;
      const opened = this.sidebarOpen;
      window.clearTimeout(layoutTimer);
      layoutTimer = window.setTimeout(() => {
        window.pywebview?.api?.save_layout?.(width, opened);
      }, 200);
    },
    async loadDesktopLayout() {
      if (!window.pywebview?.api?.layout) return;
      try {
        const data = await window.pywebview.api.layout();
        const width = Number(data?.sidebarWidth);
        if (Number.isFinite(width)) this.sidebarWidth = Math.min(520, Math.max(220, Math.round(width)));
        if (typeof data?.sidebarOpen === "boolean") this.sidebarOpen = data.sidebarOpen;
        localStorage.setItem("harness.sidebarOpen", this.sidebarOpen ? "1" : "0");
        localStorage.setItem("harness.sidebarWidth", String(this.sidebarWidth));
      } catch {
        return;
      }
    },
    toggleSidebar() {
      this.sidebarOpen = !this.sidebarOpen;
      this.rememberLayout();
    },
    setSidebarWidth(width) {
      this.sidebarWidth = Math.min(520, Math.max(220, Math.round(width)));
      this.rememberLayout();
    },
    notify(text) {
      this.toast = text;
      window.clearTimeout(this.toastTimer);
      this.toastTimer = window.setTimeout(() => {
        this.toast = "";
      }, 2400);
    },
    async logout() {
      try {
        await api("/api/auth/logout", { method: "POST", body: "{}" });
      } catch {
        // 会话已经失效时，刷新后仍会回到登录。
      }
      window.location.reload();
    },
    async boot() {
      const [settings, conversations, models] = await Promise.all([
        api("/api/settings"),
        api("/api/conversations"),
        api("/api/models"),
      ]);
      this.settings = settings.values;
      this.userName = settings.user.name;
      this.username = settings.user.username || "";
      this.deepseekConfigured = settings.deepseek_configured;
      this.deepseekKeySource = settings.deepseek_key_source || "";
      this.models = models;
      this.applyAppearance();
      watchSystemTheme(this);
      this.conversations = conversations.items;
      const requested = Number(new URLSearchParams(window.location.search).get("bot") || 0);
      if (requested && this.conversations.some((item) => item.id === requested)) {
        await this.select(requested);
      } else if (!this.activeId) {
        const first = this.sidebarItems[0] || this.conversations[0];
        if (first) await this.select(first.id);
      }
    },
    async refreshConversations() {
      const data = await api("/api/conversations");
      this.conversations = data.items;
    },
    async select(id) {
      this.activeId = id;
      this.menuOpen = false;
      this.searchOpen = false;
      if (phoneLayout()) this.sidebarOpen = false;
      const current = this.conversations.find((item) => item.id === id);
      if (current?.unread) this.patchConversation(id, { unread: false });
      const token = ++loadToken;
      const data = await api(`/api/conversations/${id}/messages`);
      if (token === loadToken) {
        this.messages = data.items;
        this.privateThreads = data.private_threads || [];
      }
    },
    async createConversation(extra = {}) {
      const data = await api("/api/conversations", {
        method: "POST",
        body: JSON.stringify({
          title: extra.title || "新会话",
          icon_key: extra.icon_key,
          icon_color: extra.icon_color,
        }),
      });
      await this.refreshConversations();
      this.messages = [];
      this.privateThreads = [];
      this.activeId = data.id;
      this.searchOpen = false;
      this.botOpen = false;
      if (extra.greet !== false) await this.startIntake(data.id);
      return data;
    },
    async startIntake(id) {
      this.intakeBusy = true;
      try {
        const data = await api(`/api/conversations/${id}/intake`, { method: "POST", body: "{}" });
        if (this.activeId === id) this.messages = data.items || [];
        await this.refreshConversations();
      } catch (error) {
        this.notify(error.message || "开场没有生成");
      } finally {
        if (this.activeId === id) this.intakeBusy = false;
      }
    },
    async answerIntake(messageId, choice) {
      const id = this.activeId;
      if (!id || this.intakeBusy) return;
      this.intakeBusy = true;
      try {
        const data = await api(`/api/conversations/${id}/intake/answer`, {
          method: "POST",
          body: JSON.stringify({ message_id: messageId, choice }),
        });
        if (this.activeId === id) this.messages = data.items || [];
        await this.refreshConversations();
      } catch (error) {
        this.notify(error.message || "没能记下这个选择");
      } finally {
        if (this.activeId === id) this.intakeBusy = false;
      }
    },
    async createGroup(body) {
      try {
        const data = await api("/api/groups", {
          method: "POST",
          body: JSON.stringify(body),
        });
        await this.refreshConversations();
        this.botOpen = false;
        await this.select(data.id);
        return data;
      } catch (error) {
        this.notify(error.message || "创建群聊失败");
        return null;
      }
    },
    async reorderConversations(ids) {
      const rank = new Map(ids.map((id, index) => [Number(id), index]));
      this.conversations = this.conversations
        .map((item) => (rank.has(item.id) ? { ...item, sort_order: rank.get(item.id) } : item))
        .sort(compareConversations);
      try {
        await api("/api/conversations/reorder", { method: "POST", body: JSON.stringify({ ids }) });
      } catch (error) {
        this.notify(error.message || "顺序没有保存");
        await this.refreshConversations();
      }
    },
    async patchConversation(id, body) {
      const data = await api(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      const index = this.conversations.findIndex((item) => item.id === id);
      if (index >= 0) this.conversations[index] = data;
      return data;
    },
    async rename(id, title) {
      const data = await api(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      const index = this.conversations.findIndex((item) => item.id === id);
      if (index >= 0) this.conversations[index] = data;
    },
    async remove(id) {
      let removed = [Number(id)];
      try {
        const data = await api(`/api/conversations/${id}`, { method: "DELETE" });
        if (Array.isArray(data?.removed) && data.removed.length) removed = data.removed.map(Number);
      } catch (error) {
        if (error.message !== "会话不存在") {
          this.notify(error.message || "删除失败");
          return;
        }
      }
      const gone = new Set(removed);
      this.conversations = this.conversations.filter((item) => !gone.has(item.id));
      try {
        await this.refreshConversations();
      } catch {
        // 刷新失败时保留刚才从列表里拿掉的结果。
      }
      if (!this.conversations.some((item) => item.id === this.activeId)) {
        this.messages = [];
        this.privateThreads = [];
        this.activeId = this.conversations[0]?.id || null;
        if (this.activeId) await this.select(this.activeId);
      }
    },
    async patchActive(body) {
      if (!this.activeId) return;
      const data = await api(`/api/conversations/${this.activeId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      const index = this.conversations.findIndex((item) => item.id === data.id);
      if (index >= 0) this.conversations[index] = data;
    },
    applyAppearance() {
      const theme = this.settings.theme || "system";
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.dataset.theme = theme === "dark" || (theme === "system" && prefersDark) ? "dark" : "light";
      document.documentElement.style.setProperty("--pink", this.settings.accent || "#ff4b93");
      document.documentElement.style.setProperty("--app-font", FONT_STACKS[this.settings.font_family] || FONT_STACKS.grok);
      const size = Number(this.settings.font_size);
      document.documentElement.style.setProperty("--text-size", `${FONT_SIZES.includes(size) ? size : 17}px`);
    },
    async loadModels() {
      this.models = await api("/api/models");
    },
    async saveSettings(values) {
      const data = await api("/api/settings", { method: "PUT", body: JSON.stringify(values) });
      this.settings = data.values;
      this.userName = data.user?.name || this.userName;
      this.deepseekConfigured = data.deepseek_configured;
      this.deepseekKeySource = data.deepseek_key_source || "";
      this.applyAppearance();
      return data;
    },
    async upload(file) {
      if (!this.activeId) await this.createConversation({ greet: false });
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`/api/conversations/${this.activeId}/attachments`, { method: "POST", body: form });
      if (!response.ok) throw new Error(await response.text());
      const attachment = await response.json();
      this.pending.push(attachment);
    },
    async send() {
      const content = this.draft.trim();
      if ((!content && !this.pending.length) || this.streaming) return;
      if (!this.activeId) await this.createConversation({ greet: false });
      const conversation = this.active;
      const attachmentIds = this.pending.map((item) => item.id);
      const localAttachments = [...this.pending];
      this.draft = "";
      this.pending = [];
      this.streaming = true;
      let assistant = {
        id: `local-${Date.now()}`,
        role: "assistant",
        content: "",
        reasoning: "",
        tools: [],
        attachments: [],
        status: "streaming",
        approval: null,
        startedAt: Date.now(),
        segments: [],
        segment: { id: "", content: "", reasoning: "", tools: [], attachments: [] },
      };
      this.messages.push({
        id: `local-user-${Date.now()}`,
        role: "user",
        content,
        attachments: localAttachments,
        tools: [],
        status: "done",
      });
      this.messages.push(assistant);
      assistant = this.messages[this.messages.length - 1];
      window.clearTimeout(activityTimer);
      activityTimer = window.setTimeout(() => {
        if (assistant.status === "streaming" && !String(assistant.content || "").trim()) assistant.phase = "working";
      }, 700);
      const controller = new AbortController();
      streamAbort = controller;
      try {
        await streamChat(
          this.activeId,
          {
            content,
            attachment_ids: attachmentIds,
            provider: conversation?.provider,
            model: conversation?.model,
          },
          (event) => {
            if (event.type === "speaker") {
              const named = event.name || "";
              if (assistant.speaker || assistant.content || (assistant.tools || []).length) {
                assistant.status = "done";
                assistant = {
                  id: `local-${Date.now()}-${named}`,
                  role: "assistant",
                  speaker: named,
                  content: "",
                  reasoning: "",
                  tools: [],
                  attachments: [],
                  status: "streaming",
                  approval: null,
                  startedAt: Date.now(),
                  segments: [],
                  segment: { id: "", content: "", reasoning: "", tools: [], attachments: [] },
                };
                this.messages.push(assistant);
                assistant = this.messages[this.messages.length - 1];
              } else {
                assistant.speaker = named;
              }
              return;
            }
            this.applyEvent(assistant, event);
          },
          controller.signal,
        );
        if (assistant.status === "error") {
          this.notify(assistant.content || "发送失败");
        } else {
          await this.select(this.activeId);
        }
        await this.refreshConversations();
      } catch (error) {
        if (error.name !== "AbortError") {
          assistant.status = "error";
          assistant.content = assistant.content || error.message || "发送失败";
          this.notify(error.message || "发送失败");
        }
      } finally {
        window.clearTimeout(activityTimer);
        this.streaming = false;
        streamAbort = null;
      }
    },
    applyEvent(assistant, event) {
      if (!assistant.segment) assistant.segment = { id: "", content: "", reasoning: "", tools: [], attachments: [] };
      const nextStep = event.message_id && assistant.segment.id && event.message_id !== assistant.segment.id;
      if (nextStep && (event.type === "start" || event.type === "token" || event.type === "tool_start" || event.type === "reasoning")) {
        assistant.segments = assistant.segments || [];
        assistant.segments.push(assistant.segment);
        assistant.segment = { id: event.message_id, content: "", reasoning: "", tools: [], attachments: [] };
      } else if (event.message_id && !assistant.segment.id) {
        assistant.segment.id = event.message_id;
      }
      const segment = assistant.segment;
      if (event.type === "token") {
        segment.content += event.text || "";
        assistant.content += event.text || "";
        if (event.speaker) {
          assistant.handoff_target = event.handoff_target || "";
          segment.handoff_target = assistant.handoff_target;
          assistant.status = "done";
        }
      }
      if (event.type === "reasoning") {
        segment.reasoning += event.text || "";
        assistant.reasoning += event.text || "";
      }
      if (event.type === "tool_start") {
        const tool = {
          id: event.id,
          name: event.name,
          arguments: event.arguments,
          result: "",
          ok: true,
          running: true,
        };
        segment.tools.push(tool);
        assistant.tools.push(tool);
      }
      if (event.type === "tool_end") {
        const tool = assistant.tools.find((item) => item.id === event.id);
        if (tool) {
          tool.result = event.preview || "";
          tool.diff = event.diff || "";
          tool.ok = event.ok;
          tool.running = false;
        }
        if (event.attachments?.length) {
          assistant.attachments.push(...event.attachments);
          segment.attachments.push(...event.attachments);
        }
      }
      if (event.type === "approval_required") {
        assistant.approval = event;
        segment.approval = event;
      }
      if (event.type === "question_required") {
        assistant.question = event;
        segment.question = event;
      }
      if (event.type === "mode") {
        const row = this.conversations.find((item) => item.id === this.activeId);
        if (row) row.permission_mode = event.permission_mode;
      }
      if (event.type === "error") {
        assistant.status = "error";
        assistant.content = assistant.content || event.message;
      }
      if (event.type === "done") assistant.status = "done";
    },
    async decide(approvalId, approved) {
      await api(`/api/approvals/${approvalId}`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      });
      const last = [...this.messages].reverse().find((item) => item.approval || item.question);
      if (last) {
        last.approval = null;
        last.question = null;
      }
    },
    async answerQuestion(approvalId, answers) {
      await api(`/api/approvals/${approvalId}`, {
        method: "POST",
        body: JSON.stringify({ approved: true, answers }),
      });
      const last = [...this.messages].reverse().find((item) => item.question);
      if (last) last.question = null;
    },
    async rewind() {
      if (!this.activeId) {
        this.notify("先打开一个会话");
        return;
      }
      try {
        const data = await api(`/api/conversations/${this.activeId}/rewind`, {
          method: "POST",
          body: JSON.stringify({ batches: 1 }),
        });
        if (!data.restored?.length) {
          this.notify(data.message || "没有可撤销的文件改动");
          return;
        }
        await this.select(this.activeId);
        this.notify("已撤销最近一批文件改动");
      } catch (error) {
        this.notify(error.message || "撤销失败");
      }
    },
    stop() {
      if (this.activeId) {
        api(`/api/conversations/${this.activeId}/cancel`, { method: "POST" }).catch(() => {});
      }
      streamAbort?.abort();
    },
    async share() {
      const lines = this.messages
        .filter((item) => item.role === "user" || item.role === "assistant")
        .map((item) => `${item.role === "user" ? "我" : "助手"}：${item.content || ""}`);
      await navigator.clipboard.writeText(lines.join("\n\n"));
      this.notify("对话已复制");
    },
    openSettings(tab = "general") {
      this.settingsTab = tab;
      this.settingsOpen = true;
      this.settingsStamp += 1;
    },
    async updateProfile(body) {
      const user = await api("/api/auth/profile", { method: "POST", body: JSON.stringify(body) });
      this.userName = user.name || this.userName;
      this.username = user.username || this.username;
      return user;
    },
    async loadLibrary() {
      const [memories, tasks, mcp, skills] = await Promise.all([
        api("/api/memories"),
        api(this.activeId ? `/api/tasks?conversation_id=${this.activeId}` : "/api/tasks"),
        api("/api/mcp"),
        api("/api/skills"),
      ]);
      this.memories = memories.items;
      this.tasks = tasks.items;
      this.mcp = mcp.items;
      this.skills = skills.items;
      this.loadMcpStatus();
    },
    async addSkill(form) {
      await api("/api/skills", { method: "POST", body: JSON.stringify(form) });
      this.skills = (await api("/api/skills")).items;
      this.notify("已添加技能");
    },
    async openConnect() {
      this.connectOpen = !this.connectOpen;
      if (!this.connectOpen) return;
      if (phoneLayout()) this.sidebarOpen = false;
      try {
      const [doctor, models] = await Promise.all([api("/api/doctor"), api("/api/models")]);
      this.doctor = doctor;
      this.models = models;
      } catch (error) {
        this.notify(error.message || "连接面板加载失败");
      }
    },
    async testProvider(provider) {
      this.testing = provider;
      this.testResult = "";
      try {
      const active = this.active;
      const model = provider === "ollama"
        ? (active?.provider === "ollama" ? active.model : this.settings.ollama_model || "qwen3:4b")
        : (active?.provider === "deepseek" ? active.model : this.settings.deepseek_model || "deepseek-chat");
        const result = await api("/api/providers/test", {
          method: "POST",
          body: JSON.stringify({ provider, model }),
        });
        this.testResult = `${result.provider} / ${result.model}：${result.text}`;
      } catch (error) {
        this.testResult = error.message;
      } finally {
        this.testing = "";
      }
    },
    async addMcp(form) {
      await api("/api/mcp", { method: "POST", body: JSON.stringify(form) });
      this.mcp = (await api("/api/mcp")).items;
      this.notify("已添加 MCP");
      this.loadMcpStatus();
    },
    async removeMcp(id) {
      await api(`/api/mcp/${id}`, { method: "DELETE" });
      this.mcp = (await api("/api/mcp")).items;
      this.notify("已移除 MCP");
      this.loadMcpStatus();
    },
    async loadMcpStatus() {
      this.mcpStatus = null;
      try {
        this.mcpStatus = await api("/api/mcp/status");
      } catch (error) {
        this.mcpStatus = { servers: [], error: error.message };
      }
    },
    async compact() {
      if (!this.activeId) return;
      const data = await api(`/api/conversations/${this.activeId}/compact`, { method: "POST" });
      this.notify("上下文已压缩");
      return data.summary;
    },
  },
});
