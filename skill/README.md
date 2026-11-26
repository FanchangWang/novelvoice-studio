# UI Design Quality Skill

这是一个面向产品级前端和桌面 Web UI 的 Agent Skill，重点解决 AI 生成页面常见的低对比度、小字号、层级不清、只有深色主题、主题配置不完整，以及没有实际渲染验收等问题。

## 包含内容

- `SKILL.md`：主技能文件，可作为 Agent Skill 安装，也可以直接交给 DeepSeek/本地编程 AI 作为项目级规范。
- `references/vue-naive-ui-theme.md`：Vue 3 + Naive UI 的系统/浅色/深色三态主题实施参考，包括 Pinia store、根 Provider、CSS token、设置页选择器、首屏主题初始化和 Wails 多窗口注意事项。

## 安装方式

把整个 `ui-design-quality` 文件夹放进你所用 Agent 支持的 skills 目录，并保留 `SKILL.md` 与 `references/` 的相对位置。例如：

- Codex / 支持 `.agents/skills` 的工具：`项目根目录/.agents/skills/ui-design-quality/`
- Claude Code：`项目根目录/.claude/skills/ui-design-quality/`
- 其他支持 Agent Skills 目录约定的工具：按其文档放入对应 skills 目录。

不同客户端对 Skill 的自动发现方式并不完全相同。若 DeepSeek 当前使用的客户端不支持自动加载 Skill，可直接把 `SKILL.md` 和 `references/vue-naive-ui-theme.md` 添加为项目上下文/规则文件，或在任务提示中明确要求先完整读取这两个文件。

## 给 DeepSeek 的首次使用提示词

```text
请先完整读取项目中的 ui-design-quality/SKILL.md，并遵守其中的所有硬性验收要求。再读取 references/vue-naive-ui-theme.md。

先检查当前项目结构、页面、主题状态、Naive UI 配置和 Tailwind 版本，不要直接覆盖代码。根据技能要求审查现有页面的可读性和设计层级，然后在保持现有业务功能不变的前提下完成 UI 整改。

强制要求：所有页面必须支持“跟随系统 / 浅色 / 深色”三种主题；默认跟随系统；手动选择必须持久化；跟随系统时必须响应操作系统主题变化。普通正文对比度至少 4.5:1，目标 7:1；正文默认 16px，有意义的文字不得小于 14px。必须检查弹窗、下拉菜单、表格、输入框、提示、空/错误/加载状态和所有窗口。

完成后运行项目检查，并在可用环境中实际渲染浅色、深色及跟随系统两种系统外观，提供验收结果。不要只凭源码宣称“可读性没问题”；没有实际渲染就明确写明未完成视觉验收。
```

## 设计参考来源

本技能是面向当前项目约束独立编写的规则集，并非复制第三方 Skill：

- [UI/UX Pro Max](https://github.com/yuusakuri/agent-skills/tree/main/skills/ui-ux-pro-max)：可作为设计规则、样式和 UX 检查的扩展资料。
- [Frontend Design Skill](https://github.com/PaulRBerg/agent-skills/tree/main/skills/frontend-design)：可参考其先理解项目、形成设计方向、再通过实际渲染验证的工作流。
- [WCAG 2.2 — Contrast (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)：普通文字 4.5:1、大号文字 3:1 的对比度基准。
- [MDN — prefers-color-scheme](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme)：检测操作系统/浏览器的浅色或深色偏好。
