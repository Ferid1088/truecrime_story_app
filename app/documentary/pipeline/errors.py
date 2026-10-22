from app.documentary.studio import StudioError


class JobCancelled(Exception):
    pass


class LanguageFailed(Exception):
    pass


class SpokenRejected(RuntimeError):
    """A spoken version its checkers did not approve after the redos."""


class AwaitingApproval(RuntimeError):
    """The owner has not yet approved the final version this job starts from."""


class AwaitingOutfit(StudioError):
    """No outfit is selected for this case and language: the run waits for the owner's
    choice in the Outfits panel and is resumable once every language has one. A
    StudioError so existing studio-error handling still applies."""
