import os
import requests

API_KEY = os.getenv("CLAUDE_API_KEY")

TARGET_FILE = "advanced_regime_engine.py"
MAX_INPUT_CHARS = 120000


def read_file():
    for root, _, files in os.walk("."):
        for f in files:
            if f == TARGET_FILE:
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as file:
                    return file.read()[:MAX_INPUT_CHARS]
    return ""


def build_prompt(code):
    return f"""
You are a senior quantitative engineer, distributed systems expert, and production auditor.

Perform a DEEP PRODUCTION-GRADE AUDIT.

You must:
- Analyze line-by-line
- Detect ALL critical issues
- Focus on:
  - hidden state bugs
  - regime logic correctness
  - probabilistic consistency
  - numerical stability
  - concurrency safety
  - silent failures
  - incomplete implementations

STRICT OUTPUT FORMAT:

## 🔍 AUDIT SUMMARY
- List ALL critical issues

## ⚠️ ROOT CAUSE
- Explain WHY each issue occurs

## 🧩 PATCH DIFF
- Unified git diff ONLY
- Must be directly applicable
- No full file rewrite
- No explanations inside diff

FILE: advanced_regime_engine.py

CODE:
{code}
"""


def call_claude(prompt):
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 16000,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    return response.json()


def main():
    if not API_KEY:
        raise ValueError("CLAUDE_API_KEY missing")

    code = read_file()

    print("🚀 Running deep audit...")

    result = call_claude(build_prompt(code))

    output = result.get("content", [{}])[0].get("text", "")

    with open("CLAUDE_AUDIT.md", "w") as f:
        f.write(output)

    print("✅ Audit saved to CLAUDE_AUDIT.md")


if __name__ == "__main__":
    main()