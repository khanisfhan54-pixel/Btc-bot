import os
import requests
import json

API_KEY = os.getenv("CLAUDE_API_KEY")

def read_repo():
    code = ""
    for root, _, files in os.walk("."):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8") as file:
                        code += f"\n# FILE: {path}\n\n"
                        code += file.read()
                except:
                    pass
    return code[:100000]  # limit tokens


def run_claude():
    code = read_repo()

    prompt = f"""
You are a senior quant engineer.

Audit this BTC trading bot code.
Find:
- bugs
- logic flaws
- missing risk management
- performance issues

Return clear fixes.

CODE:
{code}
"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-3-sonnet-20240229",
            "max_tokens": 2000,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
    )

    print("Status:", response.status_code)

    try:
        result = response.json()
    except:
        print("❌ Invalid response:", response.text)
        return

    print("Claude response received")

    # 🔥 SAVE OUTPUT (VERY IMPORTANT)
    with open("claude_output.json", "w") as f:
        json.dump(result, f, indent=2)

    print("✅ Saved to claude_output.json")


if __name__ == "__main__":
    run_claude()
