<template>
  <span class="duck-stage" :class="['kind-' + safe, { live }]" aria-hidden="true">
    <i class="shadow"></i>
    <span class="rig">
      <template v-if="safe === 'hatch'">
        <i class="shell back"></i>
        <span class="head peek">
          <i class="eye left"></i>
          <i class="eye right"></i>
          <i class="bill"></i>
        </span>
        <i class="shell front"></i>
      </template>
      <template v-else-if="safe === 'fly'">
        <i class="wing left"></i>
        <i class="wing right"></i>
        <i class="body"></i>
        <span class="head">
          <i class="eye left"></i>
          <i class="eye right"></i>
          <i class="bill"></i>
        </span>
      </template>
      <template v-else-if="safe === 'nap'">
        <i class="body"></i>
        <span class="head">
          <i class="eye shut left"></i>
          <i class="eye shut right"></i>
          <i class="bill"></i>
        </span>
        <i class="zzz">z</i>
      </template>
      <template v-else-if="safe === 'swim'">
        <i class="water"></i>
        <i class="body"></i>
        <span class="head">
          <i class="eye left"></i>
          <i class="eye right"></i>
          <i class="bill"></i>
        </span>
      </template>
      <template v-else-if="safe === 'waddle'">
        <span class="adult">
          <i class="body"></i>
          <span class="head">
            <i class="eye left"></i>
            <i class="eye right"></i>
            <i class="bill"></i>
          </span>
          <i class="foot left"></i>
          <i class="foot right"></i>
        </span>
        <span class="mini">
          <i class="body"></i>
          <span class="head">
            <i class="eye left"></i>
            <i class="eye right"></i>
            <i class="bill"></i>
          </span>
        </span>
      </template>
      <template v-else>
        <i class="body"></i>
        <span class="head">
          <i v-if="safe === 'sailor'" class="hat"></i>
          <i class="eye left"></i>
          <i class="eye right"></i>
          <i class="blush left"></i>
          <i class="blush right"></i>
          <i class="bill"></i>
        </span>
        <i class="foot left"></i>
        <i class="foot right"></i>
      </template>
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
</script>

<style scoped>
.duck-stage {
  position: relative;
  display: block;
  width: 100%;
  height: 100%;
  perspective: 220px;
  color: inherit;
}
.shadow {
  position: absolute;
  left: 22%;
  right: 22%;
  bottom: 5%;
  height: 12%;
  border-radius: 50%;
  background: rgba(28, 28, 30, 0.16);
  filter: blur(1px);
}
.rig, .adult, .mini, .head { position: absolute; transform-style: preserve-3d; }
.rig { inset: 0; }
.body, .head, .shell {
  position: absolute;
  border-radius: 50%;
  background:
    radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.85) 0 7%, transparent 18%),
    radial-gradient(circle at 50% 42%, color-mix(in srgb, currentColor 62%, white), currentColor 58% 62%, color-mix(in srgb, currentColor 62%, black));
  box-shadow:
    inset -8% -12% 12% color-mix(in srgb, currentColor 35%, black),
    0 8% 10% rgba(28, 28, 30, 0.08);
}
.head { overflow: visible; }
.eye {
  position: absolute;
  width: 22%;
  height: 26%;
  border-radius: 50%;
  background: #fff;
  box-shadow: inset 0 -1px 0 rgba(0, 0, 0, 0.06);
  z-index: 2;
}
.eye::after {
  content: "";
  position: absolute;
  width: 46%;
  height: 46%;
  left: 30%;
  top: 30%;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, #fff 0 18%, #1c1c1e 20%);
}
.eye.left { left: 18%; top: 34%; }
.eye.right { right: 16%; top: 34%; }
.eye.shut {
  height: 14%;
  top: 46%;
  background: transparent;
  box-shadow: none;
  border-bottom: 2px solid #1c1c1e;
  border-radius: 0 0 80% 80%;
}
.eye.shut::after { display: none; }
.bill {
  position: absolute;
  width: 34%;
  height: 18%;
  left: 58%;
  top: 52%;
  border-radius: 20% 70% 60% 30%;
  background: linear-gradient(#ffe08a, #ff9a1f 55%, #e25b00);
  box-shadow: 0 18% 0 #c2410c;
  z-index: 3;
}
.bill::after {
  content: "";
  position: absolute;
  width: 18%;
  height: 28%;
  left: 24%;
  top: 28%;
  border-radius: 50%;
  background: rgba(120, 40, 0, 0.4);
}
.blush {
  position: absolute;
  width: 16%;
  height: 10%;
  top: 62%;
  border-radius: 50%;
  background: rgba(255, 120, 140, 0.55);
}
.blush.left { left: 12%; }
.blush.right { right: 18%; }
.foot {
  position: absolute;
  width: 16%;
  height: 8%;
  bottom: 7%;
  border-radius: 40% 40% 50% 50%;
  background: linear-gradient(#ffb020, #f08a24);
  z-index: 1;
}
.foot.left { left: 30%; }
.foot.right { right: 30%; }
.wing {
  position: absolute;
  width: 34%;
  height: 22%;
  top: 40%;
  border-radius: 50%;
  background: radial-gradient(circle at 40% 40%, color-mix(in srgb, currentColor 50%, white), color-mix(in srgb, currentColor 70%, black));
  z-index: 0;
}
.wing.left { left: 2%; transform-origin: right center; }
.wing.right { right: 2%; transform-origin: left center; }
.water {
  position: absolute;
  left: 10%;
  right: 10%;
  bottom: 12%;
  height: 18%;
  border-radius: 50%;
  background: radial-gradient(ellipse at center, rgba(47, 191, 176, 0.55), rgba(59, 130, 246, 0.15) 70%, transparent 72%);
}
.shell {
  background:
    radial-gradient(circle at 32% 28%, #fff 0 8%, transparent 18%),
    radial-gradient(circle at 50% 40%, #fff8ea, #f3d7a4 70%, #e2b56a);
  box-shadow: inset -8% -10% 12% rgba(140, 90, 30, 0.28);
}
.shell.back { width: 48%; height: 58%; left: 26%; bottom: 12%; }
.shell.front { width: 52%; height: 36%; left: 24%; bottom: 10%; border-radius: 0 0 50% 50%; z-index: 3; }
.hat {
  position: absolute;
  left: 16%;
  top: -8%;
  width: 68%;
  height: 34%;
  border-radius: 46% 46% 18% 18%;
  background: linear-gradient(#fff, #f4f4f6);
  box-shadow: 0 2px 0 #1c1c1e;
  z-index: 4;
}
.hat::before {
  content: "";
  position: absolute;
  left: -16%;
  right: -16%;
  bottom: -18%;
  height: 42%;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 0 0 1px #1c1c1e;
}
.hat::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 28%;
  background: #244a9a;
}
.zzz {
  position: absolute;
  right: 8%;
  top: 8%;
  color: #8d8d95;
  font-style: normal;
  font-weight: 800;
  font-size: 22cqw;
  font-family: "Segoe UI", sans-serif;
}
.duck-stage { container-type: inline-size; }

.kind-chick .body { width: 42%; height: 34%; left: 29%; bottom: 12%; }
.kind-chick .head { width: 54%; height: 54%; left: 23%; top: 8%; }
.kind-rubber .body { width: 70%; height: 50%; left: 15%; bottom: 12%; border-radius: 48% 48% 44% 44%; }
.kind-rubber .head { width: 44%; height: 44%; left: 34%; top: 4%; }
.kind-rubber .bill { width: 40%; left: 64%; }
.kind-rubber .foot { display: none; }
.kind-sailor .body { width: 42%; height: 34%; left: 29%; bottom: 12%; }
.kind-sailor .head { width: 52%; height: 52%; left: 24%; top: 12%; }
.kind-swim .body { width: 58%; height: 30%; left: 22%; bottom: 22%; }
.kind-swim .head { width: 38%; height: 38%; left: 12%; top: 24%; }
.kind-fly .body { width: 40%; height: 34%; left: 30%; top: 36%; }
.kind-fly .head { width: 36%; height: 36%; left: 46%; top: 18%; }
.kind-nap .body { width: 58%; height: 32%; left: 22%; bottom: 22%; }
.kind-nap .head { width: 42%; height: 42%; left: 8%; top: 26%; }
.kind-hatch .peek { width: 40%; height: 40%; left: 30%; top: 16%; z-index: 2; }
.kind-waddle .adult { inset: 0; }
.kind-waddle .adult .body { width: 40%; height: 32%; left: 16%; bottom: 14%; }
.kind-waddle .adult .head { width: 40%; height: 40%; left: 12%; top: 16%; }
.kind-waddle .adult .foot.left { left: 18%; }
.kind-waddle .adult .foot.right { left: 36%; right: auto; }
.kind-waddle .mini { width: 48%; height: 48%; right: 4%; bottom: 10%; }
.kind-waddle .mini .body { width: 46%; height: 36%; left: 28%; bottom: 8%; }
.kind-waddle .mini .head { width: 58%; height: 58%; left: 18%; top: 4%; }

.live.kind-chick .rig { animation: duck-bob 1.5s ease-in-out infinite; }
.live.kind-rubber .rig { animation: duck-rock 1.7s ease-in-out infinite; }
.live.kind-swim .rig { animation: duck-bob 1.3s ease-in-out infinite; }
.live.kind-swim .water { animation: duck-ripple 1.3s ease-in-out infinite; }
.live.kind-fly .wing.left { animation: flap-left 0.42s ease-in-out infinite alternate; }
.live.kind-fly .wing.right { animation: flap-right 0.42s ease-in-out infinite alternate; }
.live.kind-fly .rig { animation: duck-bob 0.84s ease-in-out infinite; }
.live.kind-hatch .rig { animation: duck-wobble 1.1s ease-in-out infinite; }
.live.kind-hatch .peek { animation: duck-peek 1.8s ease-in-out infinite; }
.live.kind-nap .rig { animation: duck-breathe 2.4s ease-in-out infinite; }
.live.kind-nap .zzz { animation: duck-float 2.4s ease-in-out infinite; }
.live.kind-sailor .rig { animation: duck-sway 2s ease-in-out infinite; }
.live.kind-waddle .rig { animation: duck-waddle 0.7s ease-in-out infinite; }
.live .eye:not(.shut) { animation: duck-blink 3.4s infinite; }
.live .shadow { animation: duck-shadow 1.5s ease-in-out infinite; }

@keyframes duck-bob {
  0%, 100% { transform: translateY(0) rotate(0deg); }
  50% { transform: translateY(-7%) rotate(-4deg); }
}
@keyframes duck-rock {
  0%, 100% { transform: rotate(-8deg); }
  50% { transform: rotate(8deg); }
}
@keyframes duck-ripple {
  0%, 100% { transform: scaleX(1); opacity: 0.85; }
  50% { transform: scaleX(1.18); opacity: 0.45; }
}
@keyframes flap-left {
  from { transform: rotate(16deg); }
  to { transform: rotate(-32deg); }
}
@keyframes flap-right {
  from { transform: rotate(-16deg); }
  to { transform: rotate(32deg); }
}
@keyframes duck-wobble {
  0%, 100% { transform: rotate(-4deg); }
  50% { transform: rotate(4deg); }
}
@keyframes duck-peek {
  0%, 100% { transform: translateY(8%); }
  45%, 70% { transform: translateY(-6%); }
}
@keyframes duck-breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.04); }
}
@keyframes duck-float {
  0%, 100% { transform: translateY(0); opacity: 0.4; }
  50% { transform: translateY(-18%); opacity: 1; }
}
@keyframes duck-sway {
  0%, 100% { transform: rotateY(-18deg) rotate(-2deg); }
  50% { transform: rotateY(18deg) rotate(2deg); }
}
@keyframes duck-waddle {
  0%, 100% { transform: translateX(-4%) rotate(-5deg); }
  50% { transform: translateX(4%) rotate(5deg); }
}
@keyframes duck-blink {
  0%, 92%, 100% { transform: scaleY(1); }
  96% { transform: scaleY(0.12); }
}
@keyframes duck-shadow {
  0%, 100% { transform: scaleX(1); opacity: 0.9; }
  50% { transform: scaleX(0.82); opacity: 0.45; }
}
@media (prefers-reduced-motion: reduce) {
  .live .rig,
  .live .wing,
  .live .water,
  .live .zzz,
  .live .peek,
  .live .eye,
  .live .shadow { animation: none; }
}
</style>
