<template>
  <div class="text">
    <template v-for="(part, index) in parts" :key="index">
      <button v-if="part.member" type="button" class="mention" @click="chat.select(part.member.id)">
        <span class="badge" :style="badgeStyle(part.member.icon_key, part.member.icon_color)">
          <Badge :name="part.member.icon_key" />
        </span>
        <span>{{ part.name }}</span>
      </button>
      <span v-else v-html="renderText(part.value)" />
    </template>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { renderText } from "../api";
import { badgeStyle } from "../shapes";
import { useChatStore } from "../store";
import Badge from "./Badge.vue";

const props = defineProps({ value: { type: String, default: "" } });
const chat = useChatStore();

const targets = computed(() => {
  const active = chat.active;
  if (!active) return [];
  const roomId = active.kind === "group" ? active.id : active.room_id;
  if (!roomId) return [];
  return chat.conversations
    .filter((item) => item.room_id === roomId)
    .map((item) => ({ item, name: (item.member_name || item.title || "").trim() }))
    .filter((item) => item.name)
    .sort((a, b) => b.name.length - a.name.length);
});

const parts = computed(() => splitMentions(props.value, targets.value));

function splitMentions(value, names) {
  const text = String(value || "");
  if (!names.length) return [{ value: text }];
  const parts = [];
  let index = 0;
  while (index < text.length) {
    if (text.startsWith("```", index)) {
      const end = text.indexOf("```", index + 3);
      const stop = end < 0 ? text.length : end + 3;
      parts.push({ value: text.slice(index, stop) });
      index = stop;
      continue;
    }
    if (text[index] === "`") {
      const end = text.indexOf("`", index + 1);
      const stop = end < 0 ? index + 1 : end + 1;
      parts.push({ value: text.slice(index, stop) });
      index = stop;
      continue;
    }
    if (text[index] === "@" || text[index] === "＠") {
      const rest = text.slice(index + 1);
      const hit = names.find((entry) => rest.startsWith(entry.name));
      if (hit) {
        parts.push({ member: hit.item, name: hit.name });
        index += 1 + hit.name.length;
        continue;
      }
    }
    let next = index + 1;
    while (next < text.length && text[next] !== "@" && text[next] !== "＠" && text[next] !== "`") next += 1;
    parts.push({ value: text.slice(index, next) });
    index = next;
  }
  return parts.filter((part) => part.member || part.value);
}
</script>
