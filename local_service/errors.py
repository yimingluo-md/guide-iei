"""Exception types shared by the local service's stores and HTTP layer."""

from __future__ import annotations


class CohortMergeBusyError(Exception):
    """Retryable read refusal while shared cohort evidence is being changed."""

    def __init__(self) -> None:
        super().__init__(
            "The cohort index is finishing an import or recovering an interrupted "
            "import. Cohort Search and loading cohort findings for review are "
            "temporarily unavailable. Please retry after the import finishes. "
            "If the import failed, restart the workbench to complete recovery."
        )


class NotFoundError(KeyError):
    """A named resource (job, dataset, review file) does not exist.

    A subclass of ``KeyError`` so existing ``except KeyError`` handlers keep
    working, but distinct from the bare ``KeyError`` a missing request field
    raises: the HTTP layer answers the former with 404 and the latter with
    400 (audit M23 — every POST ``KeyError`` used to be reported as "job not
    found").
    """

    def __str__(self) -> str:  # KeyError quotes its argument; a message should not be
        return str(self.args[0]) if self.args else "not found"
