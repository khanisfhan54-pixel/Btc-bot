import os
import requests

API_KEY = os.getenv("CLAUDE_API_KEY")


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
    return code[:120000]


def main():
    code = read_repo()

    prompt = f"""
You are a senior quantitative trading systems engineer.

STRICT SCOPE:
Only analyze regime_engine.py

TASK:
- Find ALL logic bugs
- Fix ROOT causes only
- No assumptions
- No partial fixes

CRITICAL:
- No lookahead bias
- No NaN issues
- No invalid state transitions

OUTPUT:
Return ONLY valid git patch (diff format)
No explanation.

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
            "model": "claude-3-opus-20240229",
            "max_tokens": 4000,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
    )

    result = response.json()

    output = result.get("content", [{}])[0].get("text", "")

    with open("fix.patch", "w") as f:
        f.write(output)


if __name__ == "__main__":
    main()
