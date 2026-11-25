import { describe, expect, it } from 'vitest'

import {
  EMOTION_LABELS,
  INTENSITY_LABELS,
  SEGMENT_TYPE_LABELS,
  isChapterAnalyzed,
  isJobActive,
  labelOf,
  percent,
} from '@/utils/analysis'

describe('analysis labels', () => {
  it('maps known values and falls back to the raw value', () => {
    expect(labelOf(SEGMENT_TYPE_LABELS, 'inner_monologue')).toBe('内心独白')
    expect(labelOf(EMOTION_LABELS, 'sarcastic')).toBe('讽刺')
    expect(labelOf(EMOTION_LABELS, 'custom_emotion')).toBe('custom_emotion')
    expect(labelOf(EMOTION_LABELS, null)).toBe('—')
    expect(INTENSITY_LABELS[3]).toBe('强')
  })

  it('formats confidence percentages', () => {
    expect(percent(0.603)).toBe('60%')
    expect(percent(1)).toBe('100%')
    expect(percent(null)).toBe('—')
  })

  it('identifies analyzed chapters and active jobs', () => {
    expect(isChapterAnalyzed('completed')).toBe(true)
    expect(isChapterAnalyzed('needs_review')).toBe(true)
    expect(isChapterAnalyzed('failed')).toBe(false)
    expect(isChapterAnalyzed('not_started')).toBe(false)
    expect(isJobActive('pending')).toBe(true)
    expect(isJobActive('running')).toBe(true)
    expect(isJobActive('completed')).toBe(false)
    expect(isJobActive(null)).toBe(false)
  })
})