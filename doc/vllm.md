# 使用 vLLM 本地部署模型

本文档介绍如何将项目从 DashScope（阿里云通义千问）切换到本地 vLLM 推理服务。

---

## 什么是 vLLM

[vLLM](https://github.com/vllm-project/vllm) 是一个高性能的开源大模型推理框架，支持运行 Llama、Qwen、Mistral、DeepSeek 等主流开源模型。它提供与 OpenAI API 完全兼容的接口，因此项目可以无缝切换。

**适用场景：**
- 私有化部署，数据不出内网
- 离线环境或无法使用云端 API
- 需要更低延迟或更高吞吐量
- 使用自定义微调模型

---

## 第一步：启动 vLLM 服务

### 安装 vLLM

```bash
pip install vllm
```

### 启动推理服务

以 Qwen2.5-7B-Instruct 为例：

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --served-model-name Qwen2.5-7B-Instruct
```

启动后，API 地址为 `http://localhost:8000/v1`。

验证服务是否正常：

```bash
curl http://localhost:8000/v1/models
```

> **注意：** `--served-model-name` 指定的名称就是后续配置中使用的模型名。

---

## 第二步：配置项目

项目的主配置文件位于 `~/.comworker/config.json`（JSON 格式）。

### 方式一：修改 config.json（推荐）

编辑 `~/.comworker/config.json`，在 `providers` 下添加 `vllm` 配置，并修改默认模型：

```json
{
  "providers": {
    "vllm": {
      "apiKey": "EMPTY",
      "apiBase": "http://localhost:8000/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "Qwen2.5-7B-Instruct"
    }
  }
}
```

**字段说明：**

| 字段 | 说明 |
|------|------|
| `providers.vllm.apiKey` | vLLM 默认不需要鉴权，填 `"EMPTY"` 即可；如果启动时设置了 `--api-key`，则填对应的值 |
| `providers.vllm.apiBase` | vLLM 服务地址，需包含 `/v1` 路径 |
| `agents.defaults.model` | 模型名称，须与 vLLM `--served-model-name` 保持一致 |

### 方式二：环境变量

也可以通过环境变量（或 `.env` 文件）覆盖配置，无需修改 config.json：

```bash
# .env 文件
COMWORKER_PROVIDERS__VLLM__API_KEY=EMPTY
COMWORKER_PROVIDERS__VLLM__API_BASE=http://localhost:8000/v1
COMWORKER_AGENTS__DEFAULTS__MODEL=Qwen2.5-7B-Instruct
```

> 环境变量前缀为 `COMWORKER_`，层级间用双下划线 `__` 分隔。

---

## 与 DashScope 配置对比

| 配置项 | DashScope（当前） | vLLM（本地） |
|--------|-------------------|--------------|
| `.env` 中的 Key | `DASHSCOPE_API_KEY=sk-xxx` | 无需 Key（或 `EMPTY`） |
| config.json provider | `"dashscope": {"apiKey": "sk-xxx"}` | `"vllm": {"apiKey": "EMPTY", "apiBase": "http://..."}` |
| 模型名示例 | `qwen-max` | `Qwen2.5-7B-Instruct` |
| LiteLLM 内部路由 | `dashscope/qwen-max` | `hosted_vllm/Qwen2.5-7B-Instruct` |

切换时只需更改 `providers` 和 `agents.defaults.model`，无需修改其他代码。

---

## 完整 config.json 示例

以下是一个最小化的完整配置（其他字段保留默认值）：

```json
{
  "providers": {
    "vllm": {
      "apiKey": "EMPTY",
      "apiBase": "http://localhost:8000/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "Qwen2.5-7B-Instruct",
      "maxTokens": 8192,
      "temperature": 0.1
    }
  }
}
```

---

## 常用模型启动命令

### Qwen2.5 系列

```bash
# 7B，消费级 GPU（24GB 显存）
vllm serve Qwen/Qwen2.5-7B-Instruct --served-model-name Qwen2.5-7B-Instruct

# 72B，多卡
vllm serve Qwen/Qwen2.5-72B-Instruct --served-model-name Qwen2.5-72B-Instruct \
  --tensor-parallel-size 4
```

### DeepSeek 系列

```bash
vllm serve deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --served-model-name DeepSeek-R1-7B
```

### Llama 系列

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --served-model-name Llama-3.1-8B-Instruct
```

---

## 工具调用（Tool Calling）支持

vLLM 支持工具调用，但需要模型本身支持 Function Calling（如 Qwen2.5-Instruct、Llama-3.1-Instruct 等）。

启动时建议加上：

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --served-model-name Qwen2.5-7B-Instruct \
  --enable-auto-tool-choice \
  --tool-call-parser hermes
```

不同模型对应的 `--tool-call-parser` 值：

| 模型系列 | parser |
|----------|--------|
| Qwen2.5 | `hermes` |
| Llama 3.1/3.2 | `llama3_json` |
| Mistral | `mistral` |
| DeepSeek | `hermes` |

---

## 部署完成后的curl测试
```
curl -X POST http://localhost:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer Empty" \
     -d '{
           "model": "Qwen2.5-7B-Instruct",
           "messages": [
               {"role": "user", "content": "你好"}
           ]
         }'
输出
{"id":"chatcmpl-8c9ba236ade65176","object":"chat.completion","created":1773411630,"model":"Qwen2.5-7B-Instruct","choices":[{"index":0,"message":{"role":"assistant","content":"你好！很高兴为你提供帮助。有什么问题或需要什么信息我可以帮你查找吗？","refusal":null,"annotations":null,"audio":null,"function_call":null,"tool_calls":[],"reasoning":null},"logprobs":null,"finish_reason":"stop","stop_reason":null,"token_ids":null}],"service_tier":null,"system_fingerprint":null,"usage":{"prompt_tokens":30,"total_tokens":49,"completion_tokens":19,"prompt_tokens_details":null},"prompt_logprobs":null,"prompt_token_ids":null,"kv_transfer_params":null}%
```
