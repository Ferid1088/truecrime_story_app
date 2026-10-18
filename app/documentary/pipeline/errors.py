class JobCancelled(Exception):
    pass


class LanguageFailed(Exception):
    pass


class SpokenRejected(RuntimeError):
    """A spoken version its checkers did not approve after the redos."""


class AwaitingApproval(RuntimeError):
    """The owner has not yet approved the final version this job starts from."""
