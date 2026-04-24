import os
import requests
import json

API_KEY = os.getenv("CLAUDE_API_KEY")

API_URL = "https://api.anthropic.com/v1/messages"

# Try latest model first, fallback if not available
MODELS = [
    "claude-opus-4-6",                # 🔥 try latest (may fail)
    "claude-3-opus-20240229",         # ✅ fallback
    "claude-3-5-sonnet-20240620"      # 💡 cheap fallback
]


def read_repo():
    code = ""
    for root, _, files in os.walk("."):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8") as file:
                        code += f"\n# FILE: {path}\n"
                        code += file.read()
                except:
                    pass
    return code[:120000]  # avoid token overflow


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
                "max_tokens": 4096,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            }
        )

        data = response.json()

        # ✅ Success
        if response.status_code == 200:
            print(f"✅ Success with {model}")
            return data, model

        # ❌ Model not available → try next
        if "error" in data:
            print(f"❌ Failed with {model}: {data['error'].get('message')}")

    return {"error": "All models failed"}, None


def main():
    code = read_repo()

    prompt = f"""
You are a senior quant engineer.

Audit this trading system and:
- Find bugs
- Find logic flaws
- Suggest exact fixes (code patches)
- Be concise but precise

CODE:
{code}
"""

    result, model_used = call_claude(prompt)

    output = {
        "model_used": model_used,
        "result": result
    }

    with open("claude_output.json", "w") as f:
        json.dump(output, f, indent=2)

    print("✅ Output saved to claude_output.json")


if __name__ == "__main__":
    main()
