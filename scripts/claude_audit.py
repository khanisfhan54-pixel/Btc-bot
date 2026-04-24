import os
import requests
import json

API_KEY = os.getenv("CLAUDE_API_KEY")

# ==========================================
# READ ONLY TARGET MODULES
# ==========================================
def read_repo():
    TARGET_FILES = [
        "advanced_regime_engine.py",
        "alpha_orchestrator.py"
    ]

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
                    print(f"⚠️ Skipping {path}: {e}")

    return code[:200000]


# ==========================================
# BUILD STRICT AUDIT PROMPT
# ==========================================
def build_prompt(code):
    return f"""
You are a senior quantitative trading systems engineer, Python backend auditor, and automated testing specialist.

⚠️ STRICT SCOPE:
You MUST ONLY analyze:
- advanced_regime_engine.py
- alpha_orchestrator.py

DO NOT mention or suggest changes outside these modules.

---

## OBJECTIVE

Audit, debug, and upgrade ONLY these modules to production-grade quality.

---

## REQUIREMENTS (NON-NEGOTIABLE)

- No partial fixes
- No TODOs
- No placeholders
- No vague suggestions
- Deliver FULL working code

---

## OUTPUT FORMAT

# 🚨 CRITICAL BUGS
- Exact issue
- Root cause
- FULL FIXED CODE

# ⚠️ LOGIC ISSUES
- Explain flaw
- Provide corrected implementation

# ⚡ PERFORMANCE IMPROVEMENTS
- Bottlenecks
- Optimized code

# 🧪 TEST FIXES
- Fix failing tests (if relevant)

# 🧠 FINAL PATCHED FILES
Return FULL FILES, not snippets

---

## CODE

{code}
"""


# ==========================================
# CALL CLAUDE API
# ==========================================
def call_claude(prompt):
    url = "https://api.anthropic.com/v1/messages"

    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    payload = {
        "model": "claude-opus-4-6",
        "max_tokens": 16000,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    response = requests.post(url, headers=headers, json=payload)

    print(f"Status: {response.status_code}")

    try:
        result = response.json()
    except Exception:
        print("❌ Failed to parse JSON")
        print(response.text)
        return None

    return result


# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    if not API_KEY:
        raise ValueError("❌ CLAUDE_API_KEY not set")

    print("🚀 Reading target modules...")
    code = read_repo()

    if not code.strip():
        raise ValueError("❌ Target files not found")

    print("🧠 Building prompt...")
    prompt = build_prompt(code)

    print("🤖 Calling Claude...")
    result = call_claude(prompt)

    if result is None:
        print("❌ No result received")
        return

    # SAFE EXTRACTION
    try:
        text = result["content"][0]["text"]
        print("✅ Clean text extracted")
    except Exception as e:
        print("⚠️ Fallback to raw JSON:", str(e))
        text = json.dumps(result, indent=2)

    # SAVE REPORT
    with open("CLAUDE_REPORT.md", "w", encoding="utf-8") as f:
        f.write(text)

    print("✅ Report saved: CLAUDE_REPORT.md")


# ==========================================
# ENTRY POINT
# ==========================================
if __name__ == "__main__":
    main()
