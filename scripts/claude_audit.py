import os
import requests
import json
import re

API_KEY = os.getenv("CLAUDE_API_KEY")

TARGET_FILES = [
    "advanced_regime_engine.py",
    "alpha_orchestrator.py"
]

# ===============================
# READ TARGET FILES
# ===============================
def read_repo():
    code = ""

    for root, _, files in os.walk("."):
        for f in files:
            if f in TARGET_FILES:
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as file:
                    code += f"\n\n# FILE: {path}\n\n"
                    code += file.read()

    return code[:200000]


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
            "max_tokens": 12000,
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    return response.json()


# ===============================
# MULTI PASS PROMPTS
# ===============================
def bug_pass(code):
    return f"""
Find ALL critical bugs and FIX them with FULL FILE OUTPUT.

ONLY output final fixed files.

CODE:
{code}
"""

def logic_pass(code):
    return f"""
Fix ALL logic issues and improve architecture.

ONLY output final fixed files.

CODE:
{code}
"""

def performance_pass(code):
    return f"""
Optimize performance and efficiency.

ONLY output final fixed files.

CODE:
{code}
"""

# ===============================
# PATCH EXTRACTOR
# ===============================
def extract_and_apply(text):
    pattern = r"## FILE: (.*?)\n(.*?)(?=## FILE:|\Z)"
    matches = re.findall(pattern, text, re.DOTALL)

    for filename, content in matches:
        filename = filename.strip()
        content = content.strip()

        print(f"✏️ Updating {filename}")

        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)


# ===============================
# MAIN
# ===============================
def main():
    if not API_KEY:
        raise ValueError("Missing API key")

    code = read_repo()

    print("🔴 Bug pass...")
    bug_result = call_claude(bug_pass(code))

    print("🟡 Logic pass...")
    logic_result = call_claude(logic_pass(code))

    print("🟢 Performance pass...")
    perf_result = call_claude(performance_pass(code))

    # Combine outputs
    combined = ""

    for r in [bug_result, logic_result, perf_result]:
        try:
            combined += r["content"][0]["text"] + "\n\n"
        except:
            pass

    # Save raw report
    with open("CLAUDE_REPORT.md", "w") as f:
        f.write(combined)

    print("📄 Report saved")

    # APPLY PATCHES
    extract_and_apply(combined)


if __name__ == "__main__":
    main()
