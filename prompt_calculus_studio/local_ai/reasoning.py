"""Qt-free, versioned reasoning policy, independent of loaded engine settings."""

# None selects the versioned, model-specific default; -1 explicitly removes
# the thinking budget. Neither changes the total max_tokens limit.
DEFAULT_REASONING = {'enable_thinking': None, 'reasoning_budget_tokens': None}


def normalize_reasoning(value, *, max_tokens=None):
    """Separate, complete snapshot; never widen the six portable parameters."""
    if not isinstance(value, dict) or set(value) != set(DEFAULT_REASONING):
        raise ValueError('思考設定格式無效。')
    enabled, budget = value['enable_thinking'], value['reasoning_budget_tokens']
    if enabled is not None and type(enabled) is not bool:
        raise ValueError('思考模式必須是依模型、開啟或關閉。')
    if budget is not None and (type(budget) is not int or not -1 <= budget <= 262144):
        raise ValueError('思考上限必須是 -1 到 262144 的整數。')
    if enabled is False and budget not in (None, -1):
        raise ValueError('關閉思考時不能另外指定思考上限。')
    if max_tokens is not None and budget is not None and budget >= max_tokens:
        raise ValueError('思考上限必須小於回覆上限，為回答保留空間。')
    return dict(enable_thinking=enabled, reasoning_budget_tokens=budget)
