<template>
  <div
    v-if="menu"
    class="context-menu narrow"
    :class="menu.kind"
    :data-testid="menu.kind === 'edit' ? 'edit-menu' : 'text-menu'"
    :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
    @mousedown.prevent
    @contextmenu.prevent
  >
    <template v-if="menu.kind === 'edit'">
      <button type="button" data-testid="edit-undo" :disabled="!menu.canUndo" @click="undo">撤销</button>
      <button type="button" data-testid="edit-redo" :disabled="!menu.canRedo" @click="redo">重做</button>
      <div class="sep" />
      <button type="button" data-testid="edit-cut" :disabled="!menu.canEdit" @click="cut">剪切</button>
      <button type="button" data-testid="edit-copy" :disabled="!menu.canEdit" @click="copyField">复制</button>
      <button type="button" data-testid="edit-paste" :disabled="menu.locked" @click="paste">粘贴</button>
      <div class="sep" />
      <button type="button" data-testid="edit-all" :disabled="!menu.canSelectAll" @click="selectAll">全选</button>
    </template>
    <template v-else>
      <button type="button" data-testid="text-select" :disabled="!menu.canSelect" @click="selectBlock">选择</button>
      <button type="button" data-testid="text-copy" :disabled="!menu.copyText" @click="copyBlock">复制</button>
    </template>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useChatStore } from "../store";

const chat = useChatStore();
const menu = ref(null);
const histories = new WeakMap();
let fieldEl = null;
let blockEl = null;
let rangeStart = 0;
let rangeEnd = 0;

function historyFor(el) {
  let state = histories.get(el);
  if (!state) {
    state = { stack: [], index: -1, mute: false };
    histories.set(el, state);
  }
  return state;
}

function remember(el) {
  const state = historyFor(el);
  if (state.mute || state.index !== -1) return;
  state.stack = [{ value: el.value || "", start: el.selectionStart ?? 0, end: el.selectionEnd ?? 0 }];
  state.index = 0;
}

function onFocusIn(event) {
  const el = editable(event.target);
  if (el) remember(el);
}

function onBeforeInput(event) {
  const el = editable(event.target);
  if (el) remember(el);
}

function onInput(event) {
  const el = editable(event.target);
  if (!el) return;
  const state = historyFor(el);
  if (state.mute) return;
  if (state.index === -1) remember(el);
  const snap = {
    value: el.value || "",
    start: el.selectionStart ?? el.value.length,
    end: el.selectionEnd ?? el.value.length,
  };
  if (state.stack[state.index]?.value === snap.value) {
    state.stack[state.index] = snap;
    return;
  }
  state.stack = state.stack.slice(0, state.index + 1);
  state.stack.push(snap);
  if (state.stack.length > 80) state.stack.shift();
  state.index = state.stack.length - 1;
}

function applyHistory(el, state) {
  const snap = state.stack[state.index];
  state.mute = true;
  el.focus();
  el.value = snap.value;
  el.setSelectionRange(snap.start, snap.end);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  state.mute = false;
}

function undoOf(el) {
  const state = histories.get(el);
  if (!state || state.index <= 0) return;
  state.index -= 1;
  applyHistory(el, state);
}

function redoOf(el) {
  const state = histories.get(el);
  if (!state || state.index >= state.stack.length - 1) return;
  state.index += 1;
  applyHistory(el, state);
}

const TEXT_TYPES = new Set(["text", "search", "url", "email", "password", "tel", ""]);

function editable(target) {
  const el = target?.closest?.("textarea, input");
  if (!el || el.disabled) return null;
  if (el.tagName === "INPUT" && !TEXT_TYPES.has((el.getAttribute("type") || "text").toLowerCase())) return null;
  return el;
}

function textBlock(target) {
  const direct = target?.closest?.(".text, pre, .diff-view, .reasoning, .empty, .plan-preview");
  if (direct) return direct;
  const host = target?.closest?.(".bubble, .row, .empty");
  return host?.querySelector?.(".text, pre, .plan-preview") || null;
}

function place(event, width, height) {
  return {
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - width - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - height - 8)),
  };
}

function openEdit(event, el) {
  fieldEl = el;
  blockEl = null;
  el.focus();
  rangeStart = el.selectionStart ?? 0;
  rangeEnd = el.selectionEnd ?? rangeStart;
  const locked = Boolean(el.readOnly);
  const state = histories.get(el);
  menu.value = {
    kind: "edit",
    ...place(event, 168, 280),
    locked,
    canUndo: !locked && Boolean(state && state.index > 0),
    canRedo: !locked && Boolean(state && state.index < state.stack.length - 1),
    canEdit: !locked && rangeStart !== rangeEnd,
    canSelectAll: (el.value || "").length > 0,
  };
}

function openText(event, block) {
  fieldEl = null;
  blockEl = block;
  const selected = window.getSelection?.()?.toString() || "";
  const blockText = (block.innerText || "").replace(/\u00a0/g, " ").trim();
  menu.value = {
    kind: "text",
    ...place(event, 168, 96),
    canSelect: Boolean(blockText),
    copyText: selected || blockText,
  };
}

function close() {
  menu.value = null;
  fieldEl = null;
  blockEl = null;
}

function restoreField() {
  const el = fieldEl;
  if (!el) return null;
  el.focus();
  if (typeof el.setSelectionRange === "function") el.setSelectionRange(rangeStart, rangeEnd);
  return el;
}

function undo() {
  const el = fieldEl;
  close();
  if (el) undoOf(el);
}

function redo() {
  const el = fieldEl;
  close();
  if (el) redoOf(el);
}

function replaceSelection(el, text) {
  el.setRangeText(text, el.selectionStart, el.selectionEnd, "end");
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function copyField() {
  const el = restoreField();
  const text = el ? (el.value || "").slice(el.selectionStart, el.selectionEnd) : "";
  if (el && text && document.execCommand("copy")) {
    close();
    return;
  }
  close();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    chat.notify("复制没有成功");
  }
}

async function cut() {
  const el = restoreField();
  const text = el ? (el.value || "").slice(el.selectionStart, el.selectionEnd) : "";
  const start = el ? el.selectionStart : 0;
  const end = el ? el.selectionEnd : 0;
  if (!el || !text) {
    close();
    return;
  }
  if (document.execCommand("cut")) {
    close();
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    close();
    chat.notify("剪切没有成功");
    return;
  }
  el.focus();
  el.setSelectionRange(start, end);
  replaceSelection(el, "");
  close();
}

async function paste() {
  const el = restoreField();
  if (!el) {
    close();
    return;
  }
  if (document.execCommand("paste")) {
    close();
    return;
  }
  let text = "";
  try {
    text = await navigator.clipboard.readText();
  } catch {
    close();
    chat.notify("无法读取剪贴板");
    return;
  }
  restoreField();
  replaceSelection(el, text);
  close();
}

function selectAll() {
  const el = fieldEl;
  close();
  if (!el) return;
  el.focus();
  el.select?.();
}

function selectBlock() {
  const block = blockEl;
  close();
  if (!block) return;
  const range = document.createRange();
  range.selectNodeContents(block);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

async function copyBlock() {
  const text = menu.value?.copyText || "";
  close();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    chat.notify("已复制");
  } catch {
    chat.notify("复制没有成功");
  }
}

function onContext(event) {
  if (event.target?.closest?.(".context-menu")) return;
  const field = editable(event.target);
  if (field) {
    event.preventDefault();
    event.stopPropagation();
    openEdit(event, field);
    return;
  }
  if (event.target?.closest?.("button, a, select, label, .sidebar")) {
    close();
    return;
  }
  const block = textBlock(event.target);
  if (!block) {
    close();
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  openText(event, block);
}

function onPointerDown(event) {
  if (!menu.value) return;
  if (event.target?.closest?.(".context-menu")) return;
  close();
}

function onKey(event) {
  if (menu.value && event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    close();
    return;
  }
  const el = editable(event.target);
  if (!el || el.readOnly || el.disabled) return;
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
  const key = event.key.toLowerCase();
  if (key === "z") {
    event.preventDefault();
    if (event.shiftKey) redoOf(el);
    else undoOf(el);
  } else if (key === "y") {
    event.preventDefault();
    redoOf(el);
  }
}

onMounted(() => {
  window.addEventListener("contextmenu", onContext, true);
  window.addEventListener("pointerdown", onPointerDown, true);
  window.addEventListener("keydown", onKey, true);
  window.addEventListener("focusin", onFocusIn, true);
  window.addEventListener("beforeinput", onBeforeInput, true);
  window.addEventListener("input", onInput, true);
});

onBeforeUnmount(() => {
  window.removeEventListener("contextmenu", onContext, true);
  window.removeEventListener("pointerdown", onPointerDown, true);
  window.removeEventListener("keydown", onKey, true);
  window.removeEventListener("focusin", onFocusIn, true);
  window.removeEventListener("beforeinput", onBeforeInput, true);
  window.removeEventListener("input", onInput, true);
});
</script>
