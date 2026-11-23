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
        <button type="button" :class="{ active: tab === 'mcp' }" @click="tab = 'mcp'">
          <svg viewBox="0 0 24 24"><path d="M9 7v4M15 7v4M8 11h8v5a4 4 0 0 1-8 0zM12 16v3" /></svg>
          MCP
        </button>
        <button type="button" :class="{ active: tab === 'skills' }" @click="tab = 'skills'">
          <svg viewBox="0 0 24 24"><path d="M6 4h9l3 3v13H6zM15 4v4h4M8 12h8M8 16h6" /></svg>
          Skills
        </button>
        <button type="button" :class="{ active: tab === 'memory' }" @click="tab = 'memory'">
          <svg viewBox="0 0 24 24"><path d="M12 4a6 6 0 0 0-6 6c0 2.2 1.2 3.6 2.2 4.6.6.6 1 1.4 1.1 2.2h5.4c.1-.8.5-1.6 1.1-2.2 1-1 2.2-2.4 2.2-4.6a6 6 0 0 0-6-6zM9.5 20h5" /></svg>
          记忆
        </button>
        <button type="button" :class="{ active: tab === 'tasks' }" @click="tab = 'tasks'">
          <svg viewBox="0 0 24 24"><path d="M8 6h11M8 12h11M8 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" /></svg>
          任务
        </button>
      </aside>
      <div class="settings-main">
        <header>
          <h2>{{ currentLabel }}</h2>
          <button type="button" aria-label="关闭" @click="chat.settingsOpen = false">×</button>
        </header>

        <div v-if="tab === 'general'" class="settings-body">
          <section class="settings-card">
            <h3>账号</h3>
            <p class="settings-note">用户名 {{ chat.username || "未设置" }}。这里改的是显示名和密码，登录用的用户名不变。</p>
            <label class="settings-field">显示名<input v-model="displayName" maxlength="64" autocomplete="nickname" /></label>
            <div class="settings-actions">
              <button type="button" data-testid="save-display-name" :disabled="savingProfile" @click="saveName">保存显示名</button>
            </div>
            <label class="settings-field">当前密码<input v-model="currentPassword" type="password" autocomplete="current-password" /></label>
            <label class="settings-field">新密码<input v-model="newPassword" type="password" autocomplete="new-password" /></label>
            <label class="settings-field">再输入一次新密码<input v-model="confirmPassword" type="password" autocomplete="new-password" /></label>
            <div class="settings-actions">
              <button type="button" data-testid="save-password" :disabled="savingProfile" @click="savePassword">修改密码</button>
            </div>
          </section>
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
            <h3>默认模型</h3>
            <label class="settings-row">
              <span v-if="!choices.length">配置完成之后才能选择</span>
              <select data-testid="default-model" :disabled="!choices.length" :value="selectedModel" @change="chooseModel">
                <option v-if="!choices.length" value="">还没有可选择的模型</option>
                <optgroup v-for="provider in choices" :key="provider.id" :label="provider.label">
                  <option v-for="name in provider.models" :key="provider.id + name" :value="provider.id + ':' + name">{{ modelLabel(provider.id, name) }}</option>
                </optgroup>
              </select>
            </label>
            <label class="settings-field">过载时改用<input :value="chat.settings.fallback_model || ''" placeholder="留空则不切换，例如 deepseek-reasoner" @change="save({ fallback_model: $event.target.value })" /></label>
          </section>
          <section class="settings-card">
            <h3>DeepSeek</h3>
            <p class="settings-note">{{ deepseekHint }}</p>
            <label class="settings-field">接口地址<input v-model="deepseekBase" autocomplete="off" /></label>
            <label class="settings-field">API Key<input v-model="deepseekKey" type="password" autocomplete="new-password" placeholder="留空表示不修改已保存的密钥" /></label>
            <div class="settings-actions">
              <button type="button" data-testid="save-deepseek" :disabled="saving" @click="saveDeepseek">保存</button>
              <button v-if="chat.deepseekKeySource === 'settings'" type="button" class="ghost" @click="clearDeepseek">清除已保存的密钥</button>
            </div>
            <p v-if="deepseekStatus" class="settings-note" data-testid="deepseek-save-status">{{ deepseekStatus }}</p>
          </section>
          <section class="settings-card">
            <h3>Ollama</h3>
            <p class="settings-note">{{ ollamaHint }}</p>
            <label class="settings-field">服务地址<input v-model="ollamaBase" autocomplete="off" /></label>
            <div class="settings-actions">
              <button type="button" data-testid="save-ollama" :disabled="saving" @click="saveOllama">保存并检查</button>
            </div>
          </section>
        </div>

        <div v-else-if="tab === 'computer'" class="settings-body">
          <section class="settings-card">
            <h3>工作区</h3>
            <label class="settings-field">目录<input :value="chat.settings.workspace || ''" readonly /></label>
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

        <div v-else-if="tab === 'mcp'" class="settings-body">
          <section class="settings-card">
            <h3>MCP</h3>
            <p class="settings-note">可以同时接入多个。下一轮对话会把各服务的工具放进模型的工具列表。</p>
            <p v-if="!chat.mcp.length" class="settings-note">还没有接入 MCP 服务。</p>
            <div v-for="item in chat.mcp" :key="item.id" class="mcp-row">
              <div>
                <b>{{ item.name }}</b>
                <small>
                  <template v-for="piece in commandPieces(item)" :key="piece.key">{{ piece.text }}<wbr v-if="piece.breakAfter" /></template>
                </small>
                <small v-if="toolLine(item)">{{ toolLine(item) }}</small>
                <small v-else-if="chat.mcpStatus === null">正在读取工具…</small>
                <small v-else-if="serverError(item)">{{ serverError(item) }}</small>
              </div>
              <button type="button" @click="chat.removeMcp(item.id)">移除</button>
            </div>
            <form class="mcp-form" @submit.prevent="addMcp">
              <input v-model="mcp.name" placeholder="名称" required />
              <input v-model="mcp.command" placeholder="命令，例如 python" required />
              <input v-model="mcp.args" placeholder="参数，用空格分开" />
              <textarea v-model="mcp.env" rows="3" placeholder="环境变量，每行 KEY=VALUE，可留空"></textarea>
              <button type="submit">添加</button>
            </form>
          </section>
        </div>

        <div v-else-if="tab === 'skills'" class="settings-body">
          <section class="settings-card">
            <h3>Skills</h3>
            <p class="settings-note">对话里输入 /skill 名称 就能用。内置和工作区里的技能会列在这里，也可以再添加一条。</p>
            <p v-if="!chat.skills.length" class="settings-note">还没有技能。</p>
            <div v-for="item in chat.skills" :key="item.name" class="mcp-row">
              <div>
                <b>{{ item.name }}</b>
                <small>{{ skillSource(item.source) }}<template v-if="item.description"> · {{ item.description }}</template></small>
              </div>
            </div>
            <form class="mcp-form" @submit.prevent="addSkill">
              <input v-model="skill.name" placeholder="名称" required />
              <input v-model="skill.description" placeholder="一句话说明" />
              <textarea v-model="skill.body" rows="4" placeholder="技能内容" required></textarea>
              <button type="submit">添加</button>
            </form>
          </section>
        </div>

        <div v-else-if="tab === 'memory'" class="settings-body">
          <section class="settings-card">
            <h3>记忆</h3>
            <p v-if="!chat.memories.length" class="settings-note">这个工作区还没有长期记忆。</p>
            <p v-for="item in chat.memories" :key="item.id">
              {{ item.type_label || item.type || "用户" }} · {{ item.title }}（{{ item.age || "今天" }}）
              <span v-if="item.description"> — {{ item.description }}</span>
            </p>
          </section>
        </div>

        <div v-else-if="tab === 'tasks'" class="settings-body">
          <section class="settings-card">
            <h3>任务</h3>
            <p class="settings-note">{{ chat.activeId ? "这里是当前会话的任务。" : "还没有打开会话，这里是全部任务。" }}</p>
            <p v-if="!chat.tasks.length" class="settings-note">当前没有任务。</p>
            <p v-for="item in chat.tasks" :key="item.id">#{{ item.id }} [{{ item.status }}] {{ item.title }}</p>
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
const tab = ref(chat.settingsTab || "general");
const saving = ref(false);
const savingProfile = ref(false);
const displayName = ref(chat.userName);
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const deepseekBase = ref(chat.settings.deepseek_base || "https://api.deepseek.com");
const deepseekKey = ref("");
const deepseekStatus = ref("");
const ollamaBase = ref(chat.settings.ollama_base || "http://127.0.0.1:11434");
const usage = ref(null);
const mcp = ref({ name: "", command: "", args: "", env: "" });
const skill = ref({ name: "", description: "", body: "" });

const tabs = [
  { id: "general", label: "通用" },
  { id: "models", label: "配置模型" },
  { id: "computer", label: "电脑" },
  { id: "permissions", label: "权限" },
  { id: "usage", label: "用量" },
  { id: "mcp", label: "MCP" },
  { id: "skills", label: "Skills" },
  { id: "memory", label: "记忆" },
  { id: "tasks", label: "任务" },
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
  if (provider?.configured) return `已连上，发现 ${provider.models.length} 个模型，可以在默认模型里选择。`;
  return "保存地址并检查通过之后，才能选择本地模型。";
});

function skillSource(source) {
  if (source === "workspace") return "工作区";
  if (source === "db") return "自己添加";
  return "内置";
}

function serverState(item) {
  return (chat.mcpStatus?.servers || []).find((server) => server.name === item.name);
}

function commandPieces(item) {
  const text = [item.command, ...(item.args || [])].filter(Boolean).join(" ");
  return text.split(/([\\/\s])/).filter(Boolean).map((part, index) => ({
    key: `${index}-${part}`,
    text: part,
    breakAfter: part === "\\" || part === "/" || /\s/.test(part),
  }));
}

function toolLine(item) {
  return (serverState(item)?.tools || []).join("、");
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

async function addMcp() {
  try {
    await chat.addMcp({
      name: mcp.value.name,
      command: mcp.value.command,
      args: mcp.value.args.split(" ").filter(Boolean),
      env: parseEnv(mcp.value.env),
    });
    mcp.value = { name: "", command: "", args: "", env: "" };
  } catch (error) {
    chat.notify(error.message || "没有添加");
  }
}

async function addSkill() {
  try {
    await chat.addSkill({
      name: skill.value.name.trim(),
      description: skill.value.description.trim(),
      body: skill.value.body,
    });
    skill.value = { name: "", description: "", body: "" };
  } catch (error) {
    chat.notify(error.message || "没有添加");
  }
}

async function saveName() {
  const name = displayName.value.trim();
  if (!name) {
    chat.notify("显示名不能为空");
    return;
  }
  savingProfile.value = true;
  try {
    await chat.updateProfile({ name });
    chat.notify("显示名已更新");
  } catch (error) {
    chat.notify(error.message || "显示名没有保存");
  } finally {
    savingProfile.value = false;
  }
}

async function savePassword() {
  if (newPassword.value.length < 6) {
    chat.notify("新密码至少 6 位");
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    chat.notify("两次输入的新密码不一致");
    return;
  }
  savingProfile.value = true;
  try {
    await chat.updateProfile({ current_password: currentPassword.value, new_password: newPassword.value });
    currentPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";
    chat.notify("密码已更新");
  } catch (error) {
    chat.notify(error.message || "密码没有修改");
  } finally {
    savingProfile.value = false;
  }
}

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
    const nextKey = deepseekKey.value.trim();
    const body = { deepseek_base: deepseekBase.value.trim() };
    if (nextKey) body.deepseek_api_key = nextKey;
    await chat.saveSettings(body);
    deepseekKey.value = "";
    await chat.loadModels();
    const status = !chat.deepseekConfigured
      ? "接口地址已保存。当前账号还没有可用的密钥。"
      : nextKey
        ? "已保存。当前账号的接口地址和 API Key 已覆盖。"
        : "已保存。当前账号的接口地址已覆盖；密钥留空，仍用原来的。";
    deepseekStatus.value = status;
    chat.notify(status);
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
    deepseekStatus.value = "已清除当前账号保存的密钥。";
    chat.notify(deepseekStatus.value);
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
  if (["mcp", "skills", "memory", "tasks"].includes(value)) {
    chat.loadLibrary().catch((error) => chat.notify(error.message || "没有读到"));
  }
});

watch(
  () => chat.settingsStamp,
  () => {
    tab.value = chat.settingsTab || "general";
  },
);

watch(
  () => chat.userName,
  (value) => {
    displayName.value = value || "";
  },
);

onMounted(() => {
  window.addEventListener("keydown", onKey);
  chat.loadModels().catch(() => {});
  if (tab.value === "usage") loadUsage();
  if (["mcp", "skills", "memory", "tasks"].includes(tab.value)) {
    chat.loadLibrary().catch((error) => chat.notify(error.message || "没有读到"));
  }
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
