import os
import requests
import json

API_KEY = os.getenv("CLAUDE_API_KEY")

TARGET_FILES = [
    "advanced_regime_engine.py",
    "alpha_orchestrator.py"
]

MAX_INPUT_CHARS = 200000


# ===============================
# READ TARGET FILES
# ===============================
def read_repo():
    code = ""

    for root, _, files in os.walk("."):
        for f in files:
            if f in TARGET_FILES:
                path = os.path.join(root, f)

                try:
                    with open(path, "r", encoding="utf-8") as file:
                        code += f"\n\n# FILE: {path}\n\n"
                        code += file.read()
                except Exception as e:
                    print(f"⚠️ Failed to read {path}: {e}")

    return code[:MAX_INPUT_CHARS]


# ===============================
# BUILD STRICT PROMPT
# ===============================
def build_prompt(code):
    return f"""
You are a senior quantitative trading systems engineer and production Python auditor.

STRICT RULES (NON-NEGOTIABLE):
- DO NOT rewrite full files
- DO NOT output full implementations
- DO NOT apply fixes
- DO NOT suggest future improvements
- ONLY output:
    1. Audit summary
    2. Root cause
    3. Unified diff patch

OUTPUT FORMAT (STRICT):

## 🔍 AUDIT SUMMARY
- List ALL critical bugs
- Include file + function + issue

## ⚠️ ROOT CAUSE
- Explain WHY each issue exists

## 🧩 PATCH DIFF
- Provide ONLY unified git diff patches
- NO explanations in this section
- MUST be directly applicable with `git apply`

FORMAT EXAMPLE:

--- a/file.py
+++ b/file.py
@@
- old line
+ new line


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
            "messages": [
                {"role": "user", "content": prompt}
            ]
        },
        timeout=120
    )

    if response.status_code != 200:
        raise Exception(f"Claude API error: {response.text}")

    return response.json()


# ===============================
# EXTRACT RESPONSE TEXT
# ===============================
def extract_text(response):
    try:
        return response["content"][0]["text"]
    except Exception:
        raise Exception(f"Invalid Claude response: {json.dumps(response, indent=2)}")


# ===============================
# MAIN
# ===============================
def main():
    if not API_KEY:
        raise ValueError("Missing CLAUDE_API_KEY")

    print("📂 Reading repository...")
    code = read_repo()

    if not code.strip():
        raise ValueError("No target files found")

    print("🧠 Running Claude audit (Opus 4.6)...")
    prompt = build_prompt(code)

    result = call_claude(prompt)
    output = extract_text(result)

    # Save output only (NO AUTO PATCH)
    with open("CLAUDE_AUDIT.md", "w", encoding="utf-8") as f:
        f.write(output)

    print("✅ Audit complete")
    print("📄 Output saved to CLAUDE_AUDIT.md")
    print("⚠️ Manually review patch before applying")


if __name__ == "__main__":
    main()