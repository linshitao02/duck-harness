<template>
  <div class="gate" :class="{ still: !motion }">
    <div class="streaks" aria-hidden="true">
      <i v-for="item in streaks" :key="item" :style="{ '--n': item }" />
    </div>
    <aside class="stage" aria-hidden="true">
      <video
        v-if="motion && live"
        class="portrait"
        src="/login/duck-idle.mp4"
        poster="/login/duck-still.jpg"
        muted
        loop
        autoplay
        playsinline
        preload="auto"
        @error="live = false"
      />
      <img v-else class="portrait" src="/login/duck-still.jpg" alt="" />
      <div class="veil" />
      <p class="corner">MORE<br />INTELLIGENT<br />MORE<br />CREATIVE</p>
    </aside>
    <section class="panel">
      <header class="brand">
        <img src="/duck.png?v=2" alt="" />
        <div>
          <strong>Duck Harness</strong>
          <em>YOUR AI COMPANION</em>
        </div>
      </header>
      <ol class="status">
        <li v-for="(item, index) in statuses" :key="item" :class="{ on: index === activeStatus }">{{ item }}</li>
      </ol>
      <h1>Duck</h1>
      <p class="welcome">欢迎回来</p>
      <p class="sub">登录后只看自己的会话和文件</p>
      <form @submit.prevent="submit">
        <label>
          用户名
          <input v-model="username" data-testid="login-username" autocomplete="username" maxlength="32" />
        </label>
        <label>
          密码
          <input v-model="password" data-testid="login-password" type="password" autocomplete="current-password" maxlength="72" />
        </label>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button class="submit" type="submit" data-testid="login-submit" :disabled="busy">
          {{ busy ? "正在进入" : "进入" }}
        </button>
      </form>
      <pre class="term" aria-hidden="true">{{ typed }}<b>_</b></pre>
    </section>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";

const emit = defineEmits(["success"]);
const motion = ref(!window.matchMedia("(prefers-reduced-motion: reduce)").matches);
const live = ref(true);
const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
const activeStatus = ref(0);
const typed = ref("");
const streaks = [0, 1, 2, 3, 4, 5];
const statuses = ["待命", "核验身份", "打开工作区", "进入会话"];
const lines = ["> initializing duck", "> loading workspace", "> ready"];
let statusTimer = 0;
let typeTimer = 0;

async function submit() {
  error.value = "";
  const name = username.value.trim();
  if (name.length < 2) {
    error.value = "用户名至少 2 个字";
    return;
  }
  if (password.value.length < 6) {
    error.value = "密码至少 6 位";
    return;
  }
  busy.value = true;
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: name,
        password: password.value,
        remember: true,
      }),
    });
    emit("success");
  } catch (err) {
    error.value = err.message || "没能登录";
  } finally {
    busy.value = false;
  }
}

function playType() {
  let line = 0;
  let count = 0;
  const step = () => {
    const text = lines[line];
    count += 1;
    typed.value = lines.slice(0, line).concat(text.slice(0, count)).join("\n");
    if (count >= text.length) {
      line += 1;
      count = 0;
      if (line >= lines.length) {
        line = 0;
        typed.value = "";
      }
    }
    typeTimer = window.setTimeout(step, count === 0 ? 700 : 46);
  };
  step();
}

onMounted(() => {
  if (!motion.value) {
    typed.value = lines.join("\n");
    return;
  }
  statusTimer = window.setInterval(() => {
    activeStatus.value = (activeStatus.value + 1) % statuses.length;
  }, 1400);
  playType();
});

onBeforeUnmount(() => {
  window.clearInterval(statusTimer);
  window.clearTimeout(typeTimer);
});
</script>

<style scoped>
.gate {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: grid;
  grid-template-columns: minmax(420px, 88vh) minmax(340px, 1fr);
  background: #140f0c;
  color: #fff4e8;
  overflow: hidden;
}
.streaks { position: absolute; inset: 0; pointer-events: none; }
.streaks i {
  position: absolute;
  top: -20%;
  left: calc(8% + var(--n) * 14%);
  width: 2px;
  height: 28%;
  background: linear-gradient(180deg, transparent, rgba(244, 168, 84, 0.75), transparent);
  transform: rotate(18deg);
  animation: fall 7s linear infinite;
  animation-delay: calc(var(--n) * -1.1s);
  opacity: 0.65;
}
.panel {
  position: relative;
  z-index: 1;
  padding: 5vh 28px 4vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}
.panel > * { width: min(360px, 100%); }
.brand { display: flex; align-items: center; gap: 12px; }
.brand img { width: 46px; height: 46px; border-radius: 14px; object-fit: cover; }
.brand strong { display: block; letter-spacing: 0.08em; font-size: 14px; }
.brand em { display: block; margin-top: 2px; color: rgba(255, 228, 196, 0.55); font-style: normal; font-size: 10px; letter-spacing: 0.16em; }
.status { list-style: none; margin: 28px 0 18px; padding: 0; color: rgba(244, 176, 110, 0.45); font-size: 12px; letter-spacing: 0.14em; }
.status li.on { color: #ffd7a8; text-shadow: 0 0 12px rgba(244, 168, 84, 0.7); }
h1 {
  margin: 0;
  font-size: clamp(56px, 6vw, 84px);
  line-height: 0.9;
  letter-spacing: 0.04em;
  text-shadow: 0 0 24px rgba(244, 168, 84, 0.28);
}
.welcome { margin: 10px 0 0; font-size: 22px; }
.sub { margin: 6px 0 18px; color: rgba(255, 236, 214, 0.68); font-size: 13px; }
form { display: grid; gap: 12px; }
label { display: grid; gap: 6px; font-size: 12px; color: rgba(255, 228, 196, 0.76); letter-spacing: 0.08em; }
input {
  height: 42px;
  border: 1px solid rgba(244, 176, 110, 0.38);
  border-radius: 8px;
  background: rgba(32, 20, 12, 0.72);
  color: #fff8f0;
  padding: 0 12px;
  outline: none;
}
input:focus { border-color: #ffc27a; box-shadow: 0 0 0 3px rgba(244, 168, 84, 0.18); }
.error { margin: 0; color: #ffb4c8; font-size: 13px; }
.submit {
  border: 0;
  background: transparent;
  color: inherit;
}
.submit {
  height: 44px;
  border-radius: 8px;
  background: linear-gradient(90deg, #e07a2f, #f6b15a);
  color: #2a1408;
  font-weight: 700;
}
.submit:disabled { opacity: 0.6; }
.term {
  margin: 22px 0 0;
  min-height: 4.2em;
  color: #f0c48a;
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 13px;
  white-space: pre-wrap;
}
.term b { animation: blink 1s steps(1) infinite; }
.stage {
  position: relative;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  background:
    radial-gradient(ellipse at 50% 46%, rgba(244, 162, 74, 0.16), transparent 62%),
    #100d0b;
}
.portrait {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 46%;
  background: #100d0b;
}
.veil {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(90deg, transparent 72%, rgba(20, 15, 12, 0.55)),
    linear-gradient(180deg, rgba(16, 13, 11, 0.18), transparent 18%, transparent 82%, rgba(16, 13, 11, 0.28));
}
.corner {
  position: absolute;
  left: 6%;
  top: 8%;
  margin: 0;
  color: rgba(255, 214, 168, 0.72);
  letter-spacing: 0.18em;
  font-size: 12px;
  line-height: 1.7;
}
.still .streaks i, .still .term b { animation: none; }
@keyframes fall { to { transform: translateY(140vh) rotate(18deg); } }
@keyframes blink { 50% { opacity: 0; } }
@media (max-width: 860px) {
  .gate { grid-template-columns: 1fr; grid-template-rows: 42vh 1fr; overflow: auto; }
  .stage { min-height: 42vh; }
  .panel { align-items: stretch; padding: 24px 22px 36px; }
  .panel > * { width: 100%; }
  h1 { font-size: 64px; }
}
</style>
