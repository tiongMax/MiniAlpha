"""Expected failures at the financial-data boundary."""


class FinancialDataError(Exception):
    """Base class for failures safe to explain to the agent."""


class InvalidSymbolError(FinancialDataError):
    """Raised when input cannot be normalized into an accepted ticker."""


class InvalidPriceQueryError(FinancialDataError):
    """Raised when a requested price period or interval is unsupported."""


class InvalidFundamentalQueryError(FinancialDataError):
    """Raised when a fundamental dataset query exceeds supported bounds."""


class InvalidQuantitativeQueryError(FinancialDataError):
    """Raised when a quantitative calculation has unsafe or unusable inputs."""


class SymbolNotFoundError(FinancialDataError):
    """Raised when a provider has no company data for a valid ticker."""


class FinancialProviderError(FinancialDataError):
    """Raised when an upstream financial-data provider fails unexpectedly."""


class FinancialProviderTimeout(FinancialProviderError):
    """Raised when a provider call exceeds its configured time limit."""
