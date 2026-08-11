# 贡献指南

感谢参与短剧技能套件。本项目遵循“知识与做法优先”：能力尽量写入 `SKILL.md` 工作流
和 `references/` 参考资料，脚本只保留字节哈希、稳定索引和清单等确定性工作。

## 修改原则

1. **知识与做法优先**：新增能力先考虑写成参考资料或 `SKILL.md` 工作流步骤；
   只有智能体不应徒手完成的确定性工作（字节级哈希、稳定索引）才写脚本。
   不要把编辑/创作判断写成规则代码。
2. **规则分级**：所有规范都要归入 `structural_invariant` / `reviewed_invariant` /
   `craft_default` / `taste_option` 四级；不得把统一的字数、比例、数量配方设为
   质量门槛。可迁移知识在 `skills/short-drama/references/knowhow-index.md` 注册
   稳定 ID。
3. **所有权与独立审查**：每个产物只有一个负责技能；负责人不能审查自己的产物。
4. **来源边界**：仓库不得包含非公开项目内容、内部标识、私有网址、供应商任务
   或媒体文件；示例一律合成改写。边界测试会检查这些要求。

## 受保护发布检查

普通开发只能证明公开的通用边界；它不能声称已经检查维护者私有词汇或语义近似泄漏。
受保护发布环境必须在仓库外准备每行一项的本地词表，并启用 fail-closed 模式：

```bash
DRAMA_REQUIRE_PRIVATE_RELEASE_GATE=1 \
DRAMA_PRIVATE_TERMS_FILE=/path/outside/repository/release-terms.txt \
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
```

文件缺失或只有注释时，测试必须失败；词表内容、扫描命中和私有来源指纹不得提交或写入
公共日志。精确词扫描只负责明显泄漏，不能证明去复刻。任何由非公开材料晋升的候选还必须
按 maintainer-only `$short-drama-knowhow` 流程，由未看过来源和作者答案的 fresh agent 做
语义 de-copy 盲审；无法取得 fresh 独立上下文时不得发布该候选。

该维护技能故意不在公共 `skills/*` 安装循环或 public manifest 中。维护者需从受控 checkout
显式链接后调用，普通创作者无需安装：

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s "$PWD/maintainers/skills/short-drama-knowhow" \
  "${CODEX_HOME:-$HOME/.codex}/skills/short-drama-knowhow"
```

已存在同名路径时先核对并移除旧链接；不要把此目录移动到公共 `skills/` 或加入套件清单。
盲测 arms、verdict、promotion 证据和回滚记录保存在仓库外的受控工作区或被忽略的
`.omx/evals/`；`maintainers/evals/` 也被忽略，公共测试不得依赖其中的本地评测内容。
受保护 CI 需要检查这些证据时，通过显式的外部路径注入，不能把它们复制回公开 tree。
任何公共规则的 promotion / hold / retire 还要按维护技能的
`references/promotion-ledger.md` 留下去标识事件：绑定合成输入、匿名输出、公共 diff、
独立 reviewer 结论和回滚目标；hash 只能保证字节可重放，不能代替语义审查。

## 修改后必跑

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
ruff check --no-cache .
python3 tools/verify_suite.py skills/short-drama
```

### Python 版本下限

创作者会用自己机器上的解释器跑这些脚本，所以 `skills/*/scripts/*.py` 不得使用高于
声明下限的标准库 API。下限写在每个脚本的 `MINIMUM_PYTHON` 与两份 README 中，测试
会核对三处一致。本机解释器通常比下限新，用不上的 API 不会自己报错，改动前请在下限
版本上实跑一次：

```bash
uv venv --python 3.10 /tmp/floor && \
  PYTHONDONTWRITEBYTECODE=1 /tmp/floor/bin/python -B -m unittest discover -s tests
```

（`datetime.UTC` 需要 3.11、`zip(strict=)` 需要 3.10，都属于本机能跑、下限跑不了的典型。）

改动 `skills/` 下任何文件后，需重建套件清单（会同步重写 8 个 `suite-ref.json`）：

```bash
python3 tools/update_suite_manifest.py skills/short-drama
```

## 更新日志

面向创作者可见的改动要写进 `CHANGELOG.md` 的 `[未发布]` 段。按约束力归类：
`structural_invariant` 与 `reviewed_invariant` 的新增或收紧记为**变更**（可能阻断既有
产物），`craft_default` 与 `taste_option` 记为**新增**（可被创作者覆盖）。修掉会产生
错误产出的问题记为**修复**，并写清原表述错在哪。

只改措辞、补例子或调整排版不必记录。已定位但本次不处理的问题写进**已知缺口**，
不要留在提交信息里。

## 提交约定

- 一个合并请求只聚焦一件事；`SKILL.md` 与配套参考资料放在同一个合并请求中。
- 提交信息说明“哪个技能的哪类知识或确定性工作”发生变化。
- 新增参考文件必须能从负责技能的 `SKILL.md` 按需打开。
