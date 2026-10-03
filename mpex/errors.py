"""Exceptions raised when a command would violate an exchange rule."""


class ExchangeError(Exception):
    """Base class for every rule violation reported by the exchange."""


class ValidationError(ExchangeError):
    """Input is malformed or refers to something that does not exist."""


class InsufficientFunds(ExchangeError):
    """An owner lacks unencumbered assets at the required settlement."""


class QuotaExceeded(ExchangeError):
    """A packet origination would exceed a communication quota."""


class InvalidState(ExchangeError):
    """The object is not in a state that allows the requested transition."""


class TimeOrderError(ExchangeError):
    """An event is dated before the latest recorded event or a rule boundary."""
