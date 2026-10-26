"""Host-owned permission policy for future model-requested actions.

This module grants no execution capability. The current chat transport has no
tool executor and does not call this policy. A future executor must register
implemented capabilities, validate current owner/path/data contracts, then
obtain any requested user approval for this exact immutable action. It must
revalidate those contracts at execution/acceptance; ALLOW is not an owner lease.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class ApprovalMode(str, Enum):
    OFF = 'off'
    ASK = 'ask'
    AUTO = 'auto'
    ALL = 'all'


class Decision(str, Enum):
    DENY = 'deny'
    ASK = 'ask'
    ALLOW = 'allow'


def normalize_mode(value):
    try:
        return ApprovalMode(value).value
    except (TypeError, ValueError):
        raise ValueError('不支援的審查方式。') from None


@dataclass(frozen=True)
class ActionRequest:
    """An immutable proposed action, not permission to perform it.

    Scope is a bounded, flat snapshot of host identifiers/version/target names.
    The host validator decides which fields its capability requires. Neither
    natural-language descriptions nor model-provided flags establish safety.
    """
    operation: str
    scope: Mapping
    description: str
    write: bool
    external: bool

    def __post_init__(self):
        if (not isinstance(self.operation, str) or not self.operation.strip() or len(self.operation) > 128
                or not isinstance(self.description, str) or not self.description.strip() or len(self.description) > 2000
                or type(self.write) is not bool or type(self.external) is not bool
                or not isinstance(self.scope, Mapping) or not 1 <= len(self.scope) <= 64):
            raise ValueError('動作請求缺少明確的操作、範圍或影響描述。')
        snapshot = {}
        for key, value in self.scope.items():
            if (not isinstance(key, str) or not key or len(key) > 128
                    or type(value) not in (str, int, bool, type(None))
                    or isinstance(value, str) and len(value) > 32768):
                raise ValueError('動作範圍必須是有界的識別與目標快照。')
            snapshot[key] = value
        object.__setattr__(self, 'scope', MappingProxyType(snapshot))


@dataclass(frozen=True)
class Capability:
    """Registered by trusted host code only, never built from model payload.

    validate_scope must check current ownership, target/path constraints and
    data contracts, returning exactly True only when all required checks pass.
    auto_read is an explicit host classification of a safe local read. Leaving
    it False routes an otherwise valid AUTO action to ASK.
    """
    operation: str
    write: bool
    external: bool
    validate_scope: Callable[[ActionRequest], bool]
    auto_read: bool = False

    def __post_init__(self):
        if (not isinstance(self.operation, str) or not self.operation.strip()
                or type(self.write) is not bool or type(self.external) is not bool
                or type(self.auto_read) is not bool or not callable(self.validate_scope)
                or self.auto_read and (self.write or self.external)):
            raise ValueError('動作能力必須由程式明確定義影響與範圍檢查。')


@dataclass(frozen=True)
class ApprovalDecision:
    decision: Decision
    reason: str


class ApprovalPolicy:
    """Deterministic rules, not an LLM reviewer. Default capability set is empty."""
    def __init__(self, capabilities=()):
        catalog = {}
        for capability in capabilities:
            if not isinstance(capability, Capability) or capability.operation in catalog:
                raise ValueError('動作能力重複或格式無效。')
            catalog[capability.operation] = capability
        self._capabilities = MappingProxyType(catalog)

    def decide(self, request, mode=ApprovalMode.ASK):
        try:
            mode = normalize_mode(mode)
        except ValueError:
            return ApprovalDecision(Decision.DENY, 'invalid_mode')
        if not isinstance(request, ActionRequest):
            return ApprovalDecision(Decision.DENY, 'invalid_request')
        capability = self._capabilities.get(request.operation)
        if capability is None:
            return ApprovalDecision(Decision.DENY, 'unsupported_operation')
        if request.write != capability.write or request.external != capability.external:
            return ApprovalDecision(Decision.DENY, 'impact_mismatch')
        try:
            valid = capability.validate_scope(request) is True
        except Exception:
            valid = False
        if not valid:
            return ApprovalDecision(Decision.DENY, 'invalid_scope')
        # No preference, including ALL, bypasses capability or hard boundaries.
        if mode == ApprovalMode.OFF:
            return ApprovalDecision(Decision.DENY, 'actions_disabled')
        if mode == ApprovalMode.ALL:
            return ApprovalDecision(Decision.ALLOW, 'user_allows_supported_actions')
        if mode == ApprovalMode.AUTO and capability.auto_read and not capability.write and not capability.external:
            return ApprovalDecision(Decision.ALLOW, 'safe_local_read')
        return ApprovalDecision(Decision.ASK, 'ask_each_action' if mode == ApprovalMode.ASK else 'needs_user_review')
