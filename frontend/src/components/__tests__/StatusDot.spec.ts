import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusDot from '@/components/StatusDot.vue'

describe('StatusDot', () => {
  it('renders the ok tone and small size', () => {
    const wrapper = mount(StatusDot, { props: { status: 'ok', size: 'sm' } })
    const root = wrapper.get('[data-status="ok"]')
    expect(root.classes()).toContain('text-moss-400')
    expect(root.classes()).toContain('h-4')
  })

  it('renders an error pulse when requested', () => {
    const wrapper = mount(StatusDot, {
      props: { status: 'error', pulse: true },
    })
    const root = wrapper.get('[data-status="error"]')
    expect(root.classes()).toContain('text-rust-400')
    expect(root.find('.animate-ping').exists()).toBe(true)
  })

  it('defaults to the idle tone', () => {
    const wrapper = mount(StatusDot, { props: { status: 'idle' } })
    expect(wrapper.get('[data-status="idle"]').classes()).toContain('text-fog-500')
  })
})