<template>
  <div class="search-panel" data-testid="search-panel" @click.stop>
    <label class="search-field">
      <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6" /><path d="M16 16l4 4" /></svg>
      <input
        ref="input"
        v-model="query"
        placeholder="搜索 Bot、聊天、设置..."
        data-testid="search"
        @keydown.esc="chat.searchOpen = false"
      />
    </label>
    <p class="search-label">Bot</p>
    <div class="search-list">
      <button
        v-for="item in results"
        :key="item.id"
        type="button"
        class="search-hit"
        @click="open(item)"
      >
        <span class="badge" :style="badgeStyle(item.icon_key, item.icon_color)">
          <Badge :name="item.icon_key" />
        </span>
        <span class="conv-copy">
          <span class="conv-title">{{ item.title }}</span>
          <span class="conv-preview">{{ item.preview || "新的对话" }}</span>
        </span>
      </button>
      <p v-if="!results.length" class="muted search-empty">没有匹配的 Bot</p>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useChatStore } from "../store";
import { badgeStyle } from "../shapes";
import Badge from "./Badge.vue";

const chat = useChatStore();
const query = ref("");
const input = ref(null);
const results = computed(() => {
  const text = query.value.trim().toLowerCase();
  if (!text) return chat.conversations;
  return chat.conversations.filter((item) => `${item.title} ${item.preview}`.toLowerCase().includes(text));
});

function open(item) {
  chat.searchOpen = false;
  chat.select(item.id);
}

function onPointerDown(event) {
  if (event.target.closest?.(".search-panel, [data-testid=open-search]")) return;
  chat.searchOpen = false;
}

onMounted(() => {
  input.value?.focus();
  window.addEventListener("pointerdown", onPointerDown);
});
onBeforeUnmount(() => window.removeEventListener("pointerdown", onPointerDown));
</script>
