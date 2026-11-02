"""Concise presentation of new and retained execution messages."""
import re


def display_message(value):
    # Keep original journal errors and retry policy intact.
    return re.sub(r'[，；]?\s*不會自動重送[。]?', '', str(value)).strip()
