# Vue 3 + Naive UI 三态主题实施参考

本文件是本项目 `frontend/` 的实际实现，可作为同类 Vue 3 + Naive UI 项目的最小参考。核心原则：**一个 store、一份调色板、一套 CSS variables，三种模式共用**。

## 文件布局

```text
frontend/
  index.html                 首屏前主题初始化（防闪白/闪黑）
  src/theme/palette.ts       两种实际外观的调色板 + Naive UI overrides
  src/stores/theme.ts        system / light / dark 状态、持久化、系统监听
  src/App.vue                根 NConfigProvider 接线
  src/styles.css             与 palette 同值的 CSS variables
  src/stores/__tests__/theme.spec.ts
```

## 1. 存储与解析

- 存储键统一为 `novelvoice.theme-mode`，值为 `system | light | dark`，缺省 `system`。
- 实际外观只有 `light | dark`（`ResolvedTheme`）；`system` 是输入模式，不参与样式。
- `localStorage` 读写包 try/catch：隐私模式或 WebView 禁用存储时不能抛错。

## 2. Store（Pinia）

```ts
const mode = ref<ThemeMode>(readStoredMode())
const systemDark = ref(systemPrefersDark())
const resolvedTheme = computed(() =>
  mode.value === 'system' ? (systemDark.value ? 'dark' : 'light') : mode.value,
)
watch(mode, persist, { immediate: false })
watch(resolvedTheme, applyTheme, { immediate: true })
```

- 用 `matchMedia('(prefers-color-scheme: dark)')` 的 `change` 事件更新 `systemDark`；旧 WebView 回退 `addListener`。
- `applyTheme` 做三件事：`documentElement.dataset.theme`、`style.colorScheme`、更新 `<meta name="theme-color">`。
- 不要在组件里各自读 `prefers-color-scheme`；只允许 store 解析。

## 3. 调色板与 Naive UI

`palette.ts` 导出 `THEME_PALETTES: Record<'light'|'dark', ThemePalette>`，字段是语义名而非色值名：`appBg / surface1..3 / surfaceHover / textPrimary / textSecondary / textMuted / border / borderStrong / accent / accentHover / accentPressed / onAccent / focusRing / success / warning / danger / info / scrollbarThumb`。

`buildThemeOverrides(theme)` 把调色板映射成 `GlobalThemeOverrides`，至少覆盖：

- `common`：`bodyColor`、`cardColor`、`modalColor`、`popoverColor`、`tableColor`、`inputColor`、文本三级色、边框色、主色与 hover/pressed、`textColorDisabled`。
- 组件级：`Button`、`Card`、`Input`、`Select`、`DataTable`、`Dialog`、`Popover`、`Tooltip`、`Tabs`、`Tag`、`Menu`、`Scrollbar`。

要点：Naive UI 需要具体色值推导状态色，不能只传 CSS 变量名；`palette.ts` 与 `styles.css` 必须手工保持同值，改动时两处一起改。

## 4. 根 Provider

```vue
<NConfigProvider :theme="dark ? darkTheme : lightTheme" :theme-overrides="overrides" :locale="zhCN">
  <NDialogProvider><NMessageProvider>…</NMessageProvider></NDialogProvider>
</NConfigProvider>
```

`darkTheme`/`lightTheme` 来自 `naive-ui`；`theme-overrides` 始终是同一份映射函数的结果，不要为深色另写一套组件尺寸或布局。

## 5. CSS variables

`styles.css` 在 `:root`（浅色）和 `[data-theme='dark']`（深色）各定义一次 `--app-bg / --surface-1..3 / --surface-hover / --text-* / --border* / --accent* / --success / --warning / --danger / --info / --scrollbar-*`。所有页面、弹窗、表格、空状态、错误状态都消费变量；组件内不写死十六进制色值（半透明遮罩除外，需在两种外观下都检查）。

## 6. 首屏初始化

`index.html` 内联脚本在 CSS/JS 加载前读取同一存储键、计算 `resolvedTheme`，设置 `data-theme` 与 `color-scheme`，并更新 `theme-color`；失败时回退 `light`。键名和默认值与 store 必须完全一致，否则会闪一下再跳变。

## 7. 验收

- 单测覆盖：默认 `system`、持久化、非法值回退、系统外观变化、`applyTheme` 的三个副作用。
- 实际渲染三种模式各截一次：列表页、表单/弹窗、空状态、错误状态、长表格、滚动条。
- 对比度按最终 CSS/palette 取色计算，普通正文 ≥ 4.5:1；正文默认 16px，有意义的元数据不小于 14px。
- 只改源码不做真实渲染，只能写“未完成视觉验收”。
