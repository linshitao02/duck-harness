<template>
  <main class="main">
    <header class="topbar pywebview-drag-region">
      <button
        class="sidebar-toggle"
        :class="{ open: chat.sidebarOpen }"
        type="button"
        data-testid="toggle-sidebar"
        :aria-label="chat.sidebarOpen ? '隐藏会话列表' : '显示会话列表'"
        @mousedown.stop
        @click="chat.toggleSidebar()"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3.5" y="4" width="17" height="16" rx="3.2" />
          <path d="M9.2 4.6v14.8" />
          <path class="pane" d="M5.4 7.1c0-.8.6-1.5 1.4-1.5h1.5v12.8H6.8c-.8 0-1.4-.7-1.4-1.5V7.1z" />
        </svg>
      </button>
      <div class="title-pill">
        <span class="logo" aria-hidden="true" :style="{ background: chat.active?.icon_color || '#ff4b93' }">
          <Badge :name="chat.active?.icon_key || 'spark'" />
        </span>
        <span class="title" data-testid="chat-title">{{ chat.active?.title || "新会话" }}</span>
        <select class="model-select" data-testid="model-select" :value="modelValue" @mousedown.stop @change="changeModel">
          <option v-if="!modelChoices.length" value="">请先在设置里配置模型</option>
          <optgroup v-for="provider in modelChoices" :key="provider.id" :label="provider.label">
            <option v-for="name in provider.models" :key="provider.id + name" :value="provider.id + ':' + name">{{ modelLabel(provider.id, name) }}</option>
          </optgroup>
        </select>
      </div>
      <div class="top-actions">
        <button class="icon-btn soft" type="button" aria-label="复制对话" @mousedown.stop @click="chat.share()">
          <svg viewBox="0 0 24 24"><path d="M8 12v8h10V8h-6l-4 4zM8 8V4h8v4" /></svg>
        </button>
        <div class="window-controls">
          <button type="button" aria-label="最小化" @mousedown.stop @click="windowAction('minimize')">–</button>
          <button type="button" aria-label="最大化" @mousedown.stop @click="windowAction('maximize')">□</button>
          <button type="button" class="close" aria-label="关闭" @mousedown.stop @click="windowAction('close')">×</button>
        </div>
      </div>
    </header>

    <section ref="scroller" class="thread" data-testid="thread">
      <div v-if="!chat.messages.length" class="empty">
        <h1>{{ chat.active?.title || "新会话" }}</h1>
        <p>直接说你要做的事。我可以改代码、跑本机命令、联网搜索，也会记住你的偏好。</p>
      </div>
      <template v-for="item in threadItems" :key="item.id">
        <div v-if="item.kind === 'divider'" class="new-divider"><span>新</span></div>
        <article v-else-if="item.kind === 'user'" class="row user" data-testid="message">
          <div class="hover-actions">
            <button type="button" aria-label="复制" @click="copy(item.message.content)">…</button>
            <button type="button" aria-label="再发一次" @click="again(item.message.content)">↩</button>
          </div>
          <div class="bubble user" :style="bubbleStyle(item.message)">
            <div v-if="item.message.content" class="text" v-html="renderText(item.message.content)" />
            <div v-for="file in item.message.attachments || []" :key="file.id" class="file-card" data-testid="file-card">
              <span class="file-icon" :data-kind="fileKind(file.filename)">{{ fileMark(file.filename) }}</span>
              <span class="file-meta">
                <strong :title="file.filename">{{ file.filename }}</strong>
                <small>{{ formatSize(file.size) }}</small>
              </span>
              <button type="button" class="file-download" data-testid="file-download" :aria-label="'下载 ' + file.filename" :disabled="savingId === file.id" @click="downloadFile(file)">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10" /><path d="M8 10.5 12 14.5l4-4" /><path d="M5 19h14" /></svg>
              </button>
            </div>
          </div>
        </article>
        <article v-else class="row assistant" data-testid="message">
          <details v-if="item.notes.length || item.tools.length || item.streaming" class="work" data-testid="work-log" :open="isOpen(item, 'work')" @toggle="onToggle(item, 'work', $event)">
            <summary>
              <svg class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
              <span v-if="item.streaming" class="activity-ticker">
                <span class="activity-shine">{{ liveLabel(item) }}</span>
              </span>
              <span v-else>工作了 {{ workDuration(item) }}</span>
            </summary>
            <div class="work-body">
              <p v-for="(note, index) in item.bodyNotes" :key="index">{{ note }}</p>
            </div>
          </details>
          <details v-if="!item.streaming && item.explore" class="work" data-testid="explore-log" :open="isOpen(item, 'explore')" @toggle="onToggle(item, 'explore', $event)">
            <summary>
              <svg class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
              <span>{{ item.explore }}</span>
            </summary>
            <ul class="work-tools">
              <li v-for="tool in item.tools" :key="tool.id">{{ toolLine(tool) }}</li>
            </ul>
          </details>
          <div v-if="showAnswer(item)" class="bubble assistant">
            <details v-if="item.reasoning" class="reasoning">
              <summary>思考过程</summary>
              <p>{{ item.reasoning }}</p>
            </details>
            <div v-if="item.answer" class="text" v-html="renderText(item.answer)" />
            <template v-for="tool in item.diffs" :key="tool.id">
              <div class="diff-view">
                <div v-for="(line, index) in tool.diff.split('\n')" :key="index" :class="diffClass(line)">{{ line }}</div>
              </div>
            </template>
            <button v-if="item.diffs.length" class="rewind-btn" type="button" @click="chat.rewind()">撤销最近一批文件改动</button>
            <div v-if="item.approval" class="approval">
              <p>{{ item.approval.name === "exit_plan_mode" ? "批准这份计划？" : "允许执行 " + item.approval.name + "？" }}</p>
              <pre v-if="item.approval.preview" class="plan-preview">{{ item.approval.preview }}</pre>
              <div>
                <button type="button" @click="chat.decide(item.approval.approval_id, true)">{{ item.approval.name === "exit_plan_mode" ? "批准" : "允许" }}</button>
                <button type="button" class="ghost" @click="chat.decide(item.approval.approval_id, false)">拒绝</button>
              </div>
            </div>
            <div v-if="item.questionCard" class="approval question">
              <div v-for="(question, index) in item.questionCard.questions" :key="index" class="q-block">
                <p>{{ question.question }}</p>
                <label v-for="option in question.options" :key="option.label" class="q-option">
                  <input
                    :type="question.multiSelect ? 'checkbox' : 'radio'"
                    :name="'q-' + item.id + '-' + index"
                    :checked="isPicked(item.id, index, option.label)"
                    @change="togglePick(item.id, index, option.label, question.multiSelect)"
                  />
                  <span>{{ option.label }}</span>
                  <small v-if="option.description">{{ option.description }}</small>
                </label>
                <input class="other" :value="others[item.id + ':' + index] || ''" placeholder="其他" @input="setOther(item.id, index, $event.target.value)" />
              </div>
              <div>
                <button type="button" @click="submitQuestion({ id: item.id, question: item.questionCard })">提交</button>
                <button type="button" class="ghost" @click="chat.decide(item.questionCard.approval_id, false)">跳过</button>
              </div>
            </div>
            <div v-for="file in item.attachments" :key="file.id" class="file-card" data-testid="file-card">
              <span class="file-icon" :data-kind="fileKind(file.filename)">{{ fileMark(file.filename) }}</span>
              <span class="file-meta">
                <strong :title="file.filename">{{ file.filename }}</strong>
                <small>{{ formatSize(file.size) }}</small>
              </span>
              <button type="button" class="file-download" data-testid="file-download" :aria-label="'下载 ' + file.filename" :disabled="savingId === file.id" @click="downloadFile(file)">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10" /><path d="M8 10.5 12 14.5l4-4" /><path d="M5 19h14" /></svg>
              </button>
            </div>
          </div>
        </article>
      </template>
    </section>
    <Composer />
  </main>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { taskPhrase, toolPhrase } from "../activity";
import { formatSize, renderText } from "../api";
import { useChatStore } from "../store";
import { groupThread, toolLine, workDuration } from "../turns";
import Badge from "./Badge.vue";
import Composer from "./Composer.vue";

const chat = useChatStore();
const threadItems = computed(() => groupThread(chat.messages));
const openState = ref({});

function openKey(item, name) {
  return `${item.id}:${name}`;
}

function isOpen(item, name) {
  const key = openKey(item, name);
  if (Object.prototype.hasOwnProperty.call(openState.value, key)) return openState.value[key];
  return name === "work" && Boolean(item.streaming);
}

function onToggle(item, name, event) {
  openState.value = { ...openState.value, [openKey(item, name)]: event.target.open };
}

function liveLabel(item) {
  const running = (item.tools || []).find((tool) => tool.running);
  if (running) return toolPhrase(running);
  const task = taskPhrase(item.question);
  if (task) return task;
  return "正在工作";
}

function showAnswer(item) {
  return Boolean(item.answer || item.reasoning || item.diffs.length || item.attachments.length || item.approval || item.questionCard);
}

function inkOn(color) {
  const raw = String(color || "").replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(raw)) return "#fff";
  const red = parseInt(raw.slice(0, 2), 16);
  const green = parseInt(raw.slice(2, 4), 16);
  const blue = parseInt(raw.slice(4, 6), 16);
  return (red * 299 + green * 587 + blue * 114) / 1000 > 170 ? "#1c1c1e" : "#fff";
}

function bubbleStyle(message) {
  if (message.role !== "user") return null;
  const background = chat.active?.icon_color || "#ff4b93";
  return { background, color: inkOn(background) };
}

function fileKind(filename) {
  const name = String(filename || "").toLowerCase();
  if (name.endsWith(".doc") || name.endsWith(".docx")) return "word";
  if (name.endsWith(".pdf")) return "pdf";
  if (name.endsWith(".xls") || name.endsWith(".xlsx")) return "sheet";
  return "file";
}

function fileMark(filename) {
  const kind = fileKind(filename);
  if (kind === "word") return "W";
  if (kind === "pdf") return "P";
  if (kind === "sheet") return "X";
  return "F";
}

const savingId = ref(null);

async function downloadFile(file) {
  if (!file?.id || savingId.value === file.id) return;
  savingId.value = file.id;
  try {
    const response = await fetch(`/api/attachments/${file.id}`);
    if (!response.ok) throw new Error("下载失败");
    const blob = await response.blob();
    if (window.pywebview && typeof window.showSaveFilePicker === "function") {
      try {
        const handle = await window.showSaveFilePicker({ suggestedName: file.filename || "download" });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        return;
      } catch (error) {
        if (error?.name === "AbortError") return;
      }
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = file.filename || "download";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1500);
  } catch (error) {
    chat.notify(error.message || "下载失败");
  } finally {
    savingId.value = null;
  }
}
const scroller = ref(null);
const picks = ref({});
const others = ref({});

function diffClass(line) {
  if (line.startsWith("+") && !line.startsWith("+++")) return "diff-add";
  if (line.startsWith("-") && !line.startsWith("---")) return "diff-del";
  return "diff-meta";
}

function pickKey(messageId, index) {
  return `${messageId}:${index}`;
}

function isPicked(messageId, index, label) {
  return (picks.value[pickKey(messageId, index)] || []).includes(label);
}

function togglePick(messageId, index, label, multi) {
  const key = pickKey(messageId, index);
  const current = picks.value[key] || [];
  const next = multi
    ? current.includes(label)
      ? current.filter((item) => item !== label)
      : [...current, label]
    : [label];
  picks.value = { ...picks.value, [key]: next };
}

function setOther(messageId, index, value) {
  others.value = { ...others.value, [pickKey(messageId, index)]: value };
}

function submitQuestion(message) {
  const answers = {};
  message.question.questions.forEach((item, index) => {
    const chosen = [...(picks.value[pickKey(message.id, index)] || [])];
    const other = (others.value[pickKey(message.id, index)] || "").trim();
    if (other) chosen.push(other);
    answers[item.question] = item.multiSelect ? chosen : chosen[0] || "";
  });
  chat.answerQuestion(message.question.approval_id, answers);
}

const modelChoices = computed(() => (chat.models.providers || []).filter((item) => item.configured && item.models?.length));
function modelLabel(provider, name) {
  if (provider === "deepseek" && name === "deepseek-chat") return "DeepSeek";
  if (provider === "deepseek" && name === "deepseek-reasoner") return "DeepSeek 思考";
  return name;
}
const modelValue = computed(() => {
  const active = chat.active;
  if (!active) return "";
  const value = `${active.provider}:${active.model}`;
  const known = modelChoices.value.some((item) => item.models.includes(active.model) && item.id === active.provider);
  return known ? value : "";
});

watch(
  () => chat.messages.map((item) => `${item.id}:${item.content}:${item.tools?.length || 0}`).join("|"),
  async () => {
    await nextTick();
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
  },
);

async function changeModel(event) {
  if (!event.target.value) return;
  const [provider, ...rest] = event.target.value.split(":");
  const model = rest.join(":");
  if (!modelChoices.value.some((item) => item.id === provider && item.models.includes(model))) return;
  await chat.patchActive({ provider, model });
}

function copy(text) {
  navigator.clipboard.writeText(text || "");
  chat.notify("已复制");
}

function again(text) {
  chat.draft = text || "";
}

function windowAction(name) {
  const desktop = window.pywebview?.api;
  if (desktop?.[name]) {
    desktop[name]();
    return;
  }
  if (name === "maximize") {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
    return;
  }
  chat.notify(name === "close" ? "在浏览器里直接关闭这个标签页" : "最小化只在桌面窗口里生效");
}
</script>
