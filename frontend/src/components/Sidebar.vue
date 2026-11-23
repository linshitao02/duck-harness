<template>
  <aside class="sidebar">
    <div class="side-tools pywebview-drag-region">
      <span class="brand-mark" aria-hidden="true">
        <img src="/duck.png?v=2" alt="" />
      </span>
      <span class="product-title">Duck Harness</span>
      <div class="side-actions">
      <button class="icon-btn" type="button" aria-label="搜索会话" data-testid="open-search" @mousedown.stop @click.stop="chat.searchOpen = !chat.searchOpen">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6" /><path d="M16 16l4 4" /></svg>
      </button>
      <button class="icon-btn" type="button" aria-label="刷新会话列表" data-testid="refresh-conversations" :disabled="refreshing" @mousedown.stop @click.stop="refreshList">
        <svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 0 1-14.5 4.5L4 15" /><path d="M4 12a8 8 0 0 1 14.5-4.5L20 9" /><path d="M20 4v5h-5M4 20v-5h5" /></svg>
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
          v-for="(item, index) in section.items"
          :key="item.id"
          class="conv"
          :class="{
            active: item.id === chat.activeId,
            unread: item.unread,
            dragging: drag.id === item.id,
            'drop-before': drag.key === sectionKey(item) && drag.target === index && drag.id !== item.id,
            'drop-after': drag.key === sectionKey(item) && drag.target === section.items.length && index === section.items.length - 1 && drag.id !== item.id,
          }"
          :data-testid="'conv-' + item.id"
          :data-drag-section="sectionKey(item)"
          @click="onConvClick(item)"
          @pointerdown="startDrag($event, item, section)"
          @contextmenu.prevent.stop="openMenu($event, item)"
        >
          <span class="badge" :style="badgeStyle(item.icon_key, item.icon_color)">
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
      <div class="account-wrap">
        <button
          class="account"
          type="button"
          data-testid="open-account"
          :aria-expanded="accountOpen"
          @click.stop="accountOpen = !accountOpen"
        >
          <span class="avatar" aria-hidden="true">{{ initial }}</span>
          <strong>{{ chat.userName }}</strong>
        </button>
        <div v-if="accountOpen" class="account-menu" data-testid="account-menu" @click.stop>
          <button type="button" data-testid="open-settings" @click="openSettings('general')">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" /><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18" /></svg>
            设置
          </button>
          <button type="button" @click="openSettings('mcp')">
            <svg viewBox="0 0 24 24"><path d="M9 7v4M15 7v4M8 11h8v5a4 4 0 0 1-8 0zM12 16v3" /></svg>
            MCP
          </button>
          <button type="button" @click="openSettings('skills')">
            <svg viewBox="0 0 24 24"><path d="M6 4h9l3 3v13H6zM15 4v4h4M8 12h8M8 16h6" /></svg>
            Skills
          </button>
          <button type="button" @click="openSettings('memory')">
            <svg viewBox="0 0 24 24"><path d="M12 4a6 6 0 0 0-6 6c0 2.2 1.2 3.6 2.2 4.6.6.6 1 1.4 1.1 2.2h5.4c.1-.8.5-1.6 1.1-2.2 1-1 2.2-2.4 2.2-4.6a6 6 0 0 0-6-6zM9.5 20h5" /></svg>
            记忆
          </button>
          <button type="button" @click="openSettings('tasks')">
            <svg viewBox="0 0 24 24"><path d="M8 6h11M8 12h11M8 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" /></svg>
            任务
          </button>
          <button type="button" class="danger" data-testid="logout" @click="chat.logout()">
            <svg viewBox="0 0 24 24"><path d="M10 7V5H5v14h5v-2M10 12h9M16 9l3 3-3 3" /></svg>
            退出登录
          </button>
        </div>
      </div>
      <button class="connect" type="button" data-testid="connect" @click="accountOpen = false; chat.openConnect()">
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
import { badgeStyle } from "../shapes";
import Badge from "./Badge.vue";

const chat = useChatStore();
const editing = ref(null);
const refreshing = ref(false);
const menu = ref(null);
const groupName = ref("");
const renameInput = ref(null);
const accountOpen = ref(false);
const drag = ref({ id: null, key: "", target: -1 });
let justDragged = false;

const initial = computed(() => {
  const name = (chat.userName || "用").trim();
  return name.slice(0, 1) || "用";
});

function sectionKey(item) {
  if (item.pinned) return "pinned";
  return "plain:" + encodeURIComponent((item.group_name || "").trim());
}

function openSettings(tab) {
  accountOpen.value = false;
  chat.openSettings(tab);
}

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

async function refreshList() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await chat.refreshConversations();
    chat.notify("会话列表已更新");
  } catch {
    chat.notify("刷新会话列表失败");
  } finally {
    refreshing.value = false;
  }
}

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
  if (menu.value && !event.target.closest?.(".context-menu")) closeMenu();
  if (accountOpen.value && !event.target.closest?.(".account-wrap")) accountOpen.value = false;
}

function onConvClick(item) {
  if (justDragged) return;
  chat.select(item.id);
}

function startDrag(event, item, section) {
  if (event.button !== 0) return;
  if (event.target.closest?.("input, textarea")) return;
  const key = sectionKey(item);
  const items = section.items;
  const origin = items.findIndex((row) => row.id === item.id);
  const startY = event.clientY;
  let moved = false;
  const move = (ev) => {
    if (!moved && Math.abs(ev.clientY - startY) < 6) return;
    moved = true;
    document.body.classList.add("row-drag");
    const nodes = [...document.querySelectorAll(`[data-drag-section="${key}"]`)];
    let insertAt = nodes.length;
    for (let index = 0; index < nodes.length; index += 1) {
      const box = nodes[index].getBoundingClientRect();
      if (ev.clientY < box.top + box.height / 2) {
        insertAt = index;
        break;
      }
    }
    let target = insertAt > origin ? insertAt - 1 : insertAt;
    target = Math.max(0, Math.min(items.length - 1, target));
    drag.value = { id: item.id, key, target: target === items.length - 1 && insertAt === nodes.length ? items.length : target };
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
    document.body.classList.remove("row-drag");
    const target = drag.value.target;
    drag.value = { id: null, key: "", target: -1 };
    if (!moved) return;
    justDragged = true;
    window.setTimeout(() => {
      justDragged = false;
    }, 0);
    if (target < 0 || items.length < 2) return;
    const next = items.map((row) => row.id);
    const [id] = next.splice(origin, 1);
    next.splice(Math.min(target, next.length), 0, id);
    if (next.every((value, index) => value === items[index].id)) return;
    chat.reorderConversations(next);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
}

watch([menu, accountOpen], ([menuValue, accountValue]) => {
  if (menuValue || accountValue) window.addEventListener("pointerdown", onPointerDown);
  else window.removeEventListener("pointerdown", onPointerDown);
});

onBeforeUnmount(() => window.removeEventListener("pointerdown", onPointerDown));
</script>
