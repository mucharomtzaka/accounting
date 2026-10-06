class AccountingError(Exception):
    pass


class PeriodLockedError(AccountingError):
    pass


class UnbalancedJournalError(AccountingError):
    pass


class PeriodNotFoundError(AccountingError):
    pass
