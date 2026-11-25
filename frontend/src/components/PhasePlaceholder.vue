<script setup lang="ts">
import { RouterLink } from 'vue-router'

defineProps<{
  eyebrow: string
  title: string
  stage: string
  description: string
  bullets: string[]
}>()
</script>

<template>
  <div class="flex min-h-0 flex-1 items-center justify-center overflow-y-auto px-8 py-14">
    <div class="grid w-full max-w-5xl gap-12 lg:grid-cols-[1.25fr_0.75fr] lg:items-center">
      <section>
        <p class="eyebrow">{{ eyebrow }}</p>
        <h1 class="mt-4 max-w-xl font-display text-4xl leading-[1.18] text-fog-100 md:text-5xl">
          {{ title }}
        </h1>
        <p class="mt-5 max-w-xl text-sm leading-7 text-fog-300">{{ description }}</p>

        <ul class="mt-8 space-y-3">
          <li
            v-for="(item, index) in bullets"
            :key="item"
            class="flex items-start gap-3 text-sm text-fog-400"
          >
            <span
              class="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-ink-600 font-mono text-[10px] text-brass-400"
            >
              {{ index + 1 }}
            </span>
            <span class="leading-6">{{ item }}</span>
          </li>
        </ul>

        <div class="mt-9 flex items-center gap-3">
          <RouterLink
            to="/library"
            class="nv-focus inline-flex items-center gap-2 rounded-lg border border-brass-600/50 bg-brass-500/10 px-4 py-2 text-sm font-medium text-brass-300 transition-colors hover:border-brass-500 hover:bg-brass-500/15"
          >
            返回书库
            <span aria-hidden="true">→</span>
          </RouterLink>
          <span class="text-xs text-fog-500">先把阶段 1 的书稿基础用起来。</span>
        </div>
      </section>

      <aside class="nv-card relative overflow-hidden rounded-2xl p-7">
        <div class="absolute -right-16 -top-20 h-48 w-48 rounded-full bg-brass-500/10 blur-3xl" />
        <p class="eyebrow relative">Roadmap</p>
        <p class="relative mt-4 font-display text-6xl text-brass-400/90">{{ stage }}</p>
        <div class="relative mt-5 h-px w-full hairline" />
        <dl class="relative mt-6 space-y-5 text-sm">
          <div>
            <dt class="text-[10px] uppercase tracking-[0.2em] text-fog-500">当前可用</dt>
            <dd class="mt-1.5 text-fog-200">TXT 导入 · 编码识别 · 章节解析 · Mock Provider</dd>
          </div>
          <div>
            <dt class="text-[10px] uppercase tracking-[0.2em] text-fog-500">待接入</dt>
            <dd class="mt-1.5 text-fog-400">{{ title }}</dd>
          </div>
          <div>
            <dt class="text-[10px] uppercase tracking-[0.2em] text-fog-500">本页状态</dt>
            <dd class="mt-1.5 flex items-center gap-2 text-fog-400">
              <span class="h-1.5 w-1.5 rounded-full bg-fog-600" />
              占位页面，尚未开放
            </dd>
          </div>
        </dl>
      </aside>
    </div>
  </div>
</template>