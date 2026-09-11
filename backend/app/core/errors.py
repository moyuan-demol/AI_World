"""Domain level errors.

Services raise these; app.main turns them into HTTP responses, so the API layer
stays thin and services never import FastAPI.
"""


class ServiceError(Exception):
    """Base class for expected, user facing business errors."""

    def __init__(self, message: str, status_code: int = 400, code: str = "service_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class NotFoundError(ServiceError):
    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(message, status_code=404, code="not_found")


class PermissionDeniedError(ServiceError):
    def __init__(self, message: str = "没有权限访问该资源") -> None:
        super().__init__(message, status_code=403, code="forbidden")


class ValidationError(ServiceError):
    def __init__(self, message: str = "请求参数不合法") -> None:
        super().__init__(message, status_code=400, code="validation_error")


class AIUnavailableError(ServiceError):
    def __init__(self, message: str = "AI 服务暂时不可用，请稍后重试") -> None:
        super().__init__(message, status_code=502, code="ai_unavailable")
