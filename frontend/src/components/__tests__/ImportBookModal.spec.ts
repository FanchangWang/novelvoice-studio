import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ImportPreview } from '@/api/types'

vi.mock('@/api/client', () => ({
  api: {
    listBooks: vi.fn(),
    previewImport: vi.fn(),
    importBook: vi.fn(),
  },
}))

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({ success: vi.fn(), error: vi.fn() }),
  }
})

import { api } from '@/api/client'
import ImportBookModal from '@/components/ImportBookModal.vue'

const listBooks = vi.mocked(api.listBooks)
const previewImport = vi.mocked(api.previewImport)

function preview(): ImportPreview {
  return {
    filename: '123456789_下载器.txt',
    size_bytes: 120,
    sha256: 'a'.repeat(64),
    encoding: 'utf-8',
    encoding_label: 'UTF-8',
    encoding_confidence: 0.99,
    needs_encoding_confirmation: false,
    encoding_candidates: [],
    sample_text: '书名：雾港来信',
    total_chars: 120,
    line_count: 8,
    chapter_count: 1,
    chapter_titles: ['第一章'],
    chapters_truncated: false,
    structure_source: 'rules',
    ai_assisted: false,
    warnings: [],
    duplicate: null,
    metadata: {
      title: '雾港来信',
      author: '林秋',
      description: '一封迟到十年的信。',
      title_source: 'content_label',
      author_source: 'content_label',
      description_source: 'content_label',
      confidence: 0.95,
      warnings: [],
    },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  listBooks.mockResolvedValue({ items: [], total: 0 })
  previewImport.mockResolvedValue(preview())
})

describe('ImportBookModal metadata', () => {
  it('prefills book fields from content metadata instead of the filename', async () => {
    const wrapper = mount(ImportBookModal, {
      props: { show: false },
      global: {
        stubs: {
          NModal: { template: '<div><slot name="header" /><slot /></div>' },
          Modal: { template: '<div><slot name="header" /><slot /></div>' },
          NAlert: { template: '<div><slot /></div>' },
          NButton: { template: '<button><slot /></button>' },
          NInput: {
            props: ['modelValue', 'placeholder', 'type'],
            emits: ['update:modelValue'],
            template:
              '<component :is="type === \'textarea\' ? \'textarea\' : \'input\'" :value="modelValue" :placeholder="placeholder" @input="$emit(\'update:modelValue\', $event.target.value)" />',
          },
          NRadioGroup: { template: '<div><slot /></div>' },
          NRadio: { template: '<label><slot /></label>' },
          NSelect: { template: '<select />' },
          NSpin: { template: '<div />' },
        },
      },
    })

    await wrapper.setProps({ show: true })
    const file = new File(['书名：雾港来信'], '123456789_下载器.txt', { type: 'text/plain' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')
    await flushPromises()

    expect(previewImport).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).toContain('自动提取书名')
    expect(
      (wrapper.find('input[placeholder="自动提取书名，可修改"]').element as HTMLInputElement).value,
    ).toBe('雾港来信')
    expect(
      (wrapper.find('input[placeholder="自动提取，选填"]').element as HTMLInputElement).value,
    ).toBe('林秋')
    expect(wrapper.text()).toContain('来自正文标签')
  })
})
