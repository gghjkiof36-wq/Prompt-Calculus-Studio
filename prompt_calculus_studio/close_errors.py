"""Shutdown reports retain every failure after independent cleanup attempts."""


class CloseError(OSError):
    def __init__(self, failures):
        self.failures = tuple(failures)
        super().__init__('；'.join(name+'：'+str(error) for name, error in self.failures))
