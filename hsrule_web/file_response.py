import os
from contextlib import asynccontextmanager
from stat import S_ISREG

from anyio import CancelScope, open_file, to_thread
from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send

from hsrule_web.errors import APIError


class SourcePDFResponse(FileResponse):
    """Serve the opened original while keeping Starlette range handling."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            self._source = await open_file(self.path, "rb")
        except (FileNotFoundError, IsADirectoryError):
            raise APIError(404, "document_not_found", "해당 문서를 찾을 수 없습니다.") from None
        try:
            self.stat_result = await to_thread.run_sync(os.fstat, self._source.wrapped.fileno())
            if not S_ISREG(self.stat_result.st_mode):
                raise APIError(404, "document_not_found", "해당 문서를 찾을 수 없습니다.")
            self.set_stat_headers(self.stat_result)
            # A path-send server would reopen the original name after emitting headers.
            extensions = dict(scope.get("extensions", {}))
            extensions.pop("http.response.pathsend", None)
            await super().__call__({**scope, "extensions": extensions}, receive, send)
        finally:
            with CancelScope(shield=True):
                await self._source.aclose()

    @asynccontextmanager
    async def _open_file(self):
        # Starlette 1.10 FileResponse uses this hook for full and partial responses.
        yield self._source
