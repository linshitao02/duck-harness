<template>
  <div class="settings-mask" @mousedown.self="chat.settingsOpen = false">
    <section class="settings-window" role="dialog" aria-label="设置" data-testid="settings-dialog" @keydown.esc.stop="chat.settingsOpen = false">
      <aside>
        <button type="button" :class="{ active: tab === 'general' }" data-testid="settings-tab-general" @click="tab = 'general'">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" /><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18" /></svg>
          通用
        </button>
        <button type="button" :class="{ active: tab === 'models' }" data-testid="settings-tab-models" @click="tab = 'models'">
          <svg viewBox="0 0 24 24"><path d="M4 8h16v10H4zM8 8V5h8v3" /></svg>
          配置模型
        </button>
        <button type="button" :class="{ active: tab === 'computer' }" data-testid="settings-tab-computer" @click="tab = 'computer'">
          <svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="11" rx="1.5" /><path d="M8 20h8M12 16v4" /></svg>
          电脑
        </button>
        <button type="button" :class="{ active: tab === 'permissions' }" data-testid="settings-tab-permissions" @click="tab = 'permissions'">
          <svg viewBox="0 0 24 24"><path d="M12 3 5 6v6c0 4 3 6.5 7 8 4-1.5 7-4 7-8V6z" /></svg>
          权限
        </button>
        <button type="button" :class="{ active: tab === 'usage' }" data-testid="settings-tab-usage" @click="tab = 'usage'">
          <svg viewBox="0 0 24 24"><path d="M5 19V10M12 19V5M19 19v-7" /></svg>
          用量
        </button>
      </aside>
      <div class="settings-main">
        <header>
          <h2>{{ currentLabel }}</h2>
          <button type="button" aria-label="关闭" @click="chat.settingsOpen = false">×</button>
        </header>

        <div v-if="tab === 'general'" class="settings-body">
          <section class="settings-card">
            <h3>外观</h3>
            <label class="settings-row">
              <span>主题</span>
              <select :value="chat.settings.theme || 'system'" @change="save({ theme: $event.target.value })">
                <option value="system">跟随系统</option>
                <option value="light">浅色</option>
                <option value="dark">深色</option>
              </select>
            </label>
            <label class="settings-row">
              <span>强调色</span>
              <select :value="chat.settings.accent || '#ff4b93'" @change="save({ accent: $event.target.value })">
                <option value="#ff4b93">粉色</option>
                <option value="#3b82f6">蓝色</option>
                <option value="#7c4dff">紫色</option>
                <option value="#3cbe5a">绿色</option>
                <option value="#f08a24">橙色</option>
              </select>
            </label>
            <label class="settings-row">
              <span>字体</span>
              <select data-testid="font-select" :value="chat.settings.font_family || 'grok'" @change="save({ font_family: $event.target.value })">
                <option value="grok">微软雅黑 UI</option>
                <option value="dengxian">等线</option>
                <option value="yahei">微软雅黑</option>
                <option value="segoe">Segoe UI</option>
                <option value="sourcehan">思源黑体</option>
                <option value="kaiti">楷体</option>
                <option value="song">宋体</option>
              </select>
            </label>
            <label class="settings-row">
              <span>字号</span>
              <select data-testid="font-size" :value="chat.settings.font_size || '17'" @change="save({ font_size: $event.target.value })">
                <option v-for="size in FONT_SIZES" :key="size" :value="String(size)">{{ size }}</option>
              </select>
            </label>
            <label class="settings-row">
              <span>语言</span>
              <select disabled>
                <option>中文</option>
              </select>
            </label>
            <div class="settings-row">
              <span>输入时检查拼写</span>
              <button type="button" class="switch" :class="{ on: chat.settings.spellcheck !== '0' }" role="switch" :aria-checked="chat.settings.spellcheck !== '0'" @click="save({ spellcheck: chat.settings.spellcheck === '0' ? '1' : '0' })">
                <i />
              </button>
            </div>
            <div class="settings-row">
              <span>使用 Ctrl+Enter 发送</span>
              <button type="button" class="switch" :class="{ on: chat.settings.send_shortcut === 'ctrl_enter' }" role="switch" :aria-checked="chat.settings.send_shortcut === 'ctrl_enter'" @click="save({ send_shortcut: chat.settings.send_shortcut === 'ctrl_enter' ? 'enter' : 'ctrl_enter' })">
                <i />
              </button>
            </div>
          </section>
        </div>

        <div v-else-if="tab === 'models'" class="settings-body" data-testid="settings-models">
          <section class="settings-card">
            <h3>DeepSeek</h3>
            <p class="settings-note">{{ deepseekHint }}</p>
            <label class="settings-field">接口地址<input v-model="deepseekBase" autocomplete="off" /></label>
            <label class="settings-field">API Key<input v-model="deepseekKey" type="password" autocomplete="new-password" placeholder="留空表示不修改已保存的密钥" /></label>
            <div class="settings-actions">
              <button type="button" data-testid="save-deepseek" :disabled="saving" @click="saveDeepseek">保存</button>
              <button v-if="chat.deepseekKeySource === 'settings'" type="button" class="ghost" @click="clearDeepseek">清除已保存的密钥</button>
            </div>
          </section>
          <section class="settings-card">
            <h3>Ollama</h3>
            <p class="settings-note">{{ ollamaHint }}</p>
            <label class="settings-field">服务地址<input v-model="ollamaBase" autocomplete="off" /></label>
            <div class="settings-actions">
              <button type="button" data-testid="save-ollama" :disabled="saving" @click="saveOllama">保存并检查</button>
            </div>
          </section>
          <section class="settings-card">
            <h3>默认模型</h3>
            <label class="settings-field">过载时改用<input :value="chat.settings.fallback_model || ''" placeholder="留空则不切换，例如 deepseek-reasoner" @change="save({ fallback_model: $event.target.value })" /></label>
            <label class="settings-row">
              <span>{{ choices.length ? "默认模型" : "配置完成之后才能选择" }}</span>
              <select data-testid="default-model" :disabled="!choices.length" :value="selectedModel" @change="chooseModel">
                <option v-if="!choices.length" value="">还没有可选择的模型</option>
                <optgroup v-for="provider in choices" :key="provider.id" :label="provider.label">
                  <option v-for="name in provider.models" :key="provider.id + name" :value="provider.id + ':' + name">{{ modelLabel(provider.id, name) }}</option>
                </optgroup>
              </select>
            </label>
          </section>
        </div>

        <div v-else-if="tab === 'computer'" class="settings-body">
          <section class="settings-card">
            <h3>工作区</h3>
            <label class="settings-field">目录<input v-model="workspace" /></label>
            <div class="settings-actions">
              <button type="button" @click="save({ workspace })">保存</button>
            </div>
          </section>
        </div>

        <div v-else-if="tab === 'permissions'" class="settings-body">
          <section class="settings-card">
            <h3>默认权限</h3>
            <label v-for="mode in modes" :key="mode.id" class="settings-choice">
              <input type="radio" name="permission" :value="mode.id" :checked="(chat.settings.permission_mode || 'auto') === mode.id" @change="save({ permission_mode: mode.id })" />
              <span>
                <strong>{{ mode.label }}</strong>
                <small>{{ mode.detail }}</small>
              </span>
            </label>
          </section>
        </div>

        <div v-else class="settings-body">
          <section class="settings-card">
            <h3>用量</h3>
            <p v-if="!usage" class="settings-note">正在读取</p>
            <template v-else>
              <p class="settings-note">输入 {{ usage.prompt_tokens }}，输出 {{ usage.completion_tokens }}。{{ usage.note }}</p>
              <div v-for="row in usage.by_model" :key="row.model" class="settings-row">
                <span>{{ row.model || "未知模型" }}</span>
                <span>{{ row.prompt_tokens + row.completion_tokens }} tokens</span>
              </div>
            </template>
          </section>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "../api";
import { FONT_SIZES, useChatStore } from "../store";

const chat = useChatStore();
const tab = ref("general");
const saving = ref(false);
const deepseekBase = ref(chat.settings.deepseek_base || "https://api.deepseek.com");
const deepseekKey = ref("");
const ollamaBase = ref(chat.settings.ollama_base || "http://127.0.0.1:11434");
const workspace = ref(chat.settings.workspace || "");
const usage = ref(null);

const tabs = [
  { id: "general", label: "通用" },
  { id: "models", label: "配置模型" },
  { id: "computer", label: "电脑" },
  { id: "permissions", label: "权限" },
  { id: "usage", label: "用量" },
];
const modes = [
  { id: "auto", label: "自动", detail: "工具可以直接执行" },
  { id: "ask", label: "询问", detail: "修改前先征求同意" },
  { id: "plan", label: "只读计划", detail: "只查看，不改文件" },
  { id: "edits", label: "改文件免问", detail: "改文件直接执行，命令仍会询问" },
  { id: "dontAsk", label: "不询问", detail: "需要确认的操作直接拒绝" },
];

const currentLabel = computed(() => tabs.find((item) => item.id === tab.value)?.label || "设置");
const choices = computed(() => (chat.models.providers || []).filter((item) => item.configured && item.models?.length));
const selectedModel = computed(() => {
  const value = `${chat.settings.provider || ""}:${chat.settings.model || ""}`;
  return choices.value.some((item) => item.id === chat.settings.provider && item.models.includes(chat.settings.model)) ? value : "";
});
const deepseekHint = computed(() => {
  if (chat.deepseekKeySource === "settings") return "正在使用设置里保存的密钥。密钥只留在本机数据库，不会写进代码，也不会再显示出来。";
  if (chat.deepseekKeySource === "env") return "正在使用本机环境变量里的密钥。这里不会显示它；保存新密钥后改为使用设置里的。";
  return "还没有密钥。保存之后才能在对话里选择 DeepSeek。";
});
const ollamaHint = computed(() => {
  const provider = chat.models.providers?.find((item) => item.id === "ollama");
  if (provider?.configured) return `已连上，发现 ${provider.models.length} 个模型，可以在上面选择。`;
  return "保存地址并检查通过之后，才能选择本地模型。";
});

function modelLabel(provider, name) {
  if (provider === "deepseek" && name === "deepseek-chat") return "DeepSeek";
  if (provider === "deepseek" && name === "deepseek-reasoner") return "DeepSeek 思考";
  return name;
}

async function save(values) {
  try {
    await chat.saveSettings(values);
  } catch (error) {
    chat.notify(error.message || "设置没有保存");
  }
}

async function saveDeepseek() {
  saving.value = true;
  try {
    const body = { deepseek_base: deepseekBase.value.trim() };
    if (deepseekKey.value.trim()) body.deepseek_api_key = deepseekKey.value.trim();
    await chat.saveSettings(body);
    deepseekKey.value = "";
    await chat.loadModels();
    chat.notify(chat.deepseekConfigured ? "DeepSeek 已配置，现在可以选择" : "还没有可用的密钥");
  } catch (error) {
    chat.notify(error.message || "没有保存");
  } finally {
    saving.value = false;
  }
}

async function clearDeepseek() {
  saving.value = true;
  try {
    await chat.saveSettings({ clear_deepseek_key: true });
    deepseekKey.value = "";
    await chat.loadModels();
  } catch (error) {
    chat.notify(error.message || "没有清除");
  } finally {
    saving.value = false;
  }
}

async function saveOllama() {
  saving.value = true;
  try {
    await chat.saveSettings({ ollama_base: ollamaBase.value.trim() });
    await chat.loadModels();
    const provider = chat.models.providers?.find((item) => item.id === "ollama");
    chat.notify(provider?.configured ? "Ollama 已配置，现在可以选择" : "地址已保存，但还没有发现可用模型");
  } catch (error) {
    chat.notify(error.message || "没有保存");
  } finally {
    saving.value = false;
  }
}

async function chooseModel(event) {
  const value = event.target.value;
  if (!value) return;
  const [provider, ...rest] = value.split(":");
  const model = rest.join(":");
  const body = provider === "deepseek" ? { provider, model, deepseek_model: model } : { provider, model, ollama_model: model };
  await save(body);
  if (chat.activeId) await chat.patchActive({ provider, model });
}

async function loadUsage() {
  try {
    usage.value = await api("/api/usage");
  } catch (error) {
    chat.notify(error.message || "用量没有读到");
  }
}

function onKey(event) {
  if (event.key === "Escape") chat.settingsOpen = false;
}

watch(tab, (value) => {
  if (value === "usage" && !usage.value) loadUsage();
});

onMounted(() => {
  window.addEventListener("keydown", onKey);
  chat.loadModels().catch(() => {});
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
