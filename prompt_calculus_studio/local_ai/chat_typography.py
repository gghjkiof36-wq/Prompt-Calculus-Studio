"""Shared fallbacks for native and browser chat projections."""

BODY_FALLBACKS = ('Segoe UI', 'Microsoft JhengHei UI', 'Microsoft JhengHei', 'Noto Sans CJK TC')
CODE_FAMILIES = ('Cascadia Mono', 'Consolas', *BODY_FALLBACKS)


def body_families(font):
    # Preserve the user's first choice; explicitly cover Traditional Chinese so
    # a browser's serif default cannot replace Qt's UI fallback.
    return list(dict.fromkeys([*font.families(), *BODY_FALLBACKS]))
