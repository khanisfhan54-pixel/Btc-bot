import os
import requests
import json
import sys
import time

# ==========================================
# CONFIG
# ==========================================
API_KEY = os.getenv("CLAUDE_API_KEY")

TARGET_FILES = [
    "alpha_orchestrator.py"
]

MAX_INPUT_CHARS = 120000   # safer for large models
CHUNK_SIZE = 100000
MAX_RETRIES = 3


# ==========================================
# READ TARGET FILES
# ==========================================
def read_repo():
    code = ""

    for root, _, files in os.walk("."):
        for f in files:
            if f in TARGET_FILES:
                path = os.path.join(root, f)

                try:
                    with open(path, "r", encoding="utf-8") as file:
                        content = file.read()

                        code += f"\n\n# FILE: {path}\n\n"
                        code += content

                except Exception as e:
                    print(f"⚠️ Failed reading {path}: {e}")

    return code


# ==========================================
# SPLIT LARGE CODE (prevents timeout)
# ==========================================
def split_code(code):
    chunks = []
    for i in range(0, len(code), CHUNK_SIZE):
        chunks.append(code[i:i + CHUNK_SIZE])
    return chunks


# ==========================================
# PROMPT
# ==========================================
def audit_prompt(code):
    return f"""
You are a SENIOR QUANTITATIVE SYSTEMS AUDITOR.

You are auditing a PRODUCTION-CRITICAL trading system.

Your job is NOT to summarize.
Your job is to FIND ALL FAILURES.

-------------------------------------
MANDATORY AUDIT DIMENSIONS
-------------------------------------

You MUST audit across ALL of the following:

1. LOGIC CORRECTNESS
2. STATE MANAGEMENT
3. NUMERICAL STABILITY
4. EDGE CASES
5. CONCURRENCY / THREAD SAFETY
6. DATA VALIDATION
7. ERROR HANDLING
8. PERFORMANCE BOTTLENECKS
9. MEMORY RISKS
10. DETERMINISM
11. TIME-DEPENDENCY BUGS
12. CONFIG MISUSE
13. ARCHITECTURAL FLAWS
14. INCOMPLETE IMPLEMENTATIONS
15. DEAD CODE
16. DATA FLOW ISSUES
17. RISK ENGINE FAILURES

-------------------------------------
STRICT OUTPUT FORMAT
-------------------------------------

## AUDIT SUMMARY
(list ALL issues)

## ROOT CAUSE
(full technical explanation)

## DETAILED FIX PLAN
(step-by-step fixes)

-------------------------------------

STRICT RULES:
- NO assumptions
- NO generic advice
- MUST be exhaustive

-------------------------------------

CODE:
{code}
"""


# ==========================================
# CLAUDE CALL (WITH RETRIES)
# ==========================================
def call_claude(prompt):
    for attempt in range(MAX_RETRIES):
        try:
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
                    "messages": [
                        {"role": "user", "content": prompt}
                    ]
                },
                timeout=180
            )

            data = response.json()

            if "error" in data:
                print("❌ Claude API Error:", data["error"])
                return None

            return data

        except Exception as e:
            print(f"⚠️ Retry {attempt+1}/{MAX_RETRIES} failed:", e)
            time.sleep(5)

    return None


# ==========================================
# EXTRACT TEXT
# ==========================================
def extract_text(response):
    try:
        return response["content"][0]["text"]
    except Exception:
        print("❌ Invalid Claude response")
        print(json.dumps(response, indent=2))
        sys.exit(1)


# ==========================================
# VALIDATION
# ==========================================
def validate_output(text):
    errors = []

    if "AUDIT SUMMARY" not in text:
        errors.append("Missing AUDIT SUMMARY")

    if "ROOT CAUSE" not in text:
        errors.append("Missing ROOT CAUSE")

    if "DETAILED FIX PLAN" not in text:
        errors.append("Missing DETAILED FIX PLAN")

    return errors


# ==========================================
# MAIN
# ==========================================
def main():
    print("🚀 Running alpha orchestrator audit...")

    if not API_KEY:
        print("❌ Missing CLAUDE_API_KEY")
        sys.exit(1)

    code = read_repo()

    if not code.strip():
        print("❌ No target files found")
        sys.exit(1)

    print(f"📏 Total code length: {len(code)} chars")

    chunks = split_code(code)

    full_report = ""

    for i, chunk in enumerate(chunks):
        print(f"🔍 Auditing chunk {i+1}/{len(chunks)}")

        prompt = audit_prompt(chunk)
        response = call_claude(prompt)

        if not response:
            print("❌ Claude failed on chunk")
            sys.exit(1)

        text = extract_text(response)

        full_report += f"\n\n# ===== CHUNK {i+1} =====\n\n"
        full_report += text

    # Save report
    with open("CLAUDE_AUDIT_REPORT.md", "w", encoding="utf-8") as f:
        f.write(full_report)

    print("📄 Audit report saved: CLAUDE_AUDIT_REPORT.md")

    # Validate
    errors = validate_output(full_report)

    if errors:
        print("⚠️ Output issues:")
        for e in errors:
            print("-", e)
    else:
        print("✅ Audit completed successfully")


if __name__ == "__main__":
    main()