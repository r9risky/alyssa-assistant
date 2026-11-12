"""Dice rolls and coin flips - 'roll 2d20', 'flip a coin'."""
import random
import re

PERMISSIONS = []


def roll_dice(dice: str = "1d6") -> str:
    match = re.fullmatch(r"\s*(\d{0,2})\s*d\s*(\d{1,4})\s*", dice or "1d6", re.IGNORECASE)
    if not match:
        return "Tell me dice like '2d6' or 'd20'."
    count, sides = int(match.group(1) or 1), int(match.group(2))
    if not 1 <= count <= 20 or sides < 2:
        return "I can roll 1 to 20 dice with at least 2 sides."
    rolls = [random.randint(1, sides) for _ in range(count)]
    if count == 1:
        return f"You rolled a {rolls[0]}."
    return f"You rolled {', '.join(map(str, rolls))} - {sum(rolls)} total."


def flip_coin() -> str:
    return f"It's {random.choice(['heads', 'tails'])}."


FUNCTIONS = {"roll_dice": roll_dice, "flip_coin": flip_coin}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Rolls dice in NdS notation, e.g. 'roll a d20', 'roll 3d6'.",
            "parameters": {
                "type": "object",
                "properties": {"dice": {"type": "string", "description": "Like '1d6', '2d20', 'd12'."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flip_coin",
            "description": "Flips a coin - 'flip a coin', 'heads or tails'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
