# Trajectory Checker

LLM Agent 轨迹数据质量检查工具。主要用于 Claude Code 插件场景，也可以独立命令行运行，并可选安装到 Codex 作为本地 plugin/skill 使用。

工具通过 YAML delivery profile 定义不同交付规范的校验规则，覆盖结构、字段、tool call、工具 schema、去重、多样性、思考内容偏好、合成痕迹等检查。

## Features

- 支持 JSONL、单条 JSON、JSON 数组输入。
- 支持交付规则配置：`buyers/*.yaml`。
- 内置脱敏交付规则 profile：`buyers/delivery-standard.yaml`。
- 支持结构检查、统计检查和交付规则检查。
- 支持 Claude Code skill 调用、独立命令行调用、Codex 可选安装。
- 支持生成 Markdown 报告。

## Claude Code Plugin Usage

保持 Claude Code marketplace 安装方式：

```json
{
  "extraKnownMarketplaces": {
    "trajectory-checker-marketplace": {
      "source": { "repo": "stan1027/trajectory-checker-public", "source": "github" }
    }
  },
  "enabledPlugins": {
    "trajectory-checker@trajectory-checker-marketplace": true
  }
}
```

然后在 Claude Code 中直接说：

```text
check trajectory /path/to/data.jsonl --buyer delivery-standard
```

也可以让 Claude Code 自动选择 profile：

```text
check trajectory /path/to/data.jsonl
```

如果只有一个非模板 profile，会自动使用该 profile；如果有多个，会询问使用哪个 profile。

## Command Line Usage

安装依赖：

```bash
pip3 install pyyaml
```

使用内置交付规则检查：

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --buyer delivery-standard \
  --data /path/to/data.jsonl
```

输出 JSON：

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --buyer delivery-standard \
  --data /path/to/data.jsonl \
  --json
```

生成 Markdown 报告：

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --buyer delivery-standard \
  --data /path/to/data.jsonl \
  --json \
  --report reports/
```

仅做结构检查：

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --data /path/to/data.jsonl \
  --structural-only \
  --json
```

校验 delivery profile：

```bash
python3 skills/check-trajectory/scripts/checker.py \
  --validate-profile delivery-standard
```

## Optional Codex Installation

Claude Code 是主使用场景；Codex 安装是可选补充。推荐把整个仓库作为 Codex plugin 安装，这样 `buyers/`、报告目录和 skill 资源都会保留：

```bash
mkdir -p ~/.codex/plugins
git clone https://github.com/stan1027/trajectory-checker-public.git \
  ~/.codex/plugins/trajectory-checker
```

如果你已经安装过：

```bash
cd ~/.codex/plugins/trajectory-checker
git pull
```

在 Codex 中可直接要求：

```text
用 trajectory-checker 检查 /path/to/data.jsonl 是否符合内置交付要求
```

也可以只安装 Codex skill：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/stan1027/trajectory-checker-public.git /tmp/trajectory-checker-public
cp -R /tmp/trajectory-checker-public/skills/check-trajectory ~/.codex/skills/check-trajectory
```

skill 子目录内已经包含 `buyers/delivery-standard.yaml`，所以只安装 skill 时也可以使用内置交付规则。更新时重新复制 `skills/check-trajectory` 即可。

## Project Structure

```text
trajectory-checker-public/
├── .claude-plugin/
│   ├── marketplace.json
│   └── plugin.json
├── buyers/
│   ├── _template.yaml
│   ├── example.yaml
│   └── delivery-standard.yaml
├── skills/
│   └── check-trajectory/
│       ├── SKILL.md
│       ├── buyers/
│       │   ├── _template.yaml
│       │   └── delivery-standard.yaml
│       ├── scripts/
│       │   └── checker.py
│       └── references/
│           └── report-format.md
└── reports/
```

## Bundled Delivery Profile Notes

`buyers/delivery-standard.yaml` 按脱敏后的交付规则整理，并结合实际交付口径做了自动化检测。

阻断交付的 error 重点包括：

| Check | Meaning |
|---|---|
| `top_level_fields` | 缺少必要顶层字段 |
| `system_format` | `prompt[0]` 不是合法 system |
| `message_order` | tool 消息前没有 assistant tool call |
| `tool_call_pairing` | `tool_call.id` 和 `tool.tool_call_id` 不匹配 |
| `candidates_format` | candidates 结构不符合要求 |
| `tools_schema_shape` | tools schema 被简化或缺少 name/description/parameters |
| `tool_call_declared` | assistant 调用了未声明工具 |
| `tool_call_arguments_schema` | tool call arguments 不满足 schema 的 required/type |
| `allowed_scaffold_model` | scaffold/model 不在 profile 允许范围内 |
| `tool_result_template_markers` | tool result 出现模板化占位文本 |
| `user_tool_merge_markers` | user 内容疑似混入 tool result/tool use |
| `prompt_candidates_tools_dedup` | `prompt + candidates + tools` 完全重复或前缀包含 |
| `session_containment` | 同 session 出现不同截断点重复 |
| `synthetic_markers` | 出现合成/生产提示痕迹 |

warning 重点包括：

| Check | Meaning |
|---|---|
| `signature_shape` | `signature`/思考签名缺失或为空；部分模型常见缺失，按偏好项处理 |
| `thinking_presence` | 缺少 `reasoning_content`/`thinking`；按偏好项处理 |
| `system_harness_markers` | system 未识别到已知 harness 签名 |
| `tools_description_length` | 工具描述过短 |
| `user_injection_markers` | user 消息缺少 harness 注入标记 |
| `system_tools_consistency` | system 中可见工具名称覆盖率偏低 |
| `repetition_garbled` | assistant 输出疑似重复或乱码 |

`signature` 字段当前不是硬性 error。规则口径为有则保留、没有则提示 warning；检测器会保留原始数据中已有的 signature，不建议伪造补充。

## Verdict

输出判定：

- `PASS`: 没有 error。
- `FAIL`: 存在至少一个 error。

严重度含义：

| Severity | Meaning |
|---|---|
| `error` | 阻断交付，需要修复或剔除 |
| `warning` | 需要复核/备注，通常不直接阻断 |
| `info` | 统计或建议，不影响判定 |

## Delivery Profiles

复制模板创建新 profile：

```bash
cp buyers/_template.yaml buyers/my-buyer.yaml
```

常用配置：

| Config | Meaning |
|---|---|
| `required_top_fields` | 每条记录必须存在的顶层字段 |
| `required_meta_fields` | `meta` 必须字段 |
| `recommended_meta_fields` | `meta` 推荐字段 |
| `allowed_scaffolds` | 允许的 agent scaffold |
| `allowed_models` | 允许的模型 |
| `system_unique_ratio` | 全批 system 去重率下限 |
| `system_min_length` | system prompt 最小长度 |
| `tools_must_differ` | tools schema 是否不能全批完全一样 |
| `tool_result_min_unique` | 非豁免工具 result 最小唯一数 |
| `assistant_content_min_rate` | assistant content 非空率下限 |
| `thinking_preferred` | 是否提示 thinking 缺失 |
| `signature_preferred` | 是否提示 signature 缺失 |
| `cross_record_max_overlap` | 跨记录消息重叠率上限 |

## Output Example

```json
{
  "verdict": "PASS",
  "summary": { "errors": 0, "warnings": 2, "info": 0 },
  "findings": [
    {
      "check_name": "signature_shape",
      "severity": "warning",
      "message": "signature field is missing; include it if available, but the source model may not return thinking signatures",
      "record_index": 0
    }
  ],
  "statistics": {
    "system_unique_count": "61/61",
    "system_unique_ratio": "100.0%"
  }
}
```
