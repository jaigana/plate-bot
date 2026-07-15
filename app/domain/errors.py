class DomainError(Exception):
    """Known business-rule violation safe to show to the user."""


class ValidationError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class AuthorizationError(DomainError):
    pass


class InsufficientFundsError(DomainError):
    pass


class PaymentError(DomainError):
    pass
