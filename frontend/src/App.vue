<template>
  <div class="shell">
    <Sidebar v-show="chat.sidebarOpen" :style="{ width: chat.sidebarWidth + 'px' }" />
    <div
      v-if="chat.sidebarOpen"
      class="splitter"
      :class="{ active: splitting }"
      data-testid="sidebar-splitter"
      @pointerdown.prevent="startSplit"
    />
    <ChatPane />
    <SearchPanel v-if="chat.searchOpen" />
    <NewBotDialog v-if="chat.botOpen" />
    <ConnectSheet v-if="chat.connectOpen" />
    <SettingsDialog v-if="chat.settingsOpen" />
    <TextMenu />
    <div v-if="chat.toast" class="toast" role="status">{{ chat.toast }}</div>
    <div v-if="desktop" class="resize-layer" aria-hidden="true">
      <div class="rz n" @pointerdown.prevent="startResize('n', $event)" />
      <div class="rz s" @pointerdown.prevent="startResize('s', $event)" />
      <div class="rz e" @pointerdown.prevent="startResize('e', $event)" />
      <div class="rz w" @pointerdown.prevent="startResize('w', $event)" />
      <div class="rz ne" @pointerdown.prevent="startResize('ne', $event)" />
      <div class="rz nw" @pointerdown.prevent="startResize('nw', $event)" />
      <div class="rz se" @pointerdown.prevent="startResize('se', $event)" />
      <div class="rz sw" @pointerdown.prevent="startResize('sw', $event)" />
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useChatStore } from "./store";
import Sidebar from "./components/Sidebar.vue";
import ChatPane from "./components/ChatPane.vue";
import SearchPanel from "./components/SearchPanel.vue";
import NewBotDialog from "./components/NewBotDialog.vue";
import ConnectSheet from "./components/ConnectSheet.vue";
import SettingsDialog from "./components/SettingsDialog.vue";
import TextMenu from "./components/TextMenu.vue";

const chat = useChatStore();
const desktop = ref(false);
const splitting = ref(false);

function markDesktop() {
  desktop.value = Boolean(window.pywebview);
}

function startResize(edge, event) {
  const dragId = Date.now();
  const originX = event.screenX;
  const originY = event.screenY;
  const move = (ev) => {
    window.pywebview?.api?.resize_by?.(dragId, edge, ev.screenX - originX, ev.screenY - originY);
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
    document.body.classList.remove("window-resize");
  };
  document.body.classList.add("window-resize");
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
}

function startSplit(event) {
  const startX = event.clientX;
  const startWidth = chat.sidebarWidth;
  splitting.value = true;
  document.body.classList.add("col-resize");
  const move = (ev) => chat.setSidebarWidth(startWidth + ev.clientX - startX);
  const stop = () => {
    splitting.value = false;
    document.body.classList.remove("col-resize");
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
}

function desktopReady() {
  markDesktop();
  chat.loadDesktopLayout();
}

onMounted(() => {
  desktopReady();
  window.addEventListener("pywebviewready", desktopReady);
  chat.boot().catch((error) => chat.notify(error.message || "无法连接后端"));
});
</script>
