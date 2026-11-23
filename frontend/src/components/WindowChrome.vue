<template>
  <header class="desktop-chrome pywebview-drag-region">
    <span>Duck Harness</span>
    <div class="window-controls">
      <button type="button" aria-label="最小化" @mousedown.stop @click="act('minimize')">–</button>
      <button type="button" aria-label="最大化" @mousedown.stop @click="act('maximize')">□</button>
      <button type="button" class="close" aria-label="关闭" @mousedown.stop @click="act('close')">×</button>
    </div>
  </header>
</template>

<script setup>
function act(name) {
  window.pywebview?.api?.[name]?.();
}
</script>

<style scoped>
.desktop-chrome {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 42px;
  z-index: 90;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 0 10px 0 16px;
  color: rgba(255, 244, 232, 0.92);
  background: linear-gradient(180deg, rgba(8, 8, 12, 0.55), transparent);
}
.desktop-chrome span {
  margin-right: auto;
  font-size: 13px;
  letter-spacing: 0.04em;
}
.desktop-chrome .window-controls {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
}
.desktop-chrome .window-controls button {
  background: transparent;
  color: inherit;
}
.desktop-chrome .window-controls button:hover {
  background: rgba(255, 255, 255, 0.14);
}
.desktop-chrome .window-controls .close:hover {
  background: #ff4b4b;
  color: #fff;
}
</style>
