"""Common HTTP error helpers for route handlers."""

from fastapi import HTTPException


def not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def gone(exc: Exception) -> HTTPException:
    return HTTPException(status_code=410, detail=str(exc))


def conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def service_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))
