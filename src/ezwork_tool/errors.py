"""统一异常与退出码：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。"""


class EztoolError(Exception):
    """所有 eztool 错误的基类。"""

    exit_code = 1

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


class UsageError(EztoolError):
    """参数用法错误（参数不属于当前后端等）。"""

    exit_code = 2


class CredentialsError(EztoolError):
    """凭证缺失或无效。"""

    exit_code = 2


class BackendError(EztoolError):
    """后端 API 调用失败。"""

    exit_code = 1


class NoResultsError(EztoolError):
    """搜索无结果。"""

    exit_code = 1
