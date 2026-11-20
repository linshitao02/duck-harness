<template>
  <div class="sheet-mask" @click.self="chat.connectOpen = false">
    <section class="sheet" data-testid="connect-sheet">
      <header>
        <h2>连接应用</h2>
        <button type="button" @click="chat.connectOpen = false">关闭</button>
      </header>
      <div class="sheet-body">
        <div class="status-row">
          <span>本机终端</span><b>已连接</b>
        </div>
        <div class="status-row">
          <span>工作区文件</span><b>已连接</b>
        </div>
        <div class="status-row">
          <span>联网搜索</span><b>已连接</b>
        </div>
        <label class="field">
          工作区
          <input v-model="workspace" @change="saveWorkspace" />
        </label>
        <div class="provider-line">
          <div>
            <strong>DeepSeek</strong>
            <small>{{ chat.deepseekConfigured ? "密钥已配置" : "未配置密钥" }}</small>
          </div>
          <button type="button" data-testid="test-deepseek" :disabled="chat.testing === 'deepseek'" @click="chat.testProvider('deepseek')">
            {{ chat.testing === "deepseek" ? "测试中" : "测试" }}
          </button>
        </div>
        <div class="provider-line">
          <div>
            <strong>Ollama</strong>
            <small>{{ ollamaDetail }}</small>
          </div>
          <button type="button" data-testid="test-ollama" :disabled="chat.testing === 'ollama'" @click="chat.testProvider('ollama')">
            {{ chat.testing === "ollama" ? "测试中" : "测试" }}
          </button>
        </div>
        <p v-if="chat.testResult" class="test-result" data-testid="test-result">{{ chat.testResult }}</p>
        <h3>MCP</h3>
        <p class="muted">可以同时接入多个。下一轮对话会把各服务的工具放进模型的工具列表。</p>
        <p v-if="!chat.mcp.length" class="muted">还没有接入 MCP 服务。</p>
        <div v-for="item in chat.mcp" :key="item.id" class="mcp-row">
          <div>
            <b>{{ item.name }}</b>
            <small>{{ item.command }} {{ (item.args || []).join(" ") }}</small>
            <small v-if="toolLine(item)">{{ toolLine(item) }}</small>
            <small v-else-if="chat.mcpStatus === null">正在读取工具…</small>
            <small v-else-if="serverError(item)">{{ serverError(item) }}</small>
          </div>
          <button type="button" @click="chat.removeMcp(item.id)">移除</button>
        </div>
        <form class="mcp-form" @submit.prevent="add">
          <input v-model="mcp.name" placeholder="名称" required />
          <input v-model="mcp.command" placeholder="命令，例如 python" required />
          <input v-model="mcp.args" placeholder="参数，用空格分开" />
          <textarea v-model="mcp.env" rows="3" placeholder="环境变量，每行 KEY=VALUE，可留空"></textarea>
          <button type="submit">添加</button>
        </form>
        <h3>记忆</h3>
        <p v-if="!chat.memories.length" class="muted">这个工作区还没有长期记忆。</p>
        <p v-for="item in chat.memories" :key="item.id">
          {{ item.type_label || item.type || "用户" }} · {{ item.title }}（{{ item.age || "今天" }}）
          <span v-if="item.description"> — {{ item.description }}</span>
        </p>
        <h3>任务</h3>
        <p v-if="!chat.tasks.length" class="muted">当前没有任务。</p>
        <p v-for="item in chat.tasks" :key="item.id">#{{ item.id }} [{{ item.status }}] {{ item.title }}</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useChatStore } from "../store";

const chat = useChatStore();
const workspace = ref(chat.settings.workspace || "");
const mcp = ref({ name: "", command: "", args: "", env: "" });

function serverState(item) {
  return (chat.mcpStatus?.servers || []).find((server) => server.name === item.name);
}

function toolLine(item) {
  const tools = serverState(item)?.tools || [];
  return tools.join("、");
}

function serverError(item) {
  return serverState(item)?.error || chat.mcpStatus?.error || "";
}

function parseEnv(text) {
  const env = {};
  for (const line of String(text || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    const key = trimmed.slice(0, index).trim();
    if (key) env[key] = trimmed.slice(index + 1);
  }
  return env;
}
watch(
  () => chat.settings.workspace,
  (value) => {
    workspace.value = value || "";
  },
);
const ollamaDetail = computed(() => chat.doctor?.checks?.find((item) => item.name === "ollama")?.detail || "检查中");

function saveWorkspace() {
  chat.saveSettings({ workspace: workspace.value });
  if (chat.activeId) chat.patchActive({ workspace: workspace.value });
}

function add() {
  chat.addMcp({
    name: mcp.value.name,
    command: mcp.value.command,
    args: mcp.value.args.split(" ").filter(Boolean),
    env: parseEnv(mcp.value.env),
  });
  mcp.value = { name: "", command: "", args: "", env: "" };
}
</script>
