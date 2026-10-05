"""Parameter presentation only; never use display text as a saved value."""
import math


def display_value(value, kind):
    """Hide binary float tails without rounding meaningful precision or drafts.

    Only substantially shorter text within one representable float step is
    used. The caller retains the exact value until the user edits the field.
    Integers (including seeds), text and in-progress input stay untouched.
    """
    original = str(value)
    if kind != 'FLOAT' or type(value) is not float or not math.isfinite(value):
        return original
    compact = format(value, '.15g')
    if len(original) - len(compact) >= 4 and abs(float(compact) - value) <= math.ulp(value):
        return compact
    return original
