---
name: short-drama-image-prompts
description: 为短剧人物、造型、地点、道具和状态编写或修改可直接复制的图片提示词 Markdown。用户提到角色设定图、三视图、参考图、场景板、道具图、风格帧、Look Development、状态变体或局部编辑提示词时使用；不生成图片，也不调用供应商。
license: MIT
---

# 短剧资产图片提示词

把视觉事实写成可复用、可修改、可直接复制的角色板、场景板、道具板或状态图提示词，统一保存到
`剧集/<EP>/图片提示词.md`。每项用 `IMG-...` 标题，正文放在 `### 可复制提示词` 引用块。
`IMG-...` 只是该提示词条目的稳定 ID，不声明图片已生成或可用；实际图片只能来自创作者已提供的输入，或经确认后的生产结果。

## Quick Start

```text
用 $short-drama-image-prompts 为 EP001 已确定的人物、地点和道具写可直接复制的图片提示词
```

## 入口

有当前视觉设定即可直接开始；剧本只在提示词需要确认剧情状态时读取。Look Development 是可选分支。
先确认用途：身份板、造型/状态变体、地点板、道具板、组合 production sheet 或比较风格帧。

创作者可读说明跟随项目语言；可复制正文跟随 `short-drama.json#/format/prompt_language`。没有
`short-drama.json` 时正文默认
使用 `en`，并在同一任务中保持一致；不能从创作者说明语言推断提示词语言。

## 工作流

1. 锁定这一张图要固定的身份、状态、空间或比较变量。
2. 只读所需资产事实、视觉方向、负面约束和已提供参考图。
3. 按“主体与身份锚点 → 当前变体 → 构图/视角 → 光色/材质 → 背景边界 → 禁止项”写正文。
4. 每张参考只控制身份、造型、地理、构图或风格中的明确部分。
5. 检查身份与变体、文字政策、视角和光线是否冲突。
6. 用户要全部资产就完成全部，资产组只是内部批次。

## 提示词要求

- 开头先写对象和用途，不用风格词淹没身份。
- 只包含当前图能同时满足的要求；多视图/状态对照写清版面关系。
- 保留稳定识别锚点，变体只改允许变化的部分。
- 被《视觉设定.md》连续性锁点名的条目，锁面原样出现在可复制正文里，让这张参考图本身就把跨镜事实定住。
- 避免无验证作用的质量词堆砌。
- 可见文字、logo、水印、界面和字幕明确允许或禁止。
- 正文可直接复制，不含占位符、流程说明、文件路径或 QA 结论。

## 按需知识

默认只读本 SKILL 和当前视觉设定。遇到对应问题时只打开一份：

- 阶段边界与规则分级：[阶段契约](references/stage-contract.md)
- 普通单图的最小配方：[通用配方](references/common-recipe.md)
- 人物身份板与造型一致性：[人物与造型](references/character-and-look.md)
- 地点地理、视角和光线：[地点板](references/location-plate.md)
- 功能道具、尺度、材质与文字：[道具板](references/prop-plate.md)
- 造型和状态变体：[造型与状态变体](references/look-and-state-variant.md)
- 多对象组合板：[Production Sheet 配方](references/production-sheet-recipes.md)
- 比较视觉方向的代表帧：[Lookdev 风格帧](references/lookdev-frame.md)
- 局部修改和 preserve set：[定点修改](references/edit-and-revision.md)
- 完成前的可生成性检查：[审查与示例](references/review-and-fixtures.md)

## 完成与投产

每个点名对象都有明确用途、可复制正文、参考边界和禁止项，且相互不矛盾，即完成。实际生成必须
转 `$short-drama-produce`，展示精确任务并取得显式确认；本技能不调用外部服务。
五份创作文档齐备后，可转 `$short-drama` 对跨文档结构做一次机械核对；内容质量仍由创作者审查。

## 安装维护

只有安装、升级或排障时运行 `python3 scripts/selftest.py`。
