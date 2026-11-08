# 运行方式

> 证据等级：`[已验证]` 本次文档生成时实际执行成功；`[配置中]` 来自清单/脚本参数/README 的固定配置；`[未验证]` 来自 README 或推断，未实际执行。验证环境：macOS，2026-11-09。

## 目录

- [环境要求](#环境要求)
- [安装](#安装)
- [构建与启动](#构建与启动)
- [数据布局](#数据布局)
- [配置](#配置)
- [建库与使用](#建库与使用)
- [直接运行脚本](#直接运行脚本)
- [测试与演练](#测试与演练)
- [常见问题](#常见问题)
- [发布流程](#发布流程)

## 环境要求

| 项 | 要求 | 依据 | 状态 |
| --- | --- | --- | --- |
| Claude Code | ≥ 2.1.275 | [README.md:31](../../README.md#L31) | `[已验证]` 本机 2.1.299 |
| Python | ≥ 3.9，无第三方包 | [README.md:47](../../README.md#L47) | `[已验证]` 本机 3.13.12 |
| poppler | 提供 `pdftotext`、`pdftoppm`、`pdfimages` | [README.md:48](../../README.md#L48) | `[已验证]` 本机 26.04.0 |
| markitdown | 处理 pptx/docx/xlsx/html | [README.md:49](../../README.md#L49) | `[已验证]` 命令存在 |
| 操作系统 | 只在 macOS 上验证过 | [README.md:74](../../README.md#L74) | `[未验证]` 其他平台 |

缺失工具不会静默失败：脚本会返回带安装提示的错误（[scripts/kb.py:120-134](../../scripts/kb.py#L120-L134)）。

## 安装

### 正式安装（推荐）

```bash
claude plugin marketplace add Huangyu-Shen/deep-tutor-with-claude-code
claude plugin install tutor@deep-tutor-with-claude-code
```

来源：[README.md:33-36](../../README.md#L33-L36)。`[未验证]` 本次未执行 marketplace 安装，只验证了本地运行路径。

### 本地开发模式

在**学习文件夹**中启动，而不是插件仓库内：

```bash
claude --plugin-dir /path/to/deep-tutor-with-claude-code
```

来源：[README.md:40](../../README.md#L40)。`[未验证]`。

> 重要约束：Claude Code 拒绝写入插件自身目录，所以不要把学习文件夹建在插件仓库里（[README.md:38](../../README.md#L38)）。

## 构建与启动

- **构建**：无。纯 Python 脚本按源文件执行，没有编译、打包或产物目录（仓库根不存在构建配置）。
- **服务/端口**：无。插件不启动常驻进程、不监听端口；每次调用都是一次性 CLI 执行。
- **启动**：进入自己的学习文件夹运行 `claude`，由技能按需调用 `${CLAUDE_PLUGIN_ROOT}/scripts/*.py`（[skills/learn/SKILL.md:18-22](../../skills/learn/SKILL.md#L18-L22)）。`[配置中]`

## 数据布局

所有运行时数据都在启动目录的 `.tutor/` 下，`--root` 默认为当前工作目录（[scripts/kb.py:843](../../scripts/kb.py#L843)、[scripts/ledger.py:268](../../scripts/ledger.py#L268)、[scripts/path.py:1005](../../scripts/path.py#L1005)）。

| 路径 | 内容 | 写入方 | 依据 |
| --- | --- | --- | --- |
| `.tutor/kb/<kb>/index.sqlite` | 来源、页面、FTS5 索引 | `kb.py ingest/register/remove` | [scripts/kb.py:92-118](../../scripts/kb.py#L92-L118) |
| `.tutor/kb/<kb>/scans/<source_id>/` | 扫描页渲染的 PNG | `kb.py render` | [scripts/kb.py:435-441](../../scripts/kb.py#L435-L441) |
| `.tutor/path/<kb>/paths.json` | 路径定义与活动路径 ID | `path.py save/activate/...` | [scripts/path.py:79-81](../../scripts/path.py#L79-L81) |
| `.tutor/learner/<learner>/events.jsonl` | 全部学习事件（追加式） | `ledger.py record` | [scripts/learner_log.py:69-72](../../scripts/learner_log.py#L69-L72) |
| `.tutor/tmp/` | 技能写入的临时 JSON | 技能 | [README.md:56-60](../../README.md#L56-L60) |
| `.tutor/quiz/` | 测验会话文件 | `/tutor:quiz` | [README.md:59](../../README.md#L59) |

`.tutor/` 被 `.gitignore` 排除（[.gitignore:2](../../.gitignore#L2)）。

## 配置

没有配置文件。可调项只有 CLI 参数：

| 参数 | 默认值 | 作用 | 依据 |
| --- | --- | --- | --- |
| `--root` | 当前目录 | 包含 `.tutor/` 的项目根 | 上述三处 parser |
| `--learner` | `default` | 事件日志所属学习者 | [scripts/ledger.py:269](../../scripts/ledger.py#L269)、[scripts/path.py:1006](../../scripts/path.py#L1006) |
| `--kb` | 因命令而异 | 目标知识库 | 各子命令 |
| `--mode` | `any` | 检索词匹配模式 `any`/`all` | [scripts/kb.py:858](../../scripts/kb.py#L858) |

技能内通过 `${CLAUDE_PLUGIN_ROOT}` 定位脚本（[skills/learn/SKILL.md:18-22](../../skills/learn/SKILL.md#L18-L22)）。

### 权限

技能自带的 `allowed-tools` 只在该技能那一轮生效；长会话可能再次询问。可一次性加入设置：

```json
{"permissions": {"allow": [
  "Bash(python3 *scripts/kb.py*)", "Bash(python3 *scripts/ledger.py*)",
  "Bash(python3 *scripts/path.py*)",
  "Edit(**/.tutor/tmp/*)", "Edit(**/.tutor/quiz/*)"
]}}
```

来源：[README.md:53-61](../../README.md#L53-L61)。`[配置中]`。

## 建库与使用

```text
/tutor:kb ./lectures                  # 导入课程资料（PDF/pptx/docx/xlsx/md/txt）
/tutor:path 期中考试前掌握光合作用     # 可选：从目标生成并确认学习路径
/tutor:learn 什么是卡尔文循环？        # 基于资料讲解，引文逐字核对
/tutor:quiz 光合作用 5                # 出 5 道题并判分
/tutor:review 10                      # 按遗忘风险复习 10 项
```

参数形态来自各技能 frontmatter 的 `argument-hint`（[skills/kb/SKILL.md:3](../../skills/kb/SKILL.md#L3)、[skills/learn/SKILL.md:3](../../skills/learn/SKILL.md#L3)、[skills/quiz/SKILL.md:3](../../skills/quiz/SKILL.md#L3)、[skills/review/SKILL.md:3](../../skills/review/SKILL.md#L3)、[skills/path/SKILL.md:3](../../skills/path/SKILL.md#L3)）。`[配置中]`；完整技能会话本次未执行。

## 直接运行脚本

适合调试与手工核对。脚本以 `python3 scripts/<name>.py` 方式运行（依赖同目录导入，见 [04-dependencies.md](04-dependencies.md)）。

```bash
python3 scripts/kb.py list
python3 scripts/kb.py status --kb bio
python3 scripts/kb.py search "light reactions" --kb bio --limit 5
python3 scripts/ledger.py state --kb bio
python3 scripts/ledger.py due --kb bio --limit 10
python3 scripts/path.py status --kb bio
```

`kb.py list` 已实际执行（`[已验证]`）；其余命令的语法来自各脚本的 argparse 定义（[scripts/kb.py:841-875](../../scripts/kb.py#L841-L875)、[scripts/ledger.py:266-284](../../scripts/ledger.py#L266-L284)、[scripts/path.py:1003-1046](../../scripts/path.py#L1003-L1046)），未逐条执行。

## 测试与演练

```bash
python3 -I -W error::ResourceWarning -m unittest discover -s tests
python3 -I tests/golden/mutation_check.py
bash tests/walkthrough.sh
```

来源：[README.md:95-99](../../README.md#L95-L99)。本次运行结果：

| 命令 | 结果 | 证据等级 |
| --- | --- | --- |
| `unittest` | `Ran 166 tests in 9.565s`，`OK` | `[已验证]` 2026-11-09 |
| `mutation_check.py` | `19/19 mutants killed` | `[已验证]` |
| `walkthrough.sh` | 退出码 0，最后断言 `exemption completed m2 and the path` | `[已验证]` |

测试全部使用临时目录，不会写入用户 `.tutor/`；黄金向量生成需要 DeepTutor checkout（[README.md:101](../../README.md#L101)）。

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 脚本报缺少 `pdftotext`/`pdftoppm`/`pdfimages` | 未安装 poppler | `brew install poppler`（[README.md:48](../../README.md#L48)） |
| pptx/docx/xlsx 导入失败 | 未安装 markitdown | `uv tool install 'markitdown[all]'` 或 `pip install 'markitdown[all]'`（[README.md:49](../../README.md#L49)） |
| 技能调用脚本被拒绝 | 权限只在技能当轮生效 | 选择 “always allow”，或写入上面的 permissions 配置（[README.md:53-63](../../README.md#L53-L63)） |
| 知识库建不起来 / 数据找不到 | 在插件仓库里当学习文件夹 | 换到独立学习文件夹；插件目录不可写（[README.md:38](../../README.md#L38)） |
| 引文被标 `wrong_page` 或 `not_found` | 页码错、文本层不完整、或引文并非逐字 | 用 `kb.py get --source S --page N` 核对原文；扫描页需先转写 |
| 扫描页引文显示低置信 | 页面来源是 `ai-transcription` | 理解为“与转写一致”，不是与纸张一致（[README.md:70](../../README.md#L70)） |
| 模块一直 `locked` | 前置门槛未过、证据页失效、或没有 `question_id` | 运行 `path.py status`，看 `gaps` 与 `next_action` |

## 发布流程

`[配置中]` 来自项目约定（[CLAUDE.md:19](../../CLAUDE.md#L19)）：

1. 每个版本一个 release commit 直接进 `main`（0.1.3 起不建 feature 分支）。
2. 打附注 tag 并建 release：
   ```bash
   git tag -a vX.Y.Z <commit> -m "X.Y.Z"
   git push origin vX.Y.Z
   gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file <文件>
   ```
3. notes 取 `CHANGELOG.md` 对应段落，语言与 CHANGELOG 一致，并链回 CHANGELOG。
4. 从 0.1.4 起纳入快照；0.1.0–0.1.3 未打 tag。

当前版本 0.2.0（[.claude-plugin/plugin.json:4](../../.claude-plugin/plugin.json#L4)、[CHANGELOG.md:1-12](../../CHANGELOG.md#L1-L12)）。
