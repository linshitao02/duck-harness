<template>
  <div class="bot-mask" @click.self="chat.botOpen = false">
    <section class="bot-dialog" data-testid="new-bot" role="dialog" aria-labelledby="new-bot-title">
      <header>
        <h2 id="new-bot-title">{{ mode === "group" ? "创建群聊" : "创建新 Agent" }}</h2>
        <button type="button" aria-label="关闭" @click="chat.botOpen = false">×</button>
      </header>
      <div class="mode-switch">
        <button type="button" data-testid="mode-chat" :class="{ selected: mode === 'chat' }" @click="mode = 'chat'">会话</button>
        <button type="button" data-testid="mode-group" :class="{ selected: mode === 'group' }" @click="mode = 'group'">群聊</button>
      </div>
      <template v-if="mode === 'group'">
        <input v-model="groupTitle" data-testid="group-title" placeholder="群名称，不填就用成员名字" @keydown.enter="create" />
        <div v-if="picked.length" class="picked-list">
          <div v-for="(item, index) in picked" :key="item.id" class="picked-row">
            <span class="badge" :style="badgeStyle(item.icon_key, item.icon_color)"><Badge :name="item.icon_key" /></span>
            <span class="who">
              <strong>{{ item.title }}</strong>
              <small v-if="index === 0">先发言</small>
            </span>
            <button type="button" aria-label="移出群聊" @click="removeMember(item.id)">×</button>
          </div>
        </div>
        <p class="picker-label">选择已有会话</p>
        <div class="agent-picker" data-testid="agent-picker">
          <p v-if="!available.length && !busy.length" class="picker-empty">还没有可选会话。先在「会话」里建好 Agent，再拉进群。</p>
          <button
            v-for="item in available"
            :key="item.id"
            type="button"
            class="agent-option"
            :class="{ added: selected.includes(item.id) }"
            :data-testid="'add-agent-' + item.id"
            @click="addMember(item.id)"
          >
            <span class="badge" :style="badgeStyle(item.icon_key, item.icon_color)"><Badge :name="item.icon_key" /></span>
            <span class="who">{{ item.title }}</span>
            <span class="add-label">{{ selected.includes(item.id) ? "已添加" : "添加到群聊" }}</span>
          </button>
          <button v-for="item in busy" :key="item.id" type="button" class="agent-option" disabled>
            <span class="badge" :style="badgeStyle(item.icon_key, item.icon_color)"><Badge :name="item.icon_key" /></span>
            <span class="who">{{ item.title }}</span>
            <span class="add-label">已在群里</span>
          </button>
        </div>
      </template>
      <div v-else class="bot-preview" :style="{ color }">
        <DuckIcon :name="shapeId" live />
      </div>
      <input v-if="mode === 'chat'" v-model="name" data-testid="bot-name" placeholder="给你的 Agent 起个名字" @keydown.enter="create" />
      <div v-if="mode === 'chat'" class="shape-grid">
        <button
          v-for="item in DUCKS"
          :key="item.id"
          type="button"
          class="shape-btn"
          :class="{ selected: item.id === shapeId }"
          :style="{ color }"
          :aria-label="item.label"
          @click="shapeId = item.id"
        >
          <DuckIcon :name="item.id" />
        </button>
      </div>
      <div v-if="mode === 'chat'" class="color-grid">
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
        <button type="button" class="create-btn" :data-testid="mode === 'group' ? 'create-group' : 'create-bot'" :disabled="!canCreate" @click="create">
          创建
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import Badge from "./Badge.vue";
import DuckIcon from "./DuckIcon.vue";
import { BOT_COLORS, DUCKS, badgeStyle } from "../shapes";
import { useChatStore } from "../store";

const chat = useChatStore();
const mode = ref("chat");
const name = ref("");
const groupTitle = ref("");
const selected = ref([]);
const shapeId = ref(DUCKS[0].id);
const color = ref(BOT_COLORS[0]);
const standalone = computed(() => chat.conversations.filter((item) => (item.kind || "chat") === "chat" && !item.hidden));
const available = computed(() => standalone.value.filter((item) => !item.room_id));
const busy = computed(() => standalone.value.filter((item) => item.room_id));
const picked = computed(() => selected.value.map((id) => chat.conversations.find((item) => item.id === id)).filter(Boolean));
const canCreate = computed(() => {
  if (mode.value === "group") return picked.value.length >= 2;
  return Boolean(name.value.trim());
});

function addMember(id) {
  if (selected.value.includes(id)) return;
  selected.value = [...selected.value, id];
}

function removeMember(id) {
  selected.value = selected.value.filter((item) => item !== id);
}

function create() {
  if (!canCreate.value) return;
  if (mode.value === "group") {
    chat.createGroup({ title: groupTitle.value.trim(), conversation_ids: picked.value.map((item) => item.id) });
    return;
  }
  const title = name.value.trim();
  chat.botOpen = false;
  chat.createConversation({ title, icon_key: shapeId.value, icon_color: color.value });
}

function onKey(event) {
  if (event.key === "Escape") chat.botOpen = false;
}

onMounted(() => {
  color.value = BOT_COLORS[Math.floor(Math.random() * BOT_COLORS.length)];
  window.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
