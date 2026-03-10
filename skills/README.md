# QuickPat Skills

Model-agnostic skills for transforming AI Quickstarts into Validated Patterns. These work with any LLM (Claude, GPT-4, Gemini, Llama, Mistral) or in pure deterministic mode.

## Available Skills

### transform_quickstart

Converts an AI Quickstart Helm chart into a production-ready Validated Pattern.

## Usage

### Text Skill (any model)

Copy the contents of `transform_quickstart.md` into:

- **ChatGPT**: Custom Instructions or system prompt
- **Claude**: Project instructions or paste at conversation start
- **Gemini**: System instruction or first message
- **Local models**: System prompt via Ollama, LM Studio, etc.

Then use the trigger: "Transform this quickstart: [path or description]"

### Python Skill (deterministic)

No LLM required for standard transformations:

```python
from skills.transform_quickstart import transform

result = transform("/path/to/quickstart")
print(result.pattern_dir)
print(result.files_created)
```

### Python Skill (with LLM for edge cases)

Use any LLM provider for reasoning about unusual charts:

```python
from skills.transform_quickstart import transform, make_openai_llm

# OpenAI
llm = make_openai_llm(model="gpt-4o-mini")
result = transform("/path/to/quickstart", llm=llm)

# Anthropic
from skills.transform_quickstart import make_anthropic_llm
llm = make_anthropic_llm(model="claude-sonnet-4-20250514")
result = transform("/path/to/quickstart", llm=llm)

# Local Ollama
from skills.transform_quickstart import make_ollama_llm
llm = make_ollama_llm(model="llama3.1")
result = transform("/path/to/quickstart", llm=llm)

# Any custom LLM — just pass a callable(system: str, user: str) -> str
result = transform("/path/to/quickstart", llm=my_custom_llm)
```

### Python Skill (CLI)

```bash
# Deterministic
python skills/transform_quickstart.py /path/to/quickstart

# With LLM
python skills/transform_quickstart.py /path/to/quickstart --llm openai
python skills/transform_quickstart.py /path/to/quickstart --llm ollama --model mistral

# Options
python skills/transform_quickstart.py /path/to/quickstart \
  --output ~/my-patterns/my-pattern \
  --name my-pattern \
  --no-vault
```

### Dispatcher Pattern

If you have multiple skills, use a dispatcher to route tasks:

```python
skills = {
    "transform": transform,
    # Add more skills here
}

# Ask any LLM: "Which skill do I need for: converting a quickstart?"
# LLM responds: "transform"
# Load and execute that skill
result = skills["transform"]("/path/to/quickstart")
```

## LLM Adapter Interface

Any function matching this signature works as an LLM:

```python
def my_llm(system: str, user: str, response_schema: dict = None) -> str | dict:
    """Send system prompt + user message, return response.

    When response_schema is provided, return a parsed dict matching
    the JSON schema. When absent, return a plain text string.
    """
    ...
```

Built-in adapters with structured output support:

| Adapter | Structured Output Method |
|---------|-------------------------|
| `make_openai_llm()` | `response_format` with JSON schema |
| `make_anthropic_llm()` | `tool_use` with forced tool choice |
| `make_ollama_llm()` | `format` parameter with JSON schema |
| `make_vllm_llm()` | `guided_json` via `extra_body` |

All hooks fall back to text parsing if the adapter doesn't support
structured output or returns a string.

### vLLM Example

```python
from skills.transform_quickstart import make_vllm_llm

llm = make_vllm_llm(model="meta-llama/Llama-3.1-8B-Instruct",
                     base_url="http://localhost:8000")
result = transform("/path/to/quickstart", llm=llm)
```

```bash
python skills/transform_quickstart.py transform /path/to/quickstart \
  --llm vllm --model meta-llama/Llama-3.1-8B-Instruct --llm-url http://localhost:8000
```

## Environment Variables

When using LLM adapters:

- `OPENAI_API_KEY` — for OpenAI adapter
- `ANTHROPIC_API_KEY` — for Anthropic adapter
- Ollama and vLLM require no API key (local)
