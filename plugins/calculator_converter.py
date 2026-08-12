"""
Smart Calculator & Unit Converter plugin for Alyssa.

Gives Alyssa abilities to:
- Safely compute mathematical expressions ('calculate_math')
- Convert between units of temperature, distance, mass, and volume ('convert_units')
"""
import ast
import math
import re


# Safe AST math evaluator (no arbitrary code execution risk)
_ALLOWED_OPERATORS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}

_ALLOWED_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
}

_ALLOWED_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant value: {node.value}")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type in _ALLOWED_OPERATORS:
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            return _ALLOWED_OPERATORS[op_type](left, right)
        raise ValueError(f"Unsupported operator: {op_type.__name__}")
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type in _ALLOWED_OPERATORS:
            operand = _eval_node(node.operand)
            return _ALLOWED_OPERATORS[op_type](operand)
        raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
    elif isinstance(node, ast.Name):
        name = node.id.lower()
        if name in _ALLOWED_CONSTANTS:
            return _ALLOWED_CONSTANTS[name]
        raise ValueError(f"Unknown variable or constant: '{node.id}'")
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            func_name = node.func.id.lower()
            if func_name in _ALLOWED_FUNCTIONS:
                args = [_eval_node(arg) for arg in node.args]
                return _ALLOWED_FUNCTIONS[func_name](*args)
        raise ValueError("Unsupported function call")
    raise ValueError("Invalid mathematical expression")


def calculate_math(expression: str) -> str:
    """Evaluates a mathematical expression safely."""
    if not expression or not expression.strip():
        return "Please provide a mathematical expression to calculate."

    cleaned = expression.strip()
    # Handle percentage expressions like "15% of 250"
    pct_match = re.match(r"^([\d.]+)\s*%\s*of\s*([\d.]+)$", cleaned, re.IGNORECASE)
    if pct_match:
        pct = float(pct_match.group(1))
        val = float(pct_match.group(2))
        res = (pct / 100.0) * val
        return f"{pct:g}% of {val:g} is {res:g}."

    # Replace common spoken operator words
    cleaned = re.sub(r"\bx\b", "*", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\btimes\b", "*", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bdivided by\b", "/", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bplus\b", "+", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bminus\b", "-", cleaned, flags=re.IGNORECASE)

    try:
        parsed = ast.parse(cleaned, mode='eval')
        result = _eval_node(parsed.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{expression.strip()} = {result}"
    except Exception as e:
        return f"Couldn't calculate '{expression}': {e}"


# Unit Conversion Definitions
_LENGTH_TO_METERS = {
    "m": 1.0, "meter": 1.0, "km": 1000.0, "kilometer": 1000.0,
    "cm": 0.01, "centimeter": 0.01, "mm": 0.001, "millimeter": 0.001,
    "mile": 1609.344, "mi": 1609.344, "foot": 0.3048, "feet": 0.3048, "ft": 0.3048,
    "inch": 0.0254, "in": 0.0254, "yard": 0.9144, "yd": 0.9144,
}
_LENGTH_TO_METERS.update({k + "s": v for k, v in list(_LENGTH_TO_METERS.items()) if not k.endswith("s")})

_MASS_TO_KG = {
    "kg": 1.0, "kilogram": 1.0, "kilo": 1.0, "g": 0.001, "gram": 0.001,
    "mg": 0.000001, "milligram": 0.000001, "lb": 0.45359237, "pound": 0.45359237,
    "oz": 0.028349523125, "ounce": 0.028349523125,
}
_MASS_TO_KG.update({k + "s": v for k, v in list(_MASS_TO_KG.items()) if not k.endswith("s")})

_VOLUME_TO_LITERS = {
    "l": 1.0, "liter": 1.0, "litre": 1.0, "ml": 0.001, "milliliter": 0.001,
    "gal": 3.78541, "gallon": 3.78541, "cup": 0.236588,
    "fl oz": 0.0295735, "floz": 0.0295735, "fluid ounce": 0.0295735,
}
_VOLUME_TO_LITERS.update({k + "s": v for k, v in list(_VOLUME_TO_LITERS.items()) if not k.endswith("s")})


def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Converts a value between measurement units."""
    try:
        val = float(value)
    except (ValueError, TypeError):
        return f"Invalid number value: '{value}'."

    src = (from_unit or "").strip().lower()
    dst = (to_unit or "").strip().lower()

    # Temperature conversion
    temp_units = {"c", "celsius", "f", "fahrenheit", "k", "kelvin"}
    if src in temp_units and dst in temp_units:
        # Convert src -> Celsius first
        if src in ("c", "celsius"):
            c_val = val
        elif src in ("f", "fahrenheit"):
            c_val = (val - 32.0) * (5.0 / 9.0)
        else:  # kelvin
            c_val = val - 273.15

        # Convert Celsius -> dst
        if dst in ("c", "celsius"):
            res = c_val
            unit_sym = "°C"
        elif dst in ("f", "fahrenheit"):
            res = (c_val * 9.0 / 5.0) + 32.0
            unit_sym = "°F"
        else:
            res = c_val + 273.15
            unit_sym = "K"

        return f"{val:g} {from_unit} is {res:.2f} {unit_sym}."

    # Length conversion
    if src in _LENGTH_TO_METERS and dst in _LENGTH_TO_METERS:
        meters = val * _LENGTH_TO_METERS[src]
        res = meters / _LENGTH_TO_METERS[dst]
        return f"{val:g} {from_unit} is {res:.4g} {to_unit}."

    # Mass conversion
    if src in _MASS_TO_KG and dst in _MASS_TO_KG:
        kg = val * _MASS_TO_KG[src]
        res = kg / _MASS_TO_KG[dst]
        return f"{val:g} {from_unit} is {res:.4g} {to_unit}."

    # Volume conversion
    if src in _VOLUME_TO_LITERS and dst in _VOLUME_TO_LITERS:
        liters = val * _VOLUME_TO_LITERS[src]
        res = liters / _VOLUME_TO_LITERS[dst]
        return f"{val:g} {from_unit} is {res:.4g} {to_unit}."

    return f"Sorry, I don't know how to convert from '{from_unit}' to '{to_unit}'."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_math",
            "description": "Calculates the result of a mathematical expression or percentage, e.g. 'what is 457 divided by 13', 'calculate 15% of 250', 'sqrt(144)'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "The mathematical expression to evaluate (e.g. '457 / 13', '15% of 250')."}
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_units",
            "description": "Converts values between units of measurement (temperature, distance, weight, volume), e.g. 'convert 75 fahrenheit to celsius', 'how many km is 26 miles'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "The numerical value to convert (e.g. 75)."},
                    "from_unit": {"type": "string", "description": "The starting unit (e.g. 'fahrenheit', 'miles', 'kg')."},
                    "to_unit": {"type": "string", "description": "The target unit to convert into (e.g. 'celsius', 'km', 'lbs')."},
                },
                "required": ["value", "from_unit", "to_unit"],
            },
        },
    },
]

FUNCTIONS = {
    "calculate_math": calculate_math,
    "convert_units": convert_units,
}
