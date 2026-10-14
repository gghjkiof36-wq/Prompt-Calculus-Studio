"""Conservative, exact tag operations. Stored source text is never rewritten."""


def tokens(text):
    """Split only complete outer tags; quoted/escaped/nested commas stay intact.

    Malformed syntax is opaque: changing it would guess at the author's intent.
    Parenthesized expressions are atomic, including their weight and context.
    """
    stack, result, start, escaped, quote = [], [], 0, False, None
    pairs = {')': '(', ']': '[', '}': '{'}
    for at, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char == '"':
            quote = char
        elif char in '([{':
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return [text.strip()] if text.strip() else []
        elif char == ',' and not stack:
            if text[start:at].strip():
                result.append(text[start:at].strip())
            start = at + 1
    if stack or quote or escaped:
        return [text.strip()] if text.strip() else []
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def apply(text, disabled=(), deduplicate=False):
    if not disabled and not deduplicate:
        return text
    blocked, seen, result = set(disabled), set(), []
    for tag in tokens(text):
        if tag in blocked or deduplicate and tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return ', '.join(result)
