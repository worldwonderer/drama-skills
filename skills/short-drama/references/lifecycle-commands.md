# 项目命令与审核记录

只在实际调用 `project_tool.py`、诊断命令失败或核对审核记录时读取本文。
从 `short-drama` 技能安装目录调用脚本，不依赖当前工作目录：

```text
python3 <short-drama-skill-dir>/scripts/project_tool.py init <project> --title <title>
python3 <short-drama-skill-dir>/scripts/project_tool.py status <project>
python3 <short-drama-skill-dir>/scripts/project_tool.py recover <project>
python3 <short-drama-skill-dir>/scripts/project_tool.py publish <project> --owner short-drama-write --artifact-id EP001:script --output episodes/EP001/screenplay.md=inputs/EP001-screenplay.candidate.md [--input <upstream-path>=<sha256> ...]
python3 <short-drama-skill-dir>/scripts/project_tool.py accept <project> --artifact-id EP001:script --decision accepted --target episodes/EP001/screenplay.md=<candidate-sha256> --evidence-artifact creator-decisions.jsonl --evidence-hash <decision-file-sha256> --evidence-record-id <decision-id>
python3 <short-drama-skill-dir>/scripts/project_tool.py review <project> --artifact-id EP001:script --verdict approve --target episodes/EP001/screenplay.md=<accepted-sha256> --verdict-owner short-drama-review --verdict-artifact reviews/EP001-verdict.json --verdict-hash <verdict-file-sha256>
python3 <short-drama-skill-dir>/scripts/project_tool.py package <project> --episode EP001 --include <accepted-path> [...]
```

## 发布与创作者确认

`publish --output <target>=<source>` 可以重复使用；来源文件必须是项目内的 UTF-8
Markdown、JSON 或 JSONL。命令把来源文件和 `--input` 依赖的准确路径与 `hash` 写入
预写日志，只发布 `candidate`，且只检查文件格式；`validation_state` 保持 `not_run`，不能同时写入创作者确认
或独立审查结论。

`accept` 使用创作者决定记录，把所有准确的 `candidate` 目标 `hash` 推进为
`accepted`；记录的负责人固定为 `creator`。JSONL 记录必须用
`--evidence-record-id` 唯一定位同名 `decision_id`；JSON 证据必须是对象；所定位记录的
`status` 或 `decision` 必须与命令的 `accepted/rejected` 一致。用于产物生命周期的记录
还必须声明 `decision_kind:"artifact_acceptance"`、当前 `artifact_id` 和与全部 `--target`
完全相同的 `target_hashes`；其他已接受决定不能代替本次接受。

## 独立审查记录

`review` 的审查结论 JSON 必须列出同一组结构化的受审 `ArtifactRef`。`reviewer` 至少包含
与审查结论负责人一致的 `owner`、`kind`、`independent:true`，并在
`excluded_owner_skills` 中准确排除被审文件的负责人。`findings_ref` 必须由审查者所有，
绑定当前有效的 `hash`，并指向可解析的 JSONL；其中所有未关闭的致命、错误或阻断问题 ID 必须与 `blocking_findings` 完全一致，
`open_blocker_count` 再与之对齐。

`structural_validation` 必须是 `pass | pass_with_warnings | fail`，并由这份准确的审查结论
更新校验状态；结构校验未通过或仍有阻断问题时不能批准。后续目标文件或任一
审核记录的 `hash` 改变，都要重新确认或审查。

## 过期影响与依赖检查

接受时把 `candidate` 的准确输入清单保存为 `accepted_inputs`。发布新 `candidate` 时，
同一预写日志清单会找出直接和间接受影响的下游文件：保留旧的创作者确认记录，
但把受影响的下游构建状态标为 `stale`，清空校验与审查就绪状态，并阻止交付。

`review` 和 `package` 会逐层复验输入的当前 `hash`、唯一且状态为 `accepted` 的提供方，
以及提供方本身的构建、确认状态和输入。外部编辑、循环或含糊依赖不能靠手改状态字符串
绕过。若多文件产物的新 `candidate` 不再包含旧的 `accepted/candidate` 目标，该路径也会
被列入受影响的下游清单；旧文件不会被静默删除，但新版本接受后，它不再拥有已接受权限，
也不能被单独打包。

`publish` 会读取 JSON 或 JSONL 候选文件中带 `owner/artifact/hash` 的引用：
指向同次输出时，`hash` 必须匹配该候选文件内容；其他引用必须以相同路径和 `hash` 出现在
`--input`。遗漏或不一致会在写预写日志前被拒绝；Markdown 依赖无法可靠推断，仍必须由
负责人明确声明。

## 恢复与打包

`recover --transaction <txid>` 只处理指定事务。`package` 会重新验证状态文件中保存的创作者
决定和独立审查记录，只打包当前 `hash` 与已接受快照一致、并且各项交付状态都已就绪的
Markdown、JSON 或 JSONL。故事中确实需要交付屏显网址或屏显机器路径时，要有明确的例外
文件，绑定准确的文字、路径、字段、来源和文字呈现方法；其他网址与机器路径默认阻断。
例外只释放它逐字声明的那一个字符串：路径必须写到完整的那一条，只写盘符或目录开头会被
拒绝，整段文档也不能当作一条例外。文件协议网址、私钥与结构化凭据字段无条件阻断，
没有例外通道。

每条例外必须写齐七个字段，缺一即整体拒绝：`exact_text`（逐字原文）、`path`（绑定到哪个
交付文件）、`field`（该文字在产物中的字段位置）、`purpose`（固定为 `on_screen_text`）、
`provenance`（`creator_supplied` 或 `story_world_authored`）、`text_policy`
（`visible_on_screen` 或 `fictional_interface_text`）、`allow_delivery`（必须为 `true`）。
