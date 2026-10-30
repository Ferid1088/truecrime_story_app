from app.documentary.studio import StudioError


class JobCancelled(Exception):
    pass


class JobPaused(BaseException):
    """The case was paused (or the global stop is on): the job stops at this step
    boundary and waits in status "paused" until resume puts it back in the queue.

    BaseException like asyncio.CancelledError: this is control flow, not a stage
    failure — inner ``except Exception`` handlers must let it propagate."""


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


class AwaitingPictureReview(RuntimeError):
    """Moments of the film are still empty after every real picture, video and alternative graphic the case's material
    allows: the run waits (before anything is rendered) for the owner to review the pictures the planner rejected or
    was unsure about, and is resumable once they have."""


class AwaitingPortrait(RuntimeError):
    """A gap of the film has no portrait that may be shown (every photo the owner chose is refused or would give the story away at
    that moment): the run stops before anything is rendered and is resumable once the owner has chosen another first photo."""
