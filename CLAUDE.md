## 项目原则
- 目标是更深入,不是更大:先把建库、讲解、出题、复习四项核心做扎实,不扩展新功能。
- 以 DeepTutor-main/ 源码与官方 SKILL.md 为一手依据;社区项目只作参考,不当事实。
- 所有教学方法的设计要能指回 DeepTutor 的具体文件;结论要有外部可核对的证据(许可证文本、测试、可运行脚本)。
- 全部推理由 Claude 完成,不调用其他模型 API;保留 DeepTutor 的 Apache 2.0 版权声明与出处。
- 先给结构与流程设计,用户确认后再写代码。
- 版本:从 0.1.0 起步。次版本(0.X.0)加新功能,修订版(0.X.Y)只做缺陷修复、维护、性能优化。路线图见 docs/DESIGN.md 第 6 节。
- 讲解默认英文(用户在英国读研,资料全是英文);用户明确要求时才换语言。用户数据放各项目内的 `.tutor/`。

## 开发流程(Compound Engineering)
每个次版本、每项新功能按以下顺序走,直接调用已安装的 `compound-engineering` 插件的命令(用户级安装,版本 3.32.0):
1. Brainstorm → `ce-brainstorm`
2. Plan → `ce-plan`;**Plan 须经用户确认后才开始 Work**
3. Work → `ce-work`
4. Review → `ce-code-review`:由干净上下文的审查者针对本次改动审查,不由写代码的上下文自评
5. 按 Review 结果修复(必要时用 `ce-debug`)
6. Compound → `ce-compound`:记录学到了什么、哪里出过错、下次怎么避免;可复用的教训提炼进本文件
- 不用 `lfg`(会一路跑到开 PR 并推送,绕过"Plan 先确认")。不调用 `ce-commit`、`ce-commit-push-pr`,除非用户明确说要提交或推送。
- 文档位置(已核实):`ce-*` 的产物根目录默认是 `docs/`(未设 `docs_root`),`ce-compound` 写到 `docs/solutions/<分类>/`,`ce-plan` 用 `docs/plans/`。0.1.0 的旧文档保持原位(`docs/brainstorms/`、`docs/plans/`、`docs/compound/`、`docs/verification/`);新的可复用经验一律写进 `docs/solutions/`,开工前先翻一遍里面有没有相关条目。
- 若某个 `ce-*` 命令因缺少 `bun` 等外部工具失败,告诉用户,不要自己安装。
- 按任务大小取舍:错别字、单行修复、纯文档微调不走全流程;修订版(0.X.Y)缺陷修复至少要有复现测试和 Review。
- 代价是更多 token,换来的是计划被确认、审查独立、教训沉淀;它不保证零缺陷,测试与黄金向量才是正确性的依据。

## 模块化
- 每个技能(skills/*)和每个脚本(scripts/*)单独成模块,脚本是小型命令行程序,JSON 进 JSON 出,可单独运行和测试。
- 命令行脚本(`kb.py`、`ledger.py`)之间只通过两个契约沟通:`.tutor/` 下的数据格式和脚本的 JSON 接口,不互相导入。纯函数库(`mastery.py`、`scheduler.py`)可以被命令行脚本导入。
- 每个模块自带测试;一个版本只动它需要的模块。不为未来版本预先造抽象层。

## 经验教训(0.1.0 Compound,详见 docs/compound/0.1.0.md)
- 技能写完后必须用真实会话跑一遍再算完成:`claude -p "/tutor:xxx" --plugin-dir . --output-format json`,看 `permission_denials`。
- 向脚本传 JSON 或文本时,先用 Write 写进 `.tutor/tmp/` 下的新文件,再用 `<` 重定向;不用 heredoc(含大括号和引号会被 Claude Code 拦截)。
- 可用的权限写法:`Bash(python3 *scripts/x.py*)`、`Edit(**/.tutor/tmp/*)`;`Write(...)` 规则不匹配绝对路径。技能的 `allowed-tools` 只在该技能的那一轮生效。
- 任何关于 DeepTutor 行为的说法,必须在 `DeepTutor-main/` 里查到文件和行号再写。
- 黄金向量必须配变异检查(`tests/golden/mutation_check.py`);改调度器后要重跑。
- 支撑"可信"承诺的代码(引文核对、掌握度)默认从严,每放宽一处就加一条回归测试。
- 我自己的 Bash 命令在 zsh 下:不要依赖 `$VAR` 按空格拆词,用函数或 bash 脚本。
