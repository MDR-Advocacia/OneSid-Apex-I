class RPAError(Exception):
    def __init__(self, message, *, current_url=None, expected=None):
        super().__init__(message)
        self.current_url = current_url
        self.expected = expected

    def __str__(self):
        parts = [super().__str__()]
        if self.current_url:
            parts.append(f"url={self.current_url}")
        if self.expected:
            parts.append(f"esperado={self.expected}")
        return " | ".join(parts)


class BrowserInitializationError(RPAError):
    pass


class LoginError(RPAError):
    pass


class OneLogUnavailableError(LoginError):
    """OneLog fora do ar ou em janela de backoff.

    Não deve provocar restart de browser nem novas tentativas imediatas:
    o runner interrompe o ciclo e deixa as tarefas para o próximo agendamento.
    """

    pass


class SessionExpiredError(RPAError):
    pass


class PortalTimeoutError(RPAError):
    pass


class PortalElementNotFoundError(RPAError):
    pass


class PortalNavigationError(RPAError):
    pass


class PortalAccessError(PortalNavigationError):
    pass


class TemporaryPortalError(RPAError):
    pass
