# Model Agents Definition

## Overview

Models, priorities, cost scores, and selection policies for the Model Manager Agent.

## Selection Policy

```
Priority 1: ZEN (OpenRouter Free) → Priority 2: Sakura Free → Priority 3: DeepSeek (paid)
```

## Models

### ZEN Models (OpenRouter Free)

| Model Key | Model ID | Provider | Context | Quality | Cost/Input | Cost/Output | Free Quota |
|-----------|----------|----------|---------|---------|------------|-------------|------------|
| owl-alpha | openrouter/owl-alpha | openrouter | 1050K | 85 | $0 | $0 | 50-1000 req/day |
| nemotron-3-super | nvidia/nemotron-3-super-120b-a12b:free | openrouter | 1000K | 80 | $0 | $0 | 50-1000 req/day |
| laguna-m1 | poolside/laguna-m.1:free | openrouter | 262K | 75 | $0 | $0 | 50-1000 req/day |
| qwen3-coder-free | qwen/qwen3-coder:free | openrouter | 1000K | 78 | $0 | $0 | 50-1000 req/day |
| gemma4-31b | google/gemma-4-31b-it:free | openrouter | 262K | 70 | $0 | $0 | 50-1000 req/day |

### Sakura Models (Free Tier)

| Model Key | Model ID | Provider | Context | Quality | Cost/Input | Cost/Output | Free Quota |
|-----------|----------|----------|---------|---------|------------|-------------|------------|
| kimi-k2.6 | preview/Kimi-K2.6 | sakura2 | 262K | 82 | $0 | $0 | 3000 req/month |
| qwen3-coder-480b | Qwen3-Coder-480B-A35B-Instruct-FP8 | sakura2 | 128K | 88 | $0 | $0 | 3000 req/month |
| qwen3-coder-30b | Qwen3-Coder-30B-A3B-Instruct | sakura2 | 128K | 72 | $0 | $0 | 3000 req/month |
| gpt-oss-120b | gpt-oss-120b | sakura2 | 131K | 76 | $0 | $0 | 3000 req/month |

### DeepSeek Models (Paid)

| Model Key | Model ID | Provider | Context | Quality | Cost/Input | Cost/Output | Notes |
|-----------|----------|----------|---------|---------|------------|-------------|-------|
| deepseek-v4-flash | deepseek-v4-flash | deepseek | 1M | 83 | $0.14 | $0.28 | Cheapest |
| deepseek-v4-pro | deepseek-v4-pro | deepseek | 1M | 92 | $0.435 | $0.87 | Best quality |

### Qwen Models (Paid - DashScope)

| Model Key | Model ID | Provider | Context | Quality | Cost/Input | Cost/Output | Notes |
|-----------|----------|----------|---------|---------|------------|-------------|-------|
| qwen-turbo | qwen-turbo | dashscope | 129K | 65 | $0.05 | $0.20 | Cheapest |
| qwen-plus | qwen-plus | dashscope | 129K | 78 | $0.40 | $1.20 | Balanced |
| qwen-max | qwen-max | dashscope | 31K | 90 | $1.60 | $6.40 | Best |

## Task Profiles

| Task Type | Description | Recommended Model | Max Cost/Task |
|-----------|-------------|-------------------|---------------|
| quick | Simple questions, greetings | owl-alpha | $0 |
| code | Code generation, debugging | qwen3-coder-free | $0 |
| long | Long context analysis | nemotron-3-super | $0 |
| reasoning | Complex reasoning, math | deepseek-v4-flash | $0.01 |
| creative | Writing, brainstorming | kimi-k2.6 | $0 |
| vision | Image understanding | gemma4-31b | $0 |

## Judgment Cost Settings

```yaml
judgment_cost:
  # Cost of making the model selection decision itself
  selection_overhead_ms: 50  # Time to select model
  selection_overhead_tokens: 100  # Tokens used in decision
  
  # Minimum quality threshold
  min_quality_score: 60
  
  # Maximum acceptable latency
  max_latency_ms: 5000
  
  # Cost weight factors
  weights:
    quality: 0.4
    cost: 0.3
    latency: 0.2
    availability: 0.1
```

## Selection Algorithm

```
1. Evaluate task type
2. Filter models by availability (not exhausted)
3. Calculate score = (quality * w_q) + (1/cost * w_c) + (1/latency * w_l) + (availability * w_a)
4. Apply judgment cost overhead
5. Select model with highest score
6. Log decision and outcome
```
