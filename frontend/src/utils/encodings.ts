export interface EncodingOption {
  label: string
  value: string
}

/** 后端 text_io.SUPPORTED_ENCODINGS 的白名单副本（展示顺序按常见程度）。 */
export const ENCODING_OPTIONS: readonly EncodingOption[] = [
  { label: 'UTF-8', value: 'utf-8' },
  { label: 'UTF-8 (BOM)', value: 'utf-8-sig' },
  { label: 'GB18030', value: 'gb18030' },
  { label: 'GBK', value: 'gbk' },
  { label: 'GB2312', value: 'gb2312' },
  { label: 'Big5', value: 'big5' },
  { label: 'UTF-16', value: 'utf-16' },
  { label: 'UTF-16 LE', value: 'utf-16-le' },
  { label: 'UTF-16 BE', value: 'utf-16-be' },
]