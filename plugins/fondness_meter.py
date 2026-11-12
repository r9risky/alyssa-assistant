"""Lets her answer 'how much do you like me?' from the fondness meter (fondness.py)."""
import fondness


def get_fondness() -> str:
    return fondness.describe()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_fondness",
            "description": (
                "Tells how close you feel to the user on the fondness meter - e.g. "
                "'how much do you like me', 'are we friends', 'what's our friendship level'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

FUNCTIONS = {"get_fondness": get_fondness}
