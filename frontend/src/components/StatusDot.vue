<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    status: 'ok' | 'warn' | 'error' | 'idle'
    pulse?: boolean
    size?: 'sm' | 'md'
  }>(),
  { pulse: false, size: 'md' },
)

const toneClass = computed(() => {
  switch (props.status) {
    case 'ok':
      return 'bg-moss-400/20 text-moss-400'
    case 'warn':
      return 'bg-brass-400/20 text-brass-400'
    case 'error':
      return 'bg-rust-400/20 text-rust-400'
    default:
      return 'bg-fog-500/20 text-fog-500'
  }
})

const dotClass = computed(() => {
  switch (props.status) {
    case 'ok':
      return 'bg-moss-400'
    case 'warn':
      return 'bg-brass-400'
    case 'error':
      return 'bg-rust-400'
    default:
      return 'bg-fog-500'
  }
})
</script>

<template>
  <span
    class="relative inline-flex shrink-0 items-center justify-center rounded-full"
    :class="[toneClass, size === 'sm' ? 'h-4 w-4' : 'h-5 w-5']"
    :data-status="status"
  >
    <span
      v-if="pulse"
      class="absolute inset-0 animate-ping rounded-full opacity-40"
      :class="dotClass"
    />
    <span
      class="relative rounded-full"
      :class="[dotClass, size === 'sm' ? 'h-1.5 w-1.5' : 'h-2 w-2']"
    />
  </span>
</template>