import os
import requests
import sys
import re

API_KEY = os.getenv("CLAUDE_API_KEY")
TARGET_FILE = "advanced_regime_engine.py"
MAX_INPUT_CHARS = 120000


# ===============================
# READ FILE (STRICT)
# ===============================
def read_file():
    for root, _, files in os.walk("."):
        for f in files:
            if f == TARGET_FILE:
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as file:
                    return file.read()[:MAX_INPUT_CHARS]
    raise FileNotFoundError(f"{TARGET_FILE} not found")


# ===============================
# STRICT PROMPT
# ===============================
def build_prompt(code):
    return f"""
You are a senior quantitative engineer and production system auditor.

MANDATORY RULES:
- No assumptions without proof
- No vague statements
- No skipped analysis
- Every issue must reference exact logic behavior

You MUST:
- Analyze line-by-line
- Detect ALL critical issues
- Include edge cases and silent failures
- Ensure probabilistic correctness
- Ensure no directional signal suppression (IMPORTANT)

CRITICAL:
- Do NOT convert directional signals into HOLD due to penalties
- Penalization must reduce conviction, NOT eliminate action

OUTPUT FORMAT (STRICT — DO NOT VIOLATE):

## 🔍 AUDIT SUMMARY
- Exhaustive bullet list

## ⚠️ ROOT CAUSE
- Deep explanation per issue

## 🧩 PATCH DIFF
- Unified git diff
- Must be directly applicable
- No explanations inside diff
- No full file rewrite

FILE: advanced_regime_engine.py

CODE:
{code}
"""


# ===============================
# CLAUDE CALL
# ===============================
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

    data = response.json()

    try:
        return data["content"][0]["text"]
    except:
        print("❌ Claude response invalid:", data)
        sys.exit(1)


# ===============================
# OUTPUT VALIDATION (CRITICAL)
# ===============================
def validate_output(text):
    required_sections = [
        "## 🔍 AUDIT SUMMARY",
        "## ⚠️ ROOT CAUSE",
        "## 🧩 PATCH DIFF"
    ]

    for section in required_sections:
        if section not in text:
            print(f"❌ Missing section: {section}")
            return False

    if "diff --git" not in text:
        print("❌ Missing patch diff")
        return False

    return True


# ===============================
# MAIN
# ===============================
def main():
    if not API_KEY:
        raise ValueError("CLAUDE_API_KEY missing")

    code = read_file()

    print("🚀 Running production-grade audit...")

    output = call_claude(build_prompt(code))

    if not validate_output(output):
        print("❌ Invalid Claude output — failing pipeline")
        sys.exit(1)

    with open("CLAUDE_AUDIT.md", "w") as f:
        f.write(output)

    print("✅ Audit saved and validated")


if __name__ == "__main__":
    main()