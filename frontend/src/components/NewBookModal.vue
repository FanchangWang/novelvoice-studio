<script setup lang="ts">
import { ref, watch } from 'vue'
import { NAlert, NButton, NInput, NModal, useMessage } from 'naive-ui'

import { api } from '@/api/client'
import { errorMessage } from '@/utils/errors'

const show = defineModel<boolean>('show', { default: false })
const emit = defineEmits<{ created: [bookId: string] }>()
const message = useMessage()

const title = ref('')
const author = ref('')
const description = ref('')
const saving = ref(false)
const error = ref<string | null>(null)

function reset(): void {
  title.value = ''
  author.value = ''
  description.value = ''
  saving.value = false
  error.value = null
}

async function submit(): Promise<void> {
  const cleanedTitle = title.value.trim()
  if (!cleanedTitle) {
    error.value = '请填写书名。'
    return
  }
  saving.value = true
  error.value = null
  try {
    const book = await api.createBook({
      title: cleanedTitle,
      author: author.value.trim() || null,
      description: description.value.trim() || null,
    })
    message.success(`已创建空项目《${book.title}》`)
    emit('created', book.id)
    show.value = false
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    saving.value = false
  }
}

watch(show, (open) => {
  if (!open) {
    reset()
  }
})
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    title="新建空项目"
    :bordered="false"
    :mask-closable="!saving"
    :closable="!saving"
    :style="{ width: '520px', maxWidth: 'calc(100vw - 32px)' }"
  >
    <div class="space-y-5">
      <p class="text-xs leading-6 text-fog-400">
        空项目只建立书籍目录，不写入正文。之后可在书库中导入 TXT，或等待阶段 2 的章节编辑功能。
      </p>

      <NAlert v-if="error" type="error" :bordered="false">{{ error }}</NAlert>

      <label class="block">
        <span class="mb-2 block text-xs font-medium text-fog-200">
          书名 <span class="text-brass-400">*</span>
        </span>
        <NInput
          v-model:value="title"
          placeholder="例如：长夜将明"
          :maxlength="255"
          @keyup.enter="submit"
        />
      </label>

      <label class="block">
        <span class="mb-2 block text-xs font-medium text-fog-200">作者</span>
        <NInput v-model:value="author" placeholder="选填" :maxlength="255" />
      </label>

      <label class="block">
        <span class="mb-2 block text-xs font-medium text-fog-200">简介</span>
        <NInput
          v-model:value="description"
          type="textarea"
          placeholder="选填，最多 5000 字"
          :maxlength="5000"
          :autosize="{ minRows: 3, maxRows: 6 }"
        />
      </label>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <NButton :disabled="saving" @click="show = false">取消</NButton>
        <NButton type="primary" :loading="saving" @click="submit">创建项目</NButton>
      </div>
    </template>
  </NModal>
</template>