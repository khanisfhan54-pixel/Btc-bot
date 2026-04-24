import os
import requests
import json

API_KEY = os.getenv("CLAUDE_API_KEY")

# ===============================
# READ FULL REPO CODE
# ===============================
def read_repo():
    code = ""
    for root, _, files in os.walk("."):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8") as file:
                        code += f"\n\n# FILE: {path}\n\n"
                        code += file.read()
                except:
                    pass

    return code[:200000]  # prevent overflow


# ===============================
# BUILD PROMPT (YOUR CORE POWER)
# ===============================
def build_prompt(code):
    return f"""
You are a senior quantitative engineer, production backend architect, and testing specialist.

Your job is NOT to suggest ideas or plans.
Your job is to deliver COMPLETE, production-ready implementations.

## CORE STANDARD (NON-NEGOTIABLE)
- No partial solutions
- No TODOs
- No placeholders
- Fix everything completely

---

## TASK

Audit, debug, and upgrade this entire codebase.

### REQUIRED OUTPUT FORMAT:

# 🚨 CRITICAL BUGS
- Exact issue
- Why it happens
- FULL FIXED CODE

# ⚠️ LOGIC ISSUES
- Explain flaw
- Provide corrected implementation

# ⚡ PERFORMANCE IMPROVEMENTS
- Bottlenecks
- Optimized code

# 🧪 TEST FIXES
- Fix failing tests completely

# 🧠 FINAL PATCHED FILES
Return FULL FILES, not snippets.

---

## CODEBASE:

{code}
"""


# ===============================
# CALL CLAUDE API
# ===============================
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
    except:
        print("❌ Failed to parse JSON")
        print(response.text)
        return None, "error"

    return result, payload["model"]


# ===============================
# MAIN EXECUTION
# ===============================
def main():
    if not API_KEY:
        raise ValueError("❌ CLAUDE_API_KEY not set")

    print("🚀 Reading repo...")
    code = read_repo()

    print("🧠 Building prompt...")
    prompt = build_prompt(code)

    print("🤖 Calling Claude...")
    result, model_used = call_claude(prompt)

    if result is None:
        print("❌ No result received")
        return

    # ===============================
    # SAFE EXTRACTION (THIS IS YOUR TRY PART)
    # ===============================
    try:
        text = result["content"][0]["text"]
        print("✅ Clean text extracted")
    except Exception as e:
        print("⚠️ Fallback to raw JSON:", str(e))
        text = json.dumps(result, indent=2)

    # ===============================
    # SAVE CLEAN REPORT
    # ===============================
    with open("CLAUDE_REPORT.md", "w", encoding="utf-8") as f:
        f.write(text)

    print("✅ Report saved: CLAUDE_REPORT.md")


# ===============================
# ENTRY POINT
# ===============================
if __name__ == "__main__":
    main()
