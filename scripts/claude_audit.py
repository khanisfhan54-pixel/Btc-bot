import os
import requests
import json

API_KEY = os.getenv("CLAUDE_API_KEY")
API_URL = "https://api.anthropic.com/v1/messages"

# Model priority (your Opus first)
MODELS = [
    "claude-opus-4-6",
    "claude-3-opus-20240229",
    "claude-3-5-sonnet-20240620"
]

MAX_CHARS_PER_CHUNK = 60000


def get_python_files():
    files = []
    for root, _, filenames in os.walk("."):
        for f in filenames:
            if f.endswith(".py") and "venv" not in root:
                files.append(os.path.join(root, f))
    return files


def read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""


def chunk_code(files):
    chunks = []
    current_chunk = ""

    for file in files:
        content = read_file(file)
        block = f"\n# FILE: {file}\n{content}\n"

        if len(current_chunk) + len(block) > MAX_CHARS_PER_CHUNK:
            chunks.append(current_chunk)
            current_chunk = block
        else:
            current_chunk += block

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def call_claude(prompt):
    for model in MODELS:
        print(f"Trying model: {model}")

        response = requests.post(
            API_URL,
            headers={
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": model,
                "max_tokens": 8000,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
        )

        data = response.json()

        if response.status_code == 200:
            print(f"✅ Success with {model}")
            return data, model

        if "error" in data:
            print(f"❌ {model} failed: {data['error'].get('message')}")

    return None, None


def build_prompt(code_chunk):
    return f"""
You are a senior quantitative trading systems engineer.

STRICT REQUIREMENTS:
- Identify REAL bugs only (no generic advice)
- Provide exact code fixes (diff or replacement)
- Focus on execution, signal generation, risk logic
- Ignore style or formatting

ALSO:
- Suggest performance improvements
- Suggest robustness upgrades (edge cases, failure handling)
- Suggest architecture improvements ONLY if critical

OUTPUT FORMAT:
1. File
2. Issue
3. Root Cause
4. Fix (code)

CODE:
{code_chunk}
"""


def extract_text(response_json):
    try:
        return response_json["content"][0]["text"]
    except:
        return json.dumps(response_json, indent=2)


def main():
    print("🚀 Starting Claude audit...")

    files = get_python_files()
    chunks = chunk_code(files)

    full_report = ""
    used_model = None

    for i, chunk in enumerate(chunks):
        print(f"\n🔍 Processing chunk {i+1}/{len(chunks)}")

        prompt = build_prompt(chunk)

        result, model = call_claude(prompt)

        if not result:
            print("❌ All models failed")
            continue

        if not used_model:
            used_model = model

        text = extract_text(result)

        full_report += f"\n\n# ===== CHUNK {i+1} =====\n"
        full_report += text

    with open("CLAUDE_REPORT.md", "w") as f:
        f.write(full_report)

    print("\n✅ Audit complete")
    print(f"📄 Report saved to CLAUDE_REPORT.md")
    print(f"🧠 Model used: {used_model}")


if __name__ == "__main__":
    main()
