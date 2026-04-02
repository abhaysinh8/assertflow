"""User-facing AssertFlow error categories."""


class AssertFlowError(Exception):
    """Base class for errors that should be shown without a traceback."""


class ConfigurationError(AssertFlowError):
    """Project or environment configuration is invalid."""


class SuiteValidationError(ConfigurationError):
    """A suite file cannot be parsed or validated."""


class VariableResolutionError(AssertFlowError):
    """A variable is undefined or recursively defined."""


class RequestExecutionError(AssertFlowError):
    """An HTTP request could not be completed."""


class RequestTimeoutError(RequestExecutionError):
    """An HTTP request exceeded its configured timeout."""


class AssertionFailure(AssertFlowError):
    """One or more response assertions failed."""


class ExtractionError(AssertFlowError):
    """A configured value cannot be extracted from a response."""


class RegressionFailure(AssertFlowError):
    """A response differs from its stored regression baseline."""


class MockError(AssertFlowError):
    """A mock route is invalid or its expectations were not met."""


class ReportingError(AssertFlowError):
    """A result report could not be generated."""
