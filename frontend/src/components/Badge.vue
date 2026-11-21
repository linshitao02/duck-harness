<template>
  <GroupMark v-if="group" :name="name" />
  <DuckIcon v-else-if="duck" :name="name" />
  <svg v-else-if="shape" viewBox="0 0 64 64" class="badge-shape">
    <path :d="shape.d" />
  </svg>
  <svg v-else viewBox="0 0 24 24" class="badge-icon">
    <path v-if="name === 'code'" d="M8 8l-3 4 3 4M16 8l3 4-3 4M13 6l-2 12" />
    <path v-else-if="name === 'trend'" d="M5 16l4-4 3 3 7-8" />
    <path v-else-if="name === 'chip'" d="M8 8h8v8H8zM10 4v4M14 4v4M10 16v4M14 16v4M4 10h4M4 14h4M16 10h4M16 14h4" />
    <path v-else-if="name === 'spider'" d="M12 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 4v2M12 18v2M4 12h2M18 12h2M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2" />
    <path v-else-if="name === 'bot'" d="M8 10h8v7H8zM12 6v3M9 13h.1M15 13h.1M10 16h4" />
    <path v-else-if="name === 'work'" d="M8 9V7h8v2M5 9h14v9H5z" />
    <path v-else-if="name === 'cpp'" d="M8 8l-4 4 4 4M14 8l-2 8M16 10h4M16 14h4" />
    <path v-else-if="name === 'cad'" d="M5 17l7-12 7 12H5z" />
    <path v-else-if="name === 'video'" d="M5 7h9v10H5zM14 11l5-3v8l-5-3" />
    <path v-else-if="name === 'industry'" d="M5 18V9l4 3V9l4 3V7l6 4v7H5z" />
    <path v-else d="M12 3c.8 2.6 1.8 4.2 4.4 5.2C13.8 9.2 12.8 10.8 12 13.4 11.2 10.8 10.2 9.2 7.6 8.2 10.2 7.2 11.2 5.6 12 3z" />
  </svg>
</template>

<script setup>
import { computed } from "vue";
import DuckIcon from "./DuckIcon.vue";
import GroupMark from "./GroupMark.vue";
import { isDuck, isGroup, shapeById } from "../shapes";

const props = defineProps({ name: { type: String, default: "spark" } });
const group = computed(() => isGroup(props.name));
const duck = computed(() => isDuck(props.name));
const shape = computed(() => (duck.value || group.value ? null : shapeById(props.name)));
</script>
