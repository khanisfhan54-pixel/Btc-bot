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
# PROMPT
# ===============================
def build_prompt(code):
    return f"""
You are a senior production-grade Python auditor.

STRICT RULES:
- DO NOT rewrite full files
- DO NOT modify code
- ONLY output:
  1. Audit summary
  2. Root cause
  3. Patch diff

FORMAT:

## 🔍 AUDIT SUMMARY

## ⚠️ ROOT CAUSE

## 🧩 PATCH DIFF
(standard unified diff only)

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
# EXTRACT TEXT
# ===============================
def extract_text(response):
    try:
        return response["content"][0]["text"]
    except Exception:
        raise Exception(f"Invalid response: {json.dumps(response, indent=2)}")


# ===============================
# MAIN (THIS IS WHAT YOU ASKED ABOUT)
# ===============================
def main():
    try:
        # 🔴 1. Check API key
        if not API_KEY:
            raise ValueError("Missing CLAUDE_API_KEY")

        # 🔴 2. Read repo files
        print("📂 Reading repository...")
        code = read_repo()

        if not code.strip():
            raise ValueError("No target files found")

        # 🔴 3. Call Claude
        print("🧠 Running Claude audit...")
        prompt = build_prompt(code)

        result = call_claude(prompt)
        output = extract_text(result)

        # 🔴 4. Save output (NO auto patching)
        with open("CLAUDE_AUDIT.md", "w", encoding="utf-8") as f:
            f.write(output)

        print("✅ Audit complete")

    except Exception as e:
        # 🔴 This is CRITICAL for debugging GitHub Actions
        print("❌ ERROR:", str(e))
        raise


if __name__ == "__main__":
    main()