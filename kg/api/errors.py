"""Single structured envelope for HTTP errors.

Before: HTTPException(detail="route not found: …") returned

    {"detail": "route not found: …"}

After: every error response goes through error_envelope() and looks like

    {"error": {"code": "NOT_FOUND", "message": "route not found: …",
                "kind": "route", "project": "adsw"}}

That gives the MCP wrapper (and any future client) a stable shape to
pattern-match on instead of grepping free-form strings.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("kg.api")


def _envelope(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


def _code_from_status(status: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        500: "INTERNAL",
        503: "UNAVAILABLE",
    }.get(status, "ERROR")


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
        # If a route already returns a dict in `detail`, pass it through;
        # otherwise wrap the string message.
        if isinstance(exc.detail, dict):
            payload = exc.detail
        else:
            payload = _envelope(_code_from_status(exc.status_code), str(exc.detail))
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope(
                "INTERNAL",
                f"{type(exc).__name__}: {exc}",
                path=request.url.path,
            ),
        )
