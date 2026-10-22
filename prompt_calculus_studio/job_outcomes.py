"""Local worker terminal receipts; independent of Qt and service envelopes.

An explicit completed receipt means the worker owns the terminal result. Its
value can be a partial batch report and does not imply every item succeeded.
Only return it after the worker's cancellable/publish boundary is complete.
Ordinary unwrapped values retain the legacy late-cancellation rejection.
Payload ownership remains with the caller; this wrapper does not snapshot it.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class JobOutcome:
    state: str
    value: object = None
    message: str = ''

    def __post_init__(self):
        if self.state not in ('completed', 'cancelled', 'failed'):
            raise ValueError('Unknown job outcome state')
        if self.state == 'completed':
            if self.message:
                raise ValueError('A completed receipt cannot contain an error')
        elif self.value is not None or not isinstance(self.message, str) or not self.message:
            raise ValueError('A failed/cancelled receipt requires a message and no value')

    @classmethod
    def completed(cls, value):
        return cls('completed', value=value)

    @classmethod
    def failed(cls, message):
        return cls('failed', message=str(message) or '操作未能完成。')

    @classmethod
    def cancelled(cls, message='已取消操作。'):
        return cls('cancelled', message=str(message) or '已取消操作。')


def run_job(function, cancel, *, finish_accepted=False):
    """Run once; only an already accepted transaction may require finalization.

    finish_accepted is for owner-bound bookkeeping after a durable acceptance,
    not permission to begin an ordinary cancelled writer. Such workers must
    still return their explicit completed/failed terminal receipt.
    """
    if cancel.is_set() and not finish_accepted:
        return JobOutcome.cancelled()
    try:
        value = function(cancel)
    except Exception as error:
        message = str(error) or type(error).__name__
        # Preserve existing cooperative cancellation types and UI messages.
        if isinstance(error, InterruptedError) or message.startswith('已取消'):
            return JobOutcome.cancelled(message)
        return JobOutcome.failed(message)
    if isinstance(value, JobOutcome):
        return value
    if cancel.is_set():
        return JobOutcome.cancelled()
    return JobOutcome.completed(value)
