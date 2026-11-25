<script setup lang="ts">
withDefaults(
  defineProps<{
    name: 'library' | 'voices' | 'jobs' | 'settings'
    size?: number
  }>(),
  { size: 20 },
)
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <template v-if="name === 'library'">
      <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v18H6.5A1.5 1.5 0 0 1 5 19.5v-15Z" />
      <path d="M8.5 3v18" />
      <path d="M12 8h3.5" />
    </template>
    <template v-else-if="name === 'voices'">
      <path d="M3 12h2.2l1.7-5.4L9.6 18l2.1-9.8L13.9 14H21" />
    </template>
    <template v-else-if="name === 'jobs'">
      <path d="M5 6.5h14" />
      <path d="M5 12h9.5" />
      <path d="M5 17.5h6.5" />
      <path d="M18.2 16.2v-4.4l2.1 3.3" />
    </template>
    <template v-else>
      <path
        d="M12 8.4a3.6 3.6 0 1 0 0 7.2 3.6 3.6 0 0 0 0-7.2Z"
      />
      <path
        d="M19.2 13.2a7.5 7.5 0 0 0 0-2.4l1.8-1.3-1.9-3.2-2.2.7a7.7 7.7 0 0 0-2-1.2L14.6 3h-3.8L10.4 5.8a7.7 7.7 0 0 0-2 1.2l-2.2-.7-1.9 3.2 1.8 1.3a7.5 7.5 0 0 0 0 2.4l-1.8 1.3 1.9 3.2 2.2-.7a7.7 7.7 0 0 0 2 1.2l.4 2.8h3.8l.4-2.8a7.7 7.7 0 0 0 2-1.2l2.2.7 1.9-3.2-1.8-1.3Z"
      />
    </template>
  </svg>
</template>