<template>
  <div class="sheet-mask" @click.self="chat.connectOpen = false">
    <section class="sheet" data-testid="connect-sheet">
      <header>
        <h2>连接应用</h2>
        <button type="button" @click="chat.connectOpen = false">关闭</button>
      </header>
      <div class="sheet-body">
        <div class="status-row">
          <span>数据库</span><b>{{ databaseDetail }}</b>
        </div>
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
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useChatStore } from "../store";

const chat = useChatStore();
const workspace = ref(chat.settings.workspace || "");

watch(
  () => chat.settings.workspace,
  (value) => {
    workspace.value = value || "";
  },
);
const ollamaDetail = computed(() => chat.doctor?.checks?.find((item) => item.name === "ollama")?.detail || "检查中");
const databaseDetail = computed(() => {
  const item = (chat.doctor?.checks || []).find((row) => row.name === "mysql" || row.name === "sqlite");
  return item?.detail || "检查中";
});

function saveWorkspace() {
  chat.saveSettings({ workspace: workspace.value });
  if (chat.activeId) chat.patchActive({ workspace: workspace.value });
}
</script>
