# 本地 issue tracker 约定(本 feature 目录)

本仓库未配置 GitHub/其他 issue tracker,按 wayfinder skill 默认使用 local-markdown tracker:
issues 以文件形式存于 `.scratch/<feature>/` 下。

- **map** = `map.md`,frontmatter 带 `labels: [wayfinder:map]`
- **ticket** = `tNN-*.md`,frontmatter 带 `labels: [wayfinder:<type>]`、`parent: map.md`、`blocked_by: [文件名列表]`
- **认领** = 把 `assignee` 写入 frontmatter(空即未认领)
- **关闭** = `state: closed` + 追加 `## Resolution` 段记录答案(等价于 tracker 的 resolution comment)
- **frontier** = `state: open`、`assignee` 为空、且 `blocked_by` 中的文件全部 `state: closed` 的 ticket
