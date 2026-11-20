<template>
  <div class="bot-mask" @click.self="chat.botOpen = false">
    <section class="bot-dialog" data-testid="new-bot" role="dialog" aria-labelledby="new-bot-title">
      <header>
        <h2 id="new-bot-title">创建新 Agent</h2>
        <button type="button" aria-label="关闭" @click="chat.botOpen = false">×</button>
      </header>
      <div class="bot-preview" :style="{ color }">
        <svg viewBox="0 0 64 64">
          <path :d="current.d" />
          <circle cx="26" cy="34" r="3.2" fill="#fff" />
          <circle cx="38" cy="34" r="3.2" fill="#fff" />
        </svg>
      </div>
      <input v-model="name" data-testid="bot-name" placeholder="给你的 Agent 起个名字" @keydown.enter="create" />
      <div class="shape-grid">
        <button
          v-for="item in BOT_SHAPES"
          :key="item.id"
          type="button"
          class="shape-btn"
          :class="{ selected: item.id === shapeId }"
          :style="{ color }"
          :aria-label="item.id"
          @click="shapeId = item.id"
        >
          <svg viewBox="0 0 64 64"><path :d="item.d" /></svg>
        </button>
      </div>
      <div class="color-grid">
        <button
          v-for="item in BOT_COLORS"
          :key="item"
          type="button"
          class="color-btn"
          :class="{ selected: item === color }"
          :style="{ background: item }"
          :aria-label="item"
          @click="color = item"
        />
      </div>
      <footer>
        <button type="button" class="text-btn" @click="chat.botOpen = false">取消</button>
        <button type="button" class="create-btn" data-testid="create-bot" :disabled="!name.trim()" @click="create">
          创建
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { BOT_COLORS, BOT_SHAPES } from "../shapes";
import { useChatStore } from "../store";

const chat = useChatStore();
const name = ref("");
const shapeId = ref("heart");
const color = ref("#f5c542");
const current = computed(() => BOT_SHAPES.find((item) => item.id === shapeId.value) || BOT_SHAPES[0]);

function create() {
  const title = name.value.trim();
  if (!title) return;
  chat.botOpen = false;
  chat.createConversation({ title, icon_key: shapeId.value, icon_color: color.value });
}

function onKey(event) {
  if (event.key === "Escape") chat.botOpen = false;
}

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
