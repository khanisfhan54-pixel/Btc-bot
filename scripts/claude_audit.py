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

    return code[:120000]


def call_claude(code):
    prompt = f"""
You are a senior quantitative trading engineer.

Analyze this BTC trading bot code.

Tasks:
1. Find bugs
2. Find logical errors
3. Identify performance issues
4. Suggest fixes
5. Output FIXED CODE (only modified parts)

Respond in JSON:
{{
  "issues": [...],
  "fixes": "...python code..."
}}

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
            "model": "claude-3-haiku-20240307",
            "max_tokens": 2000,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
    )

    return response.json()


def save_output(result):
    with open("claude_output.json", "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    code = read_repo()
    result = call_claude(code)
    save_output(result)

    print("Claude analysis saved.")
