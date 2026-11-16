"""Surface errors from manually-created unittest fixtures to the owning test."""
import unittest


def cleanup_nested(case):
    # TestCase.doCleanups() has no result when setUp was called directly. It
    # catches errors and returns False; addCleanup ignores that return value.
    # Keep LIFO and attempt every cleanup, but propagate the actual failures.
    errors=[]
    while case._cleanups:
        function,args,kwargs=case._cleanups.pop()
        try:
            owner=getattr(function,'__self__',None)
            if isinstance(owner,unittest.TestCase) and function.__name__=='doCleanups':
                cleanup_nested(owner)
            else:
                function(*args,**kwargs)
        except Exception as error:
            errors.append(error)
    if errors:raise ExceptionGroup('Nested fixture cleanup failed',errors)
