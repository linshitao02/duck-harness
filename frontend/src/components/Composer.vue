<template>
  <div class="composer-wrap">
    <div v-if="chat.pending.length" class="pending">
      <span v-for="file in chat.pending" :key="file.id">{{ file.filename }}</span>
    </div>
    <div v-if="suggestions.length" class="mention-menu" data-testid="mention-menu" role="listbox">
      <button
        v-for="(member, index) in suggestions"
        :key="member.id"
        type="button"
        role="option"
        :class="{ active: index === activeIndex }"
        :data-testid="'mention-option-' + member.id"
        @mousedown.prevent="pick(member)"
      >
        <span class="badge" :style="badgeStyle(member.icon_key, member.icon_color)">
          <Badge :name="member.icon_key" />
        </span>
        <span>{{ member.member_name || member.title }}</span>
      </button>
    </div>
    <form class="composer" @submit.prevent="chat.send()">
      <button class="plus" type="button" aria-label="更多" data-testid="plus" @click="chat.menuOpen = !chat.menuOpen">+</button>
      <div v-if="chat.menuOpen" class="plus-menu">
        <button type="button" @click="pickFile">上传文件</button>
        <button type="button" @click="setMode('auto')">权限：自动</button>
        <button type="button" @click="setMode('ask')">权限：询问</button>
        <button type="button" @click="setMode('plan')">权限：只读计划</button>
        <button type="button" @click="setMode('edits')">权限：改文件免问</button>
        <button type="button" @click="setMode('dontAsk')">权限：不询问</button>
        <button type="button" @click="doRewind">撤销最近改动</button>
        <button type="button" @click="doCompact">压缩上下文</button>
      </div>
      <textarea
        ref="box"
        v-model="chat.draft"
        data-testid="composer"
        rows="1"
        :placeholder="chat.active?.kind === 'group' ? '用 @成员名 说明目标，例如 @项目经理' : ('给 ' + (chat.active?.title || '新会话') + ' 发消息')"
        :spellcheck="chat.settings.spellcheck !== '0'"
        @input="onInput"
        @click="syncMention"
        @keyup="syncMention"
        @keydown="onKeydown"
      />
      <button class="mic" type="button" aria-label="语音输入" @click="listen">
        <svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M6 11a6 6 0 0 0 12 0M12 17v4" /></svg>
      </button>
      <button
        class="send"
        :class="{ stop: chat.streaming }"
        type="button"
        data-testid="send"
        :aria-label="chat.streaming ? '停止' : '发送'"
        :style="sendStyle"
        @click="chat.streaming ? chat.stop() : chat.send()"
      >
        <svg v-if="chat.streaming" class="pause" viewBox="0 0 24 24"><rect x="7" y="5" width="3.2" height="14" rx="1.2" /><rect x="13.8" y="5" width="3.2" height="14" rx="1.2" /></svg>
        <svg v-else-if="chat.draft.trim() || chat.pending.length" viewBox="0 0 24 24"><path d="M12 18V6M7 11l5-5 5 5" /></svg>
        <svg v-else viewBox="0 0 24 24"><path d="M5 15c2-1 3-4 3-6s1-5 4-5 4 3 4 5 1 5 3 6" /></svg>
      </button>
      <input ref="file" type="file" hidden @change="onFile" />
    </form>
    <p v-if="modeLabel" class="mode-label">{{ modeLabel }}</p>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { badgeStyle } from "../shapes";
import { useChatStore } from "../store";
import Badge from "./Badge.vue";

const chat = useChatStore();
const box = ref(null);
const mention = ref(null);
const activeIndex = ref(0);

const roomMembers = computed(() => {
  const active = chat.active;
  if (!active) return [];
  const roomId = active.kind === "group" ? active.id : active.room_id;
  if (!roomId) return [];
  return chat.conversations.filter((item) => item.room_id === roomId);
});

const suggestions = computed(() => {
  if (!mention.value || !roomMembers.value.length) return [];
  const query = mention.value.text.toLowerCase();
  return roomMembers.value.filter((item) => {
    const name = (item.member_name || item.title || "").toLowerCase();
    return !query || name.includes(query);
  });
});

function inkOn(color) {
  const raw = String(color || "").replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(raw)) return "#fff";
  const red = parseInt(raw.slice(0, 2), 16);
  const green = parseInt(raw.slice(2, 4), 16);
  const blue = parseInt(raw.slice(4, 6), 16);
  return (red * 299 + green * 587 + blue * 114) / 1000 > 170 ? "#1c1c1e" : "#fff";
}

const sendStyle = computed(() => {
  if (chat.streaming) return null;
  const background = chat.active?.icon_color || "#ff4b93";
  return { background, color: inkOn(background) };
});
const file = ref(null);
const modeLabel = computed(() => {
  const mode = chat.active?.permission_mode || chat.settings.permission_mode;
  if (mode === "plan") return "计划模式，只会查看，方案写在计划文件里";
  if (mode === "ask") return "询问模式，修改前会先征求同意";
  if (mode === "edits") return "改文件直接执行，命令仍会询问";
  if (mode === "dontAsk") return "不询问，需要确认的操作会直接拒绝";
  return "";
});

function onKeydown(event) {
  if (event.isComposing) return;
  if (suggestions.value.length && event.key === "ArrowDown") {
    event.preventDefault();
    activeIndex.value = (activeIndex.value + 1) % suggestions.value.length;
    return;
  }
  if (suggestions.value.length && event.key === "ArrowUp") {
    event.preventDefault();
    activeIndex.value = (activeIndex.value - 1 + suggestions.value.length) % suggestions.value.length;
    return;
  }
  if (suggestions.value.length && event.key === "Escape") {
    event.preventDefault();
    mention.value = null;
    return;
  }
  if (event.key !== "Enter") return;
  if (suggestions.value.length) {
    event.preventDefault();
    pick(suggestions.value[Math.min(activeIndex.value, suggestions.value.length - 1)]);
    return;
  }
  const shortcut = chat.settings.send_shortcut || "enter";
  if (shortcut === "ctrl_enter") {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      chat.send();
    }
    return;
  }
  if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
  event.preventDefault();
  chat.send();
}

function onInput(event) {
  grow(event);
  syncMention();
}

function syncMention() {
  const el = box.value;
  if (!el || !roomMembers.value.length) {
    mention.value = null;
    return;
  }
  const pos = el.selectionStart ?? chat.draft.length;
  const before = chat.draft.slice(0, pos);
  const found = before.match(/(^|[\s\n])[@＠]([^\s@＠]*)$/);
  if (!found) {
    mention.value = null;
    return;
  }
  const text = found[2];
  if (!mention.value || mention.value.text !== text) activeIndex.value = 0;
  mention.value = { start: pos - text.length - 1, text };
}

function pick(member) {
  const name = member.member_name || member.title || "";
  const el = box.value;
  const pos = el?.selectionStart ?? chat.draft.length;
  const start = mention.value?.start ?? Math.max(0, pos - 1);
  chat.draft = `${chat.draft.slice(0, start)}@${name} ${chat.draft.slice(pos)}`;
  mention.value = null;
  const caret = start + name.length + 2;
  requestAnimationFrame(() => {
    el?.focus();
    el?.setSelectionRange(caret, caret);
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 140) + "px";
    }
  });
}

function grow(event) {
  const el = event.target;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

function pickFile() {
  chat.menuOpen = false;
  file.value?.click();
}

async function onFile(event) {
  const picked = event.target.files?.[0];
  event.target.value = "";
  if (!picked) return;
  try {
    await chat.upload(picked);
  } catch (error) {
    chat.notify(error.message || "上传失败");
  }
}

async function setMode(mode) {
  chat.menuOpen = false;
  await chat.patchActive({ permission_mode: mode });
  await chat.saveSettings({ permission_mode: mode });
}

async function doCompact() {
  chat.menuOpen = false;
  try {
    await chat.compact();
  } catch (error) {
    chat.notify(error.message || "压缩失败");
  }
}

async function doRewind() {
  chat.menuOpen = false;
  await chat.rewind();
}

function listen() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    chat.notify("当前环境没有语音识别");
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "zh-CN";
  recognition.onresult = (event) => {
    chat.draft = `${chat.draft} ${event.results[0][0].transcript}`.trim();
  };
  recognition.start();
}
</script>
