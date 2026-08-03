"""统一异常与退出码：0 成功 / 1 业务失败（含空结果）/ 2 用法或凭证缺失。

所有服务商错误统一为 ServiceError，带机器可读 category：
回退链（chain.run_chain）据此决定是否尝试下一个 provider——
可重试（timeout/network/http/empty）换服务商重试；不可重试
（invalid/auth/no_results）记录后跳过。行业惯例的 failover 语义。
"""

from __future__ import annotations

# 错误分类（机器可读，chain 决策依据）
CATEGORY_TIMEOUT = "timeout"
CATEGORY_NETWORK = "network"
CATEGORY_HTTP = "http"
CATEGORY_EMPTY = "empty"
CATEGORY_INVALID = "invalid"
CATEGORY_AUTH = "auth"
CATEGORY_NO_RESULTS = "no_results"
CATEGORY_ALL_FAILED = "all_failed"

# 不可重试分类：换 provider 也没有意义
NON_RETRIABLE = frozenset({CATEGORY_INVALID, CATEGORY_AUTH, CATEGORY_NO_RESULTS})


class EztoolError(Exception):
    """所有 eztool 错误的基类。"""

    exit_code = 1

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


class ServiceError(EztoolError):
    """服务商调用失败（fetch / search / convert 通用）。"""

    exit_code = 1

    def __init__(
        self,
        message: str,
        category: str = CATEGORY_HTTP,
        http_code: int | None = None,
        code: str | None = None,
    ):
        super().__init__(message, code)
        self.category = category
        self.http_code = http_code

    @property
    def retriable(self) -> bool:
        """True = 换下一个 provider 重试有意义。"""
        return self.category not in NON_RETRIABLE

    def __str__(self) -> str:  # compact one-line form for chain stderr logs
        if self.http_code is not None:
            return f"{self.message} (HTTP {self.http_code})"
        return self.message


class UsageError(EztoolError):
    """参数用法错误（参数不属于当前后端等）。"""

    exit_code = 2


class CredentialsError(ServiceError):
    """凭证缺失或无效。"""

    exit_code = 2

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message, CATEGORY_AUTH, code=code)


class NoResultsError(ServiceError):
    """搜索无结果。"""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message, CATEGORY_NO_RESULTS, code=code)
