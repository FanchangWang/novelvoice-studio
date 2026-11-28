import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ScriptUnitOut } from '@/api/types'
import ChapterPlaylist from '@/components/production/ChapterPlaylist.vue'

function unit(
  id: string,
  overrides: Partial<ScriptUnitOut> = {},
): ScriptUnitOut {
  return {
    id,
    book_id: 'book_1',
    chapter_id: 'ch_1',
    script_version_id: 'script_1',
    scene_id: null,
    unit_index: 0,
    unit_type: 'narration',
    speaker_type: 'narrator',
    speaker_id: null,
    speaker_name: '旁白',
    character_state_id: null,
    voice_cast_id: null,
    voice_actor_id: null,
    voice_actor_name: null,
    source_text: `原文 ${id}`,
    text: `语音 ${id}`,
    display_text: null,
    source_start_offset: 0,
    source_end_offset: 3,
    emotion: 'calm',
    intensity: 1,
    delivery: 'normal',
    tts_instruction: null,
    speed: 1,
    pause_before_ms: 0,
    pause_after_ms: 0,
    sfx: {},
    review_status: 'auto',
    confidence: 1,
    audio_status: 'ready',
    current_audio_asset_id: `audio_${id}`,
    render_hash: null,
    user_locked_fields: [],
    evidence: [],
    source: 'ai',
    created_at: '2026-11-27T00:00:00Z',
    updated_at: '2026-11-27T00:00:00Z',
    ...overrides,
  }
}

describe('ChapterPlaylist', () => {
  beforeEach(() => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('warns about missing and stale audio instead of claiming a complete preview', () => {
    const wrapper = mount(ChapterPlaylist, {
      props: {
        units: [
          unit('unit_1', { audio_status: 'stale' }),
          unit('unit_2', { unit_type: 'dialogue', audio_status: 'none', current_audio_asset_id: null }),
          unit('unit_3', { unit_type: 'sfx', audio_status: 'not_applicable', current_audio_asset_id: null, text: '' }),
        ],
      },
    })

    expect(wrapper.text()).toContain('1 / 2 段可播放')
    expect(wrapper.text()).toContain('1 段尚未生成可用音频')
    expect(wrapper.text()).toContain('1 段使用旧输入生成的音频')
    expect(wrapper.text()).toContain('不合成音效')
  })

  it('plays existing TTS units in order', async () => {
    vi.useFakeTimers()
    const wrapper = mount(ChapterPlaylist, {
      props: { units: [unit('unit_1'), unit('unit_2', { unit_index: 1 })] },
    })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    const audio = wrapper.get('audio').element as HTMLAudioElement
    expect(audio.src).toContain('/script-units/unit_1/audio')
    expect(wrapper.text()).toContain('当前第 1 / 2 段')

    audio.dispatchEvent(new Event('ended'))
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    expect(audio.src).toContain('/script-units/unit_2/audio')
    expect(wrapper.text()).toContain('当前第 2 / 2 段')

    audio.dispatchEvent(new Event('ended'))
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.text()).toContain('从头试听')
    expect(audio.hasAttribute('src')).toBe(false)
  })

  it('does not show a loading error caused by stopping playback', async () => {
    const wrapper = mount(ChapterPlaylist, {
      props: { units: [unit('unit_1')] },
    })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.findAll('button')[1].trigger('click')
    wrapper.get('audio').element.dispatchEvent(new Event('error'))
    expect(wrapper.text()).not.toContain('音频加载失败')
  })
})
