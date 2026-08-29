"""
Project Atlas LLM Advisor - Core Orchestrator
Supports TWO backends:
  1. LOCAL (Ollama): Your CPU
  2. CLOUD (Groq):   Free cloud - llama3-70b etc.
API key loaded from research/.env - never hardcode keys in source.
"""

import json
import sys
import os


def _load_env():
    # File is in research/src/llm/atlas_advisor.py
    # We need to go up to research/
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ".env"
    )
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key not in os.environ:
                    os.environ[key] = value

_load_env()

_OLLAMA_DEFAULT = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
if _OLLAMA_DEFAULT not in os.environ.get("PATH", "") and os.path.isdir(_OLLAMA_DEFAULT):
    os.environ["PATH"] = _OLLAMA_DEFAULT + os.pathsep + os.environ.get("PATH", "")

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.system_prompt import SYSTEM_PROMPT
from llm.tools import TOOL_SCHEMAS, TOOL_REGISTRY

BACKEND = os.environ.get("ATLAS_BACKEND", "local").lower()
LOCAL_DEFAULT_MODEL  = os.environ.get("ATLAS_MODEL", "qwen2.5:7b")
LOCAL_FALLBACK_MODEL = "phi3.5:mini"

GROQ_MODELS = {
    "gpt-oss-120b":   "openai/gpt-oss-120b",            # Best reasoning, 120B
    "qwen-27b":       "qwen/qwen3.6-27b",               # Qwen 3.6, fast & capable
    "gpt-oss-20b":    "openai/gpt-oss-20b",              # Lightweight, fast
    "compound":       "groq/compound",                   # Groq compound model
}
GROQ_DEFAULT_MODEL = "gpt-oss-120b"


OLLAMA_OPTIONS = {
    "num_ctx":        2048,
    "num_thread":     12,
    "num_gpu":        0,
    "repeat_penalty": 1.1,
}


def _get_groq_client():
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key or api_key == "your_groq_api_key_here":
        raise RuntimeError(
            "\n  Groq API key not set!\n"
            "  1. Go to: https://console.groq.com\n"
            "  2. Sign up free and create an API key\n"
            "  3. Open: research\\.env\n"
            "  4. Replace 'your_groq_api_key_here' with your actual key\n"
            "  5. Restart Atlas\n"
        )
    try:
        from groq import Groq
        return Groq(api_key=api_key)
    except ImportError:
        raise RuntimeError("\n  Run: pip install groq\n")


class AtlasAdvisor:
    """Atlas AI Advisor. Supports local Ollama and Groq cloud backends."""

    def __init__(self, model=None, backend=None):
        self.backend = (backend or BACKEND).lower()
        self.conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]

        if self.backend == "groq":
            self.model = model or GROQ_DEFAULT_MODEL
            self._groq = _get_groq_client()
            self._groq_model_id = GROQ_MODELS.get(self.model, self.model)
            print(f"\n  Backend : Groq Cloud  |  Model: {self._groq_model_id}")
            print(f"  Speed   : ~300 tokens/sec (free tier)\n")
        else:
            self.model = model or LOCAL_DEFAULT_MODEL
            self._groq = None
            self._check_ollama()
            print(f"\n  Backend : Local Ollama  |  Model: {self.model}")
            print(f"  Speed   : ~3-5 tokens/sec on CPU (normal)\n")

    def _check_ollama(self):
        if not OLLAMA_AVAILABLE:
            raise RuntimeError("Run: pip install ollama")
        try:
            models = ollama.list()
            available = [m.model for m in models.models]
            if not any(self.model in m for m in available):
                print(f"\n  Model '{self.model}' not found. Run: ollama pull {self.model}\n")
                if any(LOCAL_FALLBACK_MODEL in m for m in available):
                    self.model = LOCAL_FALLBACK_MODEL
                else:
                    raise RuntimeError(f"Run: ollama pull {self.model}")
        except Exception as e:
            if "Connection refused" in str(e) or "ConnectError" in str(e):
                raise RuntimeError("\n  Ollama not running. Open the Ollama app.\n")
            raise

    def chat(self, user_message):
        self.conversation_history.append({"role": "user", "content": user_message})

        if self.backend == "groq":
            text, tool_calls = self._groq_chat(self.conversation_history)
        else:
            text, tool_calls = self._ollama_chat(self.conversation_history)

        if tool_calls:
            self.conversation_history.append({
                "role": "assistant",
                "content": text or "",
                "tool_calls": [
                    {"id": tc.get("id", f"call_{i}"), "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(tc["args"]) if isinstance(tc["args"], dict) else tc["args"]}}
                    for i, tc in enumerate(tool_calls)
                ]
            })

            for tc in tool_calls:
                print(f"\n  [Atlas] Running analysis: {tc['name']}({_format_args(tc['args'])})...")
                result = {"error": f"Unknown tool: {tc['name']}"}
                if tc["name"] in TOOL_REGISTRY:
                    try:
                        result = TOOL_REGISTRY[tc["name"]](**tc["args"])
                    except Exception as e:
                        result = {"error": str(e)}
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{tool_calls.index(tc)}"),
                    "content": json.dumps(result, default=str),
                })

            if self.backend == "groq":
                text, _ = self._groq_chat(self.conversation_history)
            else:
                text, _ = self._ollama_chat(self.conversation_history)

        self.conversation_history.append({"role": "assistant", "content": text or ""})
        return text or ""

    def _ollama_chat(self, messages):
        response = ollama.chat(
            model=self.model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            options=OLLAMA_OPTIONS,
        )
        msg = response.message
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                tool_calls.append({"id": getattr(tc, "id", ""), "name": tc.function.name, "args": args})
        return msg.content, tool_calls

    def _groq_chat(self, messages):
        try:
            response = self._groq.chat.completions.create(
                model=self._groq_model_id,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                max_tokens=4096,
            )
        except Exception:
            # Fallback: some models don't support tool calling
            response = self._groq.chat.completions.create(
                model=self._groq_model_id,
                messages=messages,
                max_tokens=4096,
            )
        msg = response.choices[0].message
        tool_calls = []
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                tool_calls.append({"id": tc.id, "name": tc.function.name, "args": args})
        return msg.content, tool_calls

    def reset(self):
        self.conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
        print("  Conversation cleared.")

    def switch_model(self, model, backend=None):
        if backend:
            self.backend = backend
        self.model = model
        if self.backend == "groq":
            self._groq_model_id = GROQ_MODELS.get(model, model)
            if not self._groq:
                self._groq = _get_groq_client()
            print(f"  Switched to Groq: {self._groq_model_id}")
        else:
            print(f"  Switched to local: {self.model}")


def _format_args(args):
    parts = []
    for k, v in args.items():
        if isinstance(v, float) and v > 1000:
            parts.append(f"{k}=Rs{v:,.0f}")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)
