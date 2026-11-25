import { describe, expect, it } from 'vitest'

import {
  formatBytes,
  formatDateTime,
  formatNumber,
  formatSettingValue,
  selectionIsContiguous,
  truncate,
} from '@/utils/format'

describe('format helpers', () => {
  it('formats numbers and byte sizes for the UI', () => {
    expect(formatNumber(1234567)).toBe('1,234,567')
    expect(formatNumber(undefined)).toBe('0')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB')
    expect(formatBytes(-1)).toBe('—')
  })

  it('formats timestamps and rejects invalid input', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime('not-a-date')).toBe('—')
    expect(formatDateTime('2026-11-25T09:20:00Z')).toMatch(/\d{4}/)
  })

  it('truncates long text without breaking short text', () => {
    expect(truncate('短文本', 10)).toBe('短文本')
    expect(truncate('一'.repeat(20), 10)).toBe(`${'一'.repeat(10)}…`)
    expect(truncate('a\n  b', 10)).toBe('a b')
  })

  it('formats unknown setting values safely', () => {
    expect(formatSettingValue(false)).toBe('关闭')
    expect(formatSettingValue(true)).toBe('开启')
    expect(formatSettingValue('')).toBe('—')
    expect(formatSettingValue([])).toBe('—')
    expect(formatSettingValue(['a', 'b'])).toBe('a、b')
    expect(formatSettingValue({ a: 1 })).toBe('{"a":1}')
  })

  it('only accepts contiguous chapter selections for merge', () => {
    const chapters = [
      { id: 'a', sequence: 1 },
      { id: 'b', sequence: 2 },
      { id: 'c', sequence: 3 },
    ]
    expect(selectionIsContiguous(chapters, ['b', 'c'])).toBe(true)
    expect(selectionIsContiguous(chapters, ['a', 'b', 'c'])).toBe(true)
    expect(selectionIsContiguous(chapters, ['a', 'c'])).toBe(false)
    expect(selectionIsContiguous(chapters, ['a'])).toBe(false)
    expect(selectionIsContiguous(chapters, ['a', 'missing'])).toBe(false)
  })
})