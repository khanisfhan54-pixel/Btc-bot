import os
import requests
import json
import sys

# ==========================================
# CONFIG
# ==========================================
API_KEY = os.getenv("CLAUDE_API_KEY")

TARGET_FILES = [
    "advanced_regime_engine.py"
]

MAX_INPUT_CHARS = 180000

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

    return code[:MAX_INPUT_CHARS]


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
2. STATE MANAGEMENT (mutable state, lifecycle bugs)
3. NUMERICAL STABILITY (NaN, inf, divide-by-zero, drift)
4. EDGE CASES (empty input, missing fields, extreme values)
5. CONCURRENCY / THREAD SAFETY
6. DATA VALIDATION & SANITIZATION
7. ERROR HANDLING (silent failures, swallowed exceptions)
8. PERFORMANCE BOTTLENECKS
9. MEMORY RISKS
10. DETERMINISM (same input → same output)
11. TIME-DEPENDENCY BUGS
12. CONFIG / PARAMETER MISUSE
13. ARCHITECTURAL FLAWS
14. INCOMPLETE / TRUNCATED IMPLEMENTATIONS
15. DEAD CODE / UNUSED PATHS
16. INCONSISTENT DATA FLOW
17. RISK ENGINE FAILURES (if applicable)

-------------------------------------
STRICT OUTPUT FORMAT (NO DEVIATION)
-------------------------------------

## AUDIT SUMMARY

For EACH issue:
- Severity: CRITICAL / HIGH / MEDIUM / LOW
- File:
- Function:
- Issue:
- Impact:

-------------------------------------

## ROOT CAUSE

For EACH issue:
- Exact technical cause
- Why it happens
- When it triggers
- Minimal reproducible scenario

-------------------------------------

## DETAILED FIX PLAN

For EACH issue:

Provide STEP-BY-STEP fix instructions:

- Exact logic correction
- Required condition checks
- State handling fixes
- Data validation additions
- Numerical safeguards
- Concurrency protections (if needed)
- Refactor suggestions (if required)

IMPORTANT:
- DO NOT output code
- DO NOT output patch diff
- DO NOT skip steps

-------------------------------------

STRICT RULES

- NO assumptions without evidence from code
- NO generic advice
- NO summaries
- MUST be exhaustive

-------------------------------------

CODE TO AUDIT:

{code}
"""


# ==========================================
# CLAUDE CALL
# ==========================================
def call_claude(prompt):
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
            timeout=120
        )

        data = response.json()

        if "error" in data:
            print("❌ Claude API Error:", data["error"])
            sys.exit(1)

        return data

    except Exception as e:
        print("❌ Request failed:", str(e))
        sys.exit(1)


# ==========================================
# EXTRACT TEXT
# ==========================================
def extract_text(response):
    try:
        return response["content"][0]["text"]
    except Exception:
        print("❌ Invalid Claude response format")
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
    print("🚀 Running production-grade audit...")

    if not API_KEY:
        print("❌ Missing CLAUDE_API_KEY")
        sys.exit(1)

    code = read_repo()

    if not code.strip():
        print("❌ No target files found")
        sys.exit(1)

    print(f"📏 Code length: {len(code)} chars")

    prompt = audit_prompt(code)

    response = call_claude(prompt)

    text = extract_text(response)

    # Save report
    with open("CLAUDE_AUDIT_REPORT.md", "w", encoding="utf-8") as f:
        f.write(text)

    print("📄 Audit report saved: CLAUDE_AUDIT_REPORT.md")

    # Validate structure
    errors = validate_output(text)

    if errors:
        print("⚠️ Output structure issues:")
        for e in errors:
            print(f"- {e}")
    else:
        print("✅ Audit completed successfully")


if __name__ == "__main__":
    main()
