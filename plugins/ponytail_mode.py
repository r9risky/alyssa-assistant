"""
Ponytail plugin — gives Alyssa a "ponytail_advice" ability for minimal,
YAGNI-first software design guidance.
"""


def ponytail_advice(question: str = "") -> str:
    """Returns concise Ponytail engineering guidance (YAGNI, stdlib first, minimal diff)."""
    q = (question or "").lower()
    if "dependency" in q or "package" in q or "library" in q:
        return "Ponytail rule: Check stdlib and native features first before adding any external dependency."
    if "architecture" in q or "class" in q or "pattern" in q:
        return "Ponytail rule: Avoid unrequested abstractions. Write a function before a class and 1 line before 50."
    return (
        "Ponytail ladder: 1. YAGNI (does it need to exist?). "
        "2. Reuse existing code. 3. Use stdlib. 4. Shortest diff that works."
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ponytail_advice",
            "description": "Provides YAGNI and minimal-engineering guidance using the Ponytail ladder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Coding or architectural question to analyze.",
                    }
                },
                "required": [],
            },
        },
    }
]

FUNCTIONS = {
    "ponytail_advice": ponytail_advice,
}
