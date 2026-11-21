<template>
  <span class="group-stage" :class="['kind-' + safe, { live }]" aria-hidden="true">
    <span class="tile">
      <img v-for="slot in faces" :key="slot" class="head" :class="slot" src="/ducks/head.png?v=4" alt="" />
      <i v-if="safe === 'talk'" class="bubble"></i>
      <i v-if="safe === 'relay'" class="arrow"></i>
    </span>
  </span>
</template>

<script setup>
import { computed } from "vue";
import { GROUPS } from "../shapes";

const props = defineProps({
  name: { type: String, default: "flock" },
  live: { type: Boolean, default: false },
});

const layouts = {
  flock: ["p1", "p2", "p3"],
  talk: ["p1", "p2"],
  ring: ["p1", "p2", "p3", "p4"],
  relay: ["p1", "p2"],
  row: ["p1", "p2", "p3"],
  huddle: ["p1", "p2", "p3"],
};

const safe = computed(() => (GROUPS.some((item) => item.id === props.name) ? props.name : "flock"));
const faces = computed(() => layouts[safe.value] || layouts.flock);
</script>

<style scoped>
.group-stage {
  position: relative;
  display: block;
  width: 100%;
  height: 100%;
  color: inherit;
  container-type: inline-size;
}
.tile {
  position: absolute;
  inset: 0;
  overflow: hidden;
  border-radius: 24%;
  background:
    radial-gradient(90% 55% at 28% 0%, color-mix(in srgb, white 58%, currentColor), transparent 70%),
    linear-gradient(160deg, color-mix(in srgb, white 22%, currentColor), currentColor 42%, color-mix(in srgb, black 16%, currentColor));
  box-shadow: inset 0 -8% 12% rgba(0, 0, 0, 0.16), inset 0 10% 14% rgba(255, 255, 255, 0.28);
}
.head {
  position: absolute;
  object-fit: contain;
  filter: drop-shadow(0 1cqw 0.5cqw rgba(70, 36, 8, 0.18));
}
.kind-flock .p1 { left: 0; top: 4%; width: 48%; height: 48%; }
.kind-flock .p2 { right: -2%; top: -2%; width: 48%; height: 48%; }
.kind-flock .p3 { left: 20%; top: 34%; width: 62%; height: 62%; z-index: 2; }
.kind-talk .p1 { left: -2%; top: 32%; width: 56%; height: 56%; }
.kind-talk .p2 { right: -4%; top: 14%; width: 56%; height: 56%; }
.kind-ring .head { width: 42%; height: 42%; }
.kind-ring .p1 { left: 2%; top: 2%; }
.kind-ring .p2 { right: 0; top: 0; }
.kind-ring .p3 { left: 2%; bottom: 0; }
.kind-ring .p4 { right: 0; bottom: 2%; z-index: 2; }
.kind-relay .p1 { left: -2%; top: 28%; width: 54%; height: 54%; }
.kind-relay .p2 { right: -4%; top: 8%; width: 54%; height: 54%; }
.kind-row .head { width: 42%; height: 42%; }
.kind-row .p1 { left: 0; top: 50%; z-index: 3; }
.kind-row .p2 { left: 29%; top: 28%; z-index: 2; }
.kind-row .p3 { right: -2%; top: 4%; }
.kind-huddle .p1 { left: -4%; top: 14%; width: 58%; height: 58%; }
.kind-huddle .p2 { right: -6%; top: 0; width: 52%; height: 52%; }
.kind-huddle .p3 { left: 16%; bottom: -4%; width: 64%; height: 64%; z-index: 2; }
.bubble {
  position: absolute;
  left: 30%;
  top: 4%;
  z-index: 3;
  width: 32%;
  height: 24%;
  border-radius: 46%;
  background: #fff;
  box-shadow: 0 0.15em 0.25em rgba(0, 0, 0, 0.08);
}
.bubble::after {
  content: "";
  position: absolute;
  left: 28%;
  bottom: -18%;
  border-width: 0.42em 0.28em 0;
  border-style: solid;
  border-color: #fff transparent transparent;
}
.arrow {
  position: absolute;
  left: 38%;
  top: 40%;
  z-index: 3;
  width: 22%;
  height: 16%;
  background: #fff;
  transform: rotate(-34deg);
  clip-path: polygon(0 28%, 58% 28%, 58% 0, 100% 50%, 58% 100%, 58% 72%, 0 72%);
}
.live.kind-flock .p3,
.live.kind-huddle .p3,
.live.kind-row .p3,
.live.kind-ring .p4 { animation: bob 1.7s ease-in-out infinite; }
.live.kind-talk .bubble { animation: bob 1.5s ease-in-out infinite; }
.live.kind-relay .arrow { animation: nudge 1.2s ease-in-out infinite; }
@keyframes bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-7%); }
}
@keyframes nudge {
  0%, 100% { transform: rotate(-34deg) translateX(0); }
  50% { transform: rotate(-34deg) translateX(14%); }
}
@media (prefers-reduced-motion: reduce) {
  .live .head, .live .bubble, .live .arrow { animation: none; }
}
</style>
