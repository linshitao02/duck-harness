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
        <button
          type="button"
          class="title-id"
          :class="{ 'can-open': roomMembers.length }"
          data-testid="open-members"
          :aria-expanded="membersOpen"
          :aria-label="roomMembers.length ? (membersOpen ? '收起群成员' : '查看群成员') : (chat.active?.title || '新会话')"
          @mousedown.stop
          @click="toggleMembers"
        >
          <span class="logo" aria-hidden="true" :style="badgeStyle(chat.active?.icon_key, chat.active?.icon_color)">
            <Badge :name="chat.active?.icon_key || 'spark'" />
          </span>
          <span class="title" data-testid="chat-title">{{ chat.active?.title || "新会话" }}</span>
        </button>
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
      <div v-if="!threadItems.length && !(chat.privateThreads || []).length && !chat.intakeBusy" class="empty">
        <h1>{{ chat.active?.title || "新会话" }}</h1>
        <p v-if="chat.active?.kind === 'group'">用 @成员名 把目标发给他。项目经理会拆开任务，再派给其他成员。</p>
        <p v-else>直接说你要做的事。我可以改代码、跑本机命令、联网搜索，也会记住你的偏好。</p>
      </div>
      <template v-for="item in threadItems" :key="item.id">
        <div v-if="item.kind === 'divider'" class="new-divider"><span>新</span></div>
        <article v-else-if="item.kind === 'user'" class="row user" data-testid="message">
          <div class="hover-actions">
            <button type="button" aria-label="复制" @click="copy(item.message.content)">…</button>
            <button type="button" aria-label="再发一次" @click="again(item.message.content)">↩</button>
          </div>
          <div class="bubble user" :style="bubbleStyle(item.message)">
            <MentionText v-if="item.message.content" :value="item.message.content" />
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
          <p v-if="item.speaker" class="speaker-line" data-testid="speaker-name">
            <span v-if="person(item.speaker)" class="badge" :style="badgeStyle(person(item.speaker).icon_key, person(item.speaker).icon_color)">
              <Badge :name="person(item.speaker).icon_key" />
            </span>
            <span>{{ item.speaker }}</span>
          </p>
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
            <MentionText v-if="item.answer" :value="spoken(item)" />
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
          <div v-if="item.intake" class="intake-card" data-testid="intake-card">
            <p>{{ item.intake.question }}</p>
            <p v-if="item.intake.choice" class="intake-picked">
              <span class="intake-letter" :style="{ background: chat.active?.icon_color || '#1c1c1e', color: '#fff' }">{{ choiceLetter(item.intake) }}</span>
              <span>{{ item.intake.choice }}</span>
            </p>
            <template v-else>
              <button
                v-for="(option, index) in item.intake.options"
                :key="option"
                type="button"
                class="intake-option"
                :data-testid="'intake-option-' + index"
                :disabled="chat.intakeBusy"
                @click="pickIntake(item.intake, option)"
              >
                <span class="intake-letter">{{ intakeLetter(index) }}</span>
                <span>{{ option }}</span>
              </button>
              <input
                v-if="otherFor === item.intake.message_id"
                v-model="otherText"
                class="intake-other"
                data-testid="intake-other"
                placeholder="补充一句，回车发送"
                @keydown.enter.prevent="sendOther(item.intake)"
              />
            </template>
          </div>
        </article>
        <template v-if="item.id === firstIntakeId">
          <div v-if="(chat.privateThreads || []).length" class="private-folds">
            <p v-for="bundle in chat.privateThreads" :key="bundle.counterpart" class="private-fold">
              <span>{{ bundle.count }}条消息往来：</span>
              <button type="button" data-testid="private-toggle" @click="openPrivate = openPrivate === bundle.counterpart ? '' : bundle.counterpart">
                <span v-if="person(bundle.counterpart)" class="badge" :style="badgeStyle(person(bundle.counterpart).icon_key, person(bundle.counterpart).icon_color)">
                  <Badge :name="person(bundle.counterpart).icon_key" />
                </span>
                <span>{{ bundle.counterpart }}</span>
              </button>
            </p>
          </div>
          <div v-if="openBundle" class="private-panel" data-testid="private-thread">
            <p class="private-head">{{ chat.active?.member_name || chat.active?.title }} → {{ openBundle.counterpart }}</p>
            <article v-for="note in openBundle.messages" :key="note.id" class="row" :class="note.speaker === chat.active?.member_name ? 'user' : 'assistant'">
              <p class="speaker-line">{{ note.speaker }}</p>
              <div class="bubble" :class="note.speaker === chat.active?.member_name ? 'user' : 'assistant'">
                <MentionText :value="note.content" />
              </div>
            </article>
            <button type="button" class="close-private" data-testid="close-private" @click="openPrivate = ''">关闭聊天</button>
          </div>
        </template>
      </template>
      <p v-if="chat.intakeBusy" class="intake-thinking" data-testid="intake-thinking">
        <i></i><i></i><i></i>
        <span>{{ thinkingLine }}</span>
      </p>
      <template v-if="!firstIntakeId">
        <div v-if="(chat.privateThreads || []).length" class="private-folds">
          <p v-for="bundle in chat.privateThreads" :key="bundle.counterpart" class="private-fold">
            <span>{{ bundle.count }}条消息往来：</span>
            <button type="button" data-testid="private-toggle" @click="openPrivate = openPrivate === bundle.counterpart ? '' : bundle.counterpart">
              <span v-if="person(bundle.counterpart)" class="badge" :style="badgeStyle(person(bundle.counterpart).icon_key, person(bundle.counterpart).icon_color)">
                <Badge :name="person(bundle.counterpart).icon_key" />
              </span>
              <span>{{ bundle.counterpart }}</span>
            </button>
          </p>
        </div>
        <div v-if="openBundle" class="private-panel" data-testid="private-thread">
          <p class="private-head">{{ chat.active?.member_name || chat.active?.title }} → {{ openBundle.counterpart }}</p>
          <article v-for="note in openBundle.messages" :key="note.id" class="row" :class="note.speaker === chat.active?.member_name ? 'user' : 'assistant'">
            <p class="speaker-line">{{ note.speaker }}</p>
            <div class="bubble" :class="note.speaker === chat.active?.member_name ? 'user' : 'assistant'">
              <MentionText :value="note.content" />
            </div>
          </article>
          <button type="button" class="close-private" data-testid="close-private" @click="openPrivate = ''">关闭聊天</button>
        </div>
      </template>
    </section>
    <Composer />
    <aside v-if="membersOpen && roomMembers.length" class="member-rail" data-testid="member-rail">
      <header>
        <h2>群成员</h2>
        <button type="button" @click="membersOpen = false">关闭</button>
      </header>
      <div class="member-rail-list">
        <button
          v-for="member in roomMembers"
          :key="member.id"
          type="button"
          class="member-card"
          :class="{ active: member.id === chat.activeId }"
          :data-testid="'rail-member-' + member.id"
          @click="chat.select(member.id)"
        >
          <span class="badge" :style="badgeStyle(member.icon_key, member.icon_color)">
            <Badge :name="member.icon_key" />
          </span>
          <span class="member-copy">
            <strong>{{ member.member_name || member.title }}</strong>
            <small>{{ member.preview || "群成员" }}</small>
          </span>
        </button>
      </div>
    </aside>
  </main>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { taskPhrase, toolPhrase } from "../activity";
import { formatSize } from "../api";
import { useChatStore } from "../store";
import { groupThread, toolLine, workDuration } from "../turns";
import { badgeStyle } from "../shapes";
import Badge from "./Badge.vue";
import Composer from "./Composer.vue";
import MentionText from "./MentionText.vue";

const chat = useChatStore();
const membersOpen = ref(false);
const threadItems = computed(() => groupThread(chat.messages));
const firstIntakeId = computed(() => threadItems.value.find((item) => item.intake)?.id || null);
const roomId = computed(() => {
  const active = chat.active;
  if (!active) return null;
  return active.kind === "group" ? active.id : active.room_id || null;
});
const roomMembers = computed(() => chat.membersOf(roomId.value));

function roomPeople() {
  const active = chat.active;
  if (!active) return [];
  const id = active.kind === "group" ? active.id : active.room_id;
  return chat.membersOf(id);
}

function person(name) {
  if (!name || name === "user" || name === "用户") return null;
  return roomPeople().find((item) => item.member_name === name || item.title === name) || null;
}

function spoken(item) {
  const text = item.answer || "";
  const who = person(item.handoffTarget);
  if (!who) return text;
  const name = who.member_name || who.title || "";
  if (!name || text.includes(`@${name}`) || text.includes(`＠${name}`)) return text;
  return inlineMention(text, name);
}

function inlineMention(text, name) {
  let index = 0;
  while (index < text.length) {
    if (text.startsWith("```", index)) {
      const end = text.indexOf("```", index + 3);
      index = end < 0 ? text.length : end + 3;
      continue;
    }
    if (text[index] === "`") {
      const end = text.indexOf("`", index + 1);
      index = end < 0 ? index + 1 : end + 1;
      continue;
    }
    if (text.startsWith(name, index)) return text.slice(0, index) + "@" + text.slice(index);
    index += 1;
  }
  return text;
}

function toggleMembers() {
  if (!roomMembers.value.length) return;
  membersOpen.value = !membersOpen.value;
}

watch(roomId, () => {
  membersOpen.value = false;
});
const otherFor = ref(null);
const otherText = ref("");
const thinkingLine = computed(() => {
  const lines = ["正在整理思路", "正在琢磨", "正在想怎么开口"];
  const title = chat.active?.title || "";
  let sum = 0;
  for (const ch of title) sum += ch.charCodeAt(0);
  return lines[sum % lines.length];
});

function intakeLetter(index) {
  return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[index] || String(index + 1);
}

function choiceLetter(card) {
  const options = card.options || [];
  const index = options.indexOf(card.choice);
  if (index >= 0) return intakeLetter(index);
  const other = options.indexOf("其他");
  return intakeLetter(other >= 0 ? other : 0);
}

function pickIntake(card, option) {
  if (chat.intakeBusy || card.choice) return;
  if (option === "其他") {
    otherFor.value = card.message_id;
    otherText.value = "";
    return;
  }
  chat.answerIntake(card.message_id, option);
}

function sendOther(card) {
  const text = otherText.value.trim();
  otherFor.value = null;
  chat.answerIntake(card.message_id, text || "其他");
}
const openState = ref({});
const openPrivate = ref("");
const openBundle = computed(() => (chat.privateThreads || []).find((item) => item.counterpart === openPrivate.value) || null);
watch(() => chat.activeId, () => {
  openPrivate.value = "";
  otherFor.value = null;
});

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
