"""
Custom exception classes for IRP Integration module.

These exceptions provide clear, structured error handling for different
failure scenarios when interacting with Moody's Risk Modeler API.
"""

from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from .grouping import GroupingProblem


class IRPIntegrationError(Exception):
    """Base exception for all IRP integration errors."""
    pass


class IRPAPIError(IRPIntegrationError):
    """
    API request or response errors.

    Raised when HTTP requests fail, responses are malformed,
    or API returns unexpected status codes.
    """
    pass


class IRPAuthenticationError(IRPIntegrationError):
    """
    Bearer-token authentication errors.

    Raised when bearer-token login or token refresh fails (bad
    credentials, missing access token in the response, etc.).
    """
    pass


class IRPValidationError(IRPIntegrationError):
    """
    Input validation errors.

    Raised when method parameters fail validation checks
    (e.g., empty strings, invalid IDs, missing files).
    """
    pass


class IRPGroupingValidationError(IRPValidationError):
    """Rule-based grouping validation errors with structured problems."""

    def __init__(self, problems: Sequence["GroupingProblem"]) -> None:
        """Initialize the error from one or more grouping problems.

        Args:
            problems: Structured problems that prevented grouping submission
        """
        self.problems = tuple(problems)
        message = "; ".join(problem.message for problem in self.problems)
        super().__init__(message or "Grouping validation failed")


class IRPWorkflowError(IRPIntegrationError):
    """
    Workflow execution errors.

    Raised when workflows fail to complete successfully,
    timeout, or return error status.
    """
    pass


class IRPReferenceDataError(IRPIntegrationError):
    """
    Reference data lookup errors.

    Raised when required reference data (treaty types, currencies, etc.)
    cannot be found or retrieved.
    """
    pass


class IRPFileError(IRPIntegrationError):
    """
    File operation errors.

    Raised when file operations fail (file not found, invalid format,
    upload errors, etc.).
    """
    pass


class IRPJobError(IRPIntegrationError):
    """
    Job management errors.

    Raised when job submission, status retrieval,
    or result fetching encounters issues.
    """
    pass


class IRPDataBridgeError(IRPIntegrationError):
    """
    Data Bridge (SQL Server) operation errors.

    Base exception for all SQL Server / Data Bridge failures
    including connection, configuration, and query errors.
    """
    pass


class IRPDataBridgeConnectionError(IRPDataBridgeError):
    """
    Data Bridge connection errors.

    Raised when SQL Server connection fails (bad credentials,
    unreachable server, driver not installed).
    """
    pass


class IRPDataBridgeQueryError(IRPDataBridgeError):
    """
    Data Bridge query execution errors.

    Raised when SQL query execution fails, parameter substitution
    fails, or SQL file cannot be read.
    """
    pass
