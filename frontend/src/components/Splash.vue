<template>
  <div v-if="shown" class="splash" :class="{ out }" role="dialog" aria-label="Duck 启动" @pointerdown="enter">
    <img v-if="!motion" class="clip on" src="/splash/Duck_poster_HD.png" alt="" />
    <video
      v-else
      ref="introEl"
      class="clip on"
      src="/splash/Duck_intro_HD.mp4"
      muted
      playsinline
      autoplay
      preload="auto"
      @playing="arm"
      @ended="finish"
      @error="enter"
    ></video>
    <button type="button" class="enter" @click.stop="enter">进入</button>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";

const emit = defineEmits(["done"]);

// The last half second of the intro whips the head from one side to the other.
// Hold the frame just before that, instead of playing it or cutting to the idle loop.
const CALM_AT = 8.42;

const motion = ref(!window.matchMedia("(prefers-reduced-motion: reduce)").matches);
const shown = ref(true);
const out = ref(false);
const introEl = ref(null);
let timer = 0;
let watchId = 0;
let closed = false;
let held = false;
let armed = false;

function enter() {
  if (closed) return;
  closed = true;
  window.clearTimeout(timer);
  out.value = true;
  window.setTimeout(() => {
    shown.value = false;
    emit("done");
  }, 460);
}

function finish() {
  if (closed || held) return;
  held = true;
  const intro = introEl.value;
  if (intro) {
    intro.pause();
    if (intro.currentTime > 8.52) intro.currentTime = CALM_AT;
  }
  timer = window.setTimeout(enter, 700);
}

function arm() {
  const intro = introEl.value;
  if (!intro || armed) return;
  armed = true;
  const step = () => {
    if (closed || held) return;
    if (intro.currentTime >= CALM_AT) {
      finish();
      return;
    }
    if (intro.requestVideoFrameCallback) intro.requestVideoFrameCallback(step);
    else watchId = window.requestAnimationFrame(step);
  };
  intro.addEventListener("timeupdate", () => {
    if (!held && intro.currentTime >= CALM_AT) finish();
  });
  if (intro.requestVideoFrameCallback) intro.requestVideoFrameCallback(step);
  else watchId = window.requestAnimationFrame(step);
}

function onKey(event) {
  if (event.key === "Escape" || event.key === "Enter") enter();
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  if (!motion.value) timer = window.setTimeout(enter, 900);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  window.clearTimeout(timer);
  window.cancelAnimationFrame(watchId);
});
</script>

<style scoped>
.splash {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: grid;
  place-items: center;
  background: #080d17;
  cursor: pointer;
}
.splash.out { opacity: 0; transition: opacity 0.45s ease; pointer-events: none; }
.clip {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #080d17;
  opacity: 0;
}
.clip.on { opacity: 1; }
.enter {
  position: absolute;
  left: 7%;
  bottom: 12%;
  border: 1px solid rgba(255, 255, 255, 0.45);
  background: transparent;
  color: #fff;
  border-radius: 999px;
  padding: 8px 18px;
  font: inherit;
  letter-spacing: 0.08em;
}
.enter:hover { background: rgba(255, 255, 255, 0.12); }
</style>
