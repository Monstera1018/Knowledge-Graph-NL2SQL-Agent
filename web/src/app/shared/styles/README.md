# 全局样式（`styles.css` 引入）

| 文件 | 类名前缀 | 用途 |
|------|----------|------|
| `app-btn.css` | `app-btn` | 描边工具按钮（工具栏、详情、图谱、确认框） |
| `app-search.css` | `app-search` | 内嵌搜索框 |
| `app-chip.css` | `app-chip` | 等宽小标签（列名等） |
| `app-tooltip.css` | `app-tooltip` | Tooltip 皮肤；`appTruncateTooltip` 指令自动加 `--truncate` |
| `workbench-layout.css` | `workbench` | 分栏工作台布局（目录、知识等列表+详情） |
| `entity-form.css` | `detail-card`、`entity-form`、`detail-desc` | 详情卡片与编辑表单 |
| `app-feedback.css` | — | Toast、确认框、业务弹框 panel 类 |

组件私有样式保留在各自 `*.component.css`；页面仅放该页特有规则（如 `catalog.page.css` 的枚举编辑）。
