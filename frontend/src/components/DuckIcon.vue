<template>
  <span class="duck-stage" :class="['kind-' + safe, { live }]" aria-hidden="true">
    <span class="tile">
      <i v-if="safe === 'fly'" class="wing"></i>
      <img class="head" :src="src" alt="" />
      <img v-if="safe === 'waddle'" class="head mini" src="/ducks/head.png?v=4" alt="" />
      <i v-if="safe === 'rubber'" class="shine"></i>
      <i v-if="safe === 'swim'" class="water"></i>
      <i v-if="safe === 'hatch'" class="shell"></i>
      <i v-if="safe === 'nap'" class="zzz">z</i>
    </span>
  </span>
</template>

<script setup>
import { computed } from "vue";
import { DUCKS } from "../shapes";

const props = defineProps({
  name: { type: String, default: "chick" },
  live: { type: Boolean, default: false },
});

const safe = computed(() => (DUCKS.some((item) => item.id === props.name) ? props.name : "chick"));
const src = computed(() => {
  if (safe.value === "nap") return "/ducks/shut.png?v=4";
  if (safe.value === "sailor") return "/ducks/sailor.png?v=4";
  return "/ducks/head.png?v=4";
});
</script>

<style scoped>
.duck-stage {
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
  left: 1%;
  top: 0;
  z-index: 2;
  width: 98%;
  height: 98%;
  object-fit: contain;
  filter: drop-shadow(0 1.4cqw 0.6cqw rgba(70, 36, 8, 0.2));
}
.wing {
  position: absolute;
  left: -2%;
  top: 48%;
  z-index: 1;
  width: 36%;
  height: 26%;
  border-radius: 70% 40% 55% 50%;
  background: radial-gradient(circle at 35% 30%, #fff, #f4ece3 72%);
  transform: rotate(-28deg);
  transform-origin: 88% 50%;
}
.shine {
  position: absolute;
  left: 16%;
  top: 14%;
  z-index: 3;
  width: 26%;
  height: 14%;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.72);
  transform: rotate(-32deg);
}
.water {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 4;
  height: 20%;
  background: linear-gradient(transparent, rgba(120, 196, 255, 0.2) 20%, rgba(52, 148, 236, 0.82));
}
.water::before {
  content: "";
  position: absolute;
  left: -10%;
  right: -10%;
  top: -28%;
  height: 70%;
  border-radius: 50%;
  background: rgba(186, 226, 255, 0.7);
}
.shell {
  position: absolute;
  left: 6%;
  bottom: 5%;
  z-index: 4;
  width: 54%;
  height: 30%;
  border-radius: 46% 46% 42% 42%;
  background: linear-gradient(#fffaf3, #f4d7a6 68%, #e4b56d);
  box-shadow: inset 0 0.4em 0 rgba(255, 255, 255, 0.8);
}
.zzz {
  position: absolute;
  top: 8%;
  right: 8%;
  z-index: 5;
  color: #fff;
  font-style: normal;
  font-weight: 800;
  font-size: 22cqw;
  font-family: "Segoe UI", sans-serif;
  text-shadow: 0 0.12em 0 rgba(0, 0, 0, 0.12);
}
.kind-swim .head { top: -4%; height: 92%; }
.kind-fly .head { left: 12%; top: -6%; width: 90%; }
.kind-hatch .head { top: -8%; }
.kind-waddle .head { left: -4%; top: -2%; width: 80%; }
.kind-waddle .head.mini { left: 50%; top: 42%; width: 52%; height: 52%; z-index: 3; }
.live .head { animation: bob 1.8s ease-in-out infinite; }
.live.kind-swim .water { animation: wash 1.4s ease-in-out infinite; }
.live.kind-fly .head { animation: lift 1.3s ease-in-out infinite; }
.live.kind-fly .wing { animation: flap 0.55s ease-in-out infinite alternate; }
.live.kind-hatch .head { animation: peek 1.8s ease-in-out infinite; }
.live.kind-nap .head { animation: breathe 2.4s ease-in-out infinite; }
.live.kind-nap .zzz { animation: drift 2.2s ease-in-out infinite; }
.live.kind-waddle .head.mini { animation: bob 0.9s ease-in-out infinite; }
@keyframes bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4%); }
}
@keyframes wash {
  0%, 100% { transform: translateX(0); }
  50% { transform: translateX(5%); }
}
@keyframes lift {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8%); }
}
@keyframes flap {
  from { transform: rotate(-10deg); }
  to { transform: rotate(-42deg); }
}
@keyframes peek {
  0%, 100% { transform: translateY(10%); }
  45%, 70% { transform: translateY(-2%); }
}
@keyframes breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.035); }
}
@keyframes drift {
  0%, 100% { transform: translate(0, 0); opacity: 0.45; }
  50% { transform: translate(8%, -18%); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .live .head, .live .water, .live .wing, .live .zzz { animation: none; }
}
</style>
