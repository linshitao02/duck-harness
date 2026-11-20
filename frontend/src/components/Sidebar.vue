<template>
  <aside class="sidebar">
    <div class="side-tools pywebview-drag-region">
      <span class="brand-mark" aria-hidden="true">
        <img src="/duck.png" alt="" />
      </span>
      <span class="product-title">Duck Harness</span>
      <div class="side-actions">
      <button class="icon-btn" type="button" aria-label="搜索会话" data-testid="open-search" @mousedown.stop @click.stop="chat.searchOpen = !chat.searchOpen">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6" /><path d="M16 16l4 4" /></svg>
      </button>
      <button class="icon-btn" type="button" aria-label="新会话" data-testid="new-chat" @mousedown.stop @click.stop="chat.botOpen = true">
        <svg viewBox="0 0 24 24"><path d="M12 6v12M6 12h12" /></svg>
      </button>
      </div>
    </div>
    <div class="conv-list">
      <template v-for="section in sections" :key="section.name || 'loose'">
        <p v-if="section.name" class="group-label">{{ section.name }}</p>
        <button
          v-for="item in section.items"
          :key="item.id"
          class="conv"
          :class="{ active: item.id === chat.activeId, unread: item.unread }"
          :data-testid="'conv-' + item.id"
          @click="chat.select(item.id)"
          @contextmenu.prevent.stop="openMenu($event, item)"
        >
          <span class="badge" :style="{ background: item.icon_color }">
            <Badge :name="item.icon_key" />
            <i v-if="item.unread" class="unread-dot" />
          </span>
          <span class="conv-copy">
            <span class="conv-title">
              <input
                v-if="editing === item.id"
                ref="renameInput"
                :value="item.title"
                class="rename"
                @click.stop
                @keydown.enter="commitRename(item, $event)"
                @keydown.esc="editing = null"
                @blur="commitRename(item, $event)"
              />
              <span v-else>{{ item.title }}</span>
            </span>
            <span class="conv-preview">{{ item.preview || "新的对话" }}</span>
          </span>
        </button>
      </template>
    </div>
    <div class="side-bottom">
      <button class="settings-btn" type="button" aria-label="设置" data-testid="open-settings" @click="chat.settingsOpen = true">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" /><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18" /></svg>
      </button>
      <button class="connect" type="button" data-testid="connect" @click="chat.openConnect()">
        <span>连接应用</span>
        <span class="mini-apps" aria-hidden="true">
          <i class="dot terminal" />
          <i class="dot globe" />
          <i class="dot spark" />
        </span>
      </button>
    </div>
    <div
      v-if="menu"
      class="context-menu"
      data-testid="conv-menu"
      :style="{ left: menu.x + 'px', top: menu.y + 'px' }"
      @click.stop
      @contextmenu.prevent
    >
      <template v-if="!menu.grouping">
        <button type="button" @click="run('pin')"><svg viewBox="0 0 24 24"><path d="M9 4h6l-1 6h4l-7 10 1-6H8z" /></svg>{{ menu.item.pinned ? "取消置顶" : "置顶" }}</button>
        <button type="button" @click="menu.grouping = true"><svg viewBox="0 0 24 24"><path d="M4 7h7l2 2h7v9H4z" /></svg>移至新分组</button>
        <button type="button" @click="run('unread')"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" /></svg>{{ menu.item.unread ? "标为已读" : "标为未读" }}</button>
        <button type="button" @click="run('window')"><svg viewBox="0 0 24 24"><path d="M5 6h14v12H5zM9 10h6" /></svg>新窗口</button>
        <button type="button" @click="run('rename')"><svg viewBox="0 0 24 24"><path d="M4 16l9-9 3 3-9 9H4zM13 7l3 3" /></svg>重命名 Bot</button>
        <button type="button" @click="run('copy')"><svg viewBox="0 0 24 24"><path d="M8 8h10v12H8zM6 16H5V4h10v2" /></svg>复制对话 ID</button>
        <button type="button" @click="run('hide')"><svg viewBox="0 0 24 24"><path d="M4 7h16M7 12h10M10 17h4" /></svg>{{ menu.item.hidden ? "在侧边栏显示" : "从侧边栏隐藏" }}</button>
        <button type="button" class="danger" @click="run('delete')"><svg viewBox="0 0 24 24"><path d="M6 7h12M9 7V5h6v2M8 7l1 13h6l1-13" /></svg>删除</button>
      </template>
      <form v-else class="group-form" @submit.prevent="saveGroup">
        <p>新分组名称</p>
        <input v-model="groupName" placeholder="例如 工作" />
        <button type="submit">确定</button>
      </form>
    </div>
  </aside>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useChatStore } from "../store";
import Badge from "./Badge.vue";

const chat = useChatStore();
const editing = ref(null);
const menu = ref(null);
const groupName = ref("");
const renameInput = ref(null);

const sections = computed(() => {
  const pinned = [];
  const loose = [];
  const groups = new Map();
  for (const item of chat.sidebarItems) {
    if (item.pinned) {
      pinned.push(item);
      continue;
    }
    const name = (item.group_name || "").trim();
    if (!name) loose.push(item);
    else if (!groups.has(name)) groups.set(name, [item]);
    else groups.get(name).push(item);
  }
  const result = [];
  if (pinned.length) result.push({ name: "置顶", items: pinned });
  if (loose.length) result.push({ name: "", items: loose });
  for (const [name, items] of groups) result.push({ name, items });
  return result;
});

function openMenu(event, item) {
  const width = 232;
  const height = 340;
  menu.value = {
    item,
    grouping: false,
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - width - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - height - 8)),
  };
  groupName.value = "";
}

function closeMenu() {
  menu.value = null;
}

async function run(action) {
  const item = menu.value?.item;
  if (!item) return;
  closeMenu();
  if (action === "pin") await chat.patchConversation(item.id, { pinned: !item.pinned });
  if (action === "unread") await chat.patchConversation(item.id, { unread: !item.unread });
  if (action === "hide") await chat.patchConversation(item.id, { hidden: !item.hidden });
  if (action === "rename") {
    editing.value = item.id;
    await nextTick();
    const node = Array.isArray(renameInput.value) ? renameInput.value[0] : renameInput.value;
    node?.focus();
    node?.select();
  }
  if (action === "copy") {
    await navigator.clipboard.writeText(String(item.id));
    chat.notify("已复制对话 ID");
  }
  if (action === "window") {
    const opened = window.open(`${location.origin}${location.pathname}?bot=${item.id}`, "_blank", "noopener");
    if (!opened) chat.notify("浏览器拦住了新窗口");
  }
  if (action === "delete") await chat.remove(item.id);
}

async function saveGroup() {
  const item = menu.value?.item;
  const name = groupName.value.trim();
  closeMenu();
  if (!item || !name) return;
  await chat.patchConversation(item.id, { group_name: name, pinned: false });
}

function commitRename(item, event) {
  const title = event.target.value.trim();
  editing.value = null;
  if (title && title !== item.title) chat.rename(item.id, title);
}

function onPointerDown(event) {
  if (!menu.value) return;
  if (event.target.closest?.(".context-menu")) return;
  closeMenu();
}

watch(menu, (value) => {
  if (value) window.addEventListener("pointerdown", onPointerDown);
  else window.removeEventListener("pointerdown", onPointerDown);
});

onBeforeUnmount(() => window.removeEventListener("pointerdown", onPointerDown));
</script>
