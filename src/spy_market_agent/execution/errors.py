from __future__ import annotations


class PaperExecutionError(RuntimeError):
    """Base class for expected paper-execution failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class PaperExecutionInputError(PaperExecutionError):
    """Raised when a paper-execution boundary receives malformed input."""


class PaperExecutionConfigurationError(PaperExecutionError):
    """Raised when local configuration blocks paper execution."""


class PaperExecutionPermissionError(PaperExecutionError):
    """Raised when execution permission gates are not satisfied."""


class PaperExecutionApprovalError(PaperExecutionError):
    """Raised when explicit human approval is absent, invalid, or mismatched."""


class PaperExecutionRiskError(PaperExecutionError):
    """Raised when independent execution-time risk checks reject an order."""


class PaperExecutionDuplicateError(PaperExecutionError):
    """Raised when signal, client-order, or approval identifiers are already reserved."""


class PaperExecutionStaleSignalError(PaperExecutionError):
    """Raised when an instruction is expired or not for the current broker session."""


class PaperExecutionKillSwitchError(PaperExecutionError):
    """Raised when the durable global paper-execution kill switch is engaged."""


class PaperExecutionBrokerStateError(PaperExecutionError):
    """Raised when broker preflight state is unsafe or uncertain."""


class PaperExecutionBrokerTransportError(PaperExecutionError):
    """Raised when a read-only broker preflight call fails."""


class PaperExecutionBrokerRequestError(PaperExecutionError):
    """Raised when a paper order request cannot be constructed before submission."""


class PaperExecutionBrokerRejectionError(PaperExecutionError):
    """Raised when the broker definitively rejects a submitted paper order."""


class PaperExecutionSubmissionUnknownError(PaperExecutionError):
    """Raised when broker submission may have reached Alpaca but the outcome is unknown."""


class PaperExecutionIntegrityError(PaperExecutionError):
    """Raised when persisted paper-execution rows fail reconstruction."""


class PaperExecutionNotFoundError(PaperExecutionError):
    """Raised when a persisted paper-execution record is missing."""


__all__ = [
    "PaperExecutionApprovalError",
    "PaperExecutionBrokerRejectionError",
    "PaperExecutionBrokerRequestError",
    "PaperExecutionBrokerStateError",
    "PaperExecutionBrokerTransportError",
    "PaperExecutionConfigurationError",
    "PaperExecutionDuplicateError",
    "PaperExecutionError",
    "PaperExecutionInputError",
    "PaperExecutionIntegrityError",
    "PaperExecutionKillSwitchError",
    "PaperExecutionNotFoundError",
    "PaperExecutionPermissionError",
    "PaperExecutionRiskError",
    "PaperExecutionStaleSignalError",
    "PaperExecutionSubmissionUnknownError",
]
