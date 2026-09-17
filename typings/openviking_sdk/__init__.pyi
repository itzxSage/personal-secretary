# Minimal type stubs for ``openviking_sdk`` (OpenViking 0.4.9).
# Covers only the surface used by ``OpenVikingLifeMemoryProvider``. The real
# package ships no ``py.typed`` marker, so basedpyright would otherwise treat
# every symbol as ``Any``.

from typing import Any

class OpenVikingError(Exception):
    ...


class SyncHTTPClient:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        user: str | None = None,
        account: str | None = None,
        timeout: float | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None: ...

    def initialize(self) -> None: ...
    def close(self) -> None: ...
    def health(self) -> bool: ...
    def mkdir(self, uri: str, description: str | None = None) -> None: ...
    def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: float | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> dict[str, Any]: ...
    def find(self, query: str = "", **kwargs: Any) -> Any: ...  # noqa: ANN401
    def ls(self, uri: str, **kwargs: Any) -> Any: ...  # noqa: ANN401
    def read(self, uri: str, offset: int = 0, limit: int = -1) -> str: ...
    def rm(self, uri: str, recursive: bool = False, **kwargs: Any) -> None: ...  # noqa: ANN401
