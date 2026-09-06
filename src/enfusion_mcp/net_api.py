"""Strict asynchronous client for the Enfusion Workbench NET API.

The wire protocol is intentionally kept independent from MCP.  A request is a
signed little-endian protocol version followed by three UTF-8 Pascal strings::

    int32(1), client id, "JsonRPC", canonical JSON payload

Every response contains *exactly* two UTF-8 Pascal strings: status and payload.
Each request uses a new TCP connection.  This module never launches Workbench
and never retries a mutation.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeAlias, cast

PROTOCOL_VERSION: Final = 1
CONTENT_TYPE: Final = "JsonRPC"
OK_STATUS: Final = "Ok"
MAX_PASCAL_BYTES: Final = 16 * 1024 * 1024
INT32_BYTES: Final = 4
MAX_READ_ATTEMPTS: Final = 3
MAX_RETRY_DELAY_SECONDS: Final = 5.0
MAX_TCP_PORT: Final = 65_535
TEARDOWN_TIMEOUT_SECONDS: Final = 0.25

# The official Workbench NET API documentation explicitly describes this
# built-in endpoint as having no response body.  Custom handlers in the safe
# profile are deliberately not included here.
DOCUMENTED_BODYLESS_ENDPOINTS: Final = frozenset({"BringModuleWindowToFront"})
MUTATION_ENDPOINTS: Final = frozenset({"EnfusionMCP_VegetationApply"})

JsonScalar: TypeAlias = bool | int | float | str | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class CallKind(StrEnum):
    """Whether transport uncertainty can be retried safely."""

    READ_ONLY = "read_only"
    MUTATION = "mutation"


class NetApiPhase(StrEnum):
    """Observable phase in which a request failed."""

    BEFORE_CONNECT = "before_connect"
    CONNECT = "connect"
    PRE_SEND = "pre_send"
    SENDING = "sending"
    RESPONSE = "response"
    DECODING = "decoding"


class NetApiErrorCode(StrEnum):
    """Stable machine-readable NET API error codes."""

    INVALID_REQUEST = "INVALID_REQUEST"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    CONNECTION_LOST = "CONNECTION_LOST"
    TIMEOUT = "TIMEOUT"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    API_ERROR = "API_ERROR"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class NetApiError(Exception):
    """Base error with a stable code and failure phase."""

    def __init__(
        self,
        code: NetApiErrorCode,
        message: str,
        *,
        phase: NetApiPhase,
        send_started: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.send_started = send_started


class WorkbenchApiError(NetApiError):
    """A complete Workbench error response, including its payload."""

    def __init__(self, status: str, payload: str) -> None:
        super().__init__(
            NetApiErrorCode.API_ERROR,
            f"Workbench NET API returned status {status!r}",
            phase=NetApiPhase.DECODING,
            send_started=True,
        )
        self.status = status
        self.payload = payload


class UnknownOutcomeError(NetApiError):
    """A mutation may have reached Workbench and must only be reconciled."""

    def __init__(self, cause: NetApiError) -> None:
        super().__init__(
            NetApiErrorCode.UNKNOWN_OUTCOME,
            "Mutation outcome is unknown because the request may have reached Workbench; "
            "do not resend it, and reconcile with the same idempotency key",
            phase=cause.phase,
            send_started=True,
        )
        self.cause = cause


@dataclass(frozen=True, slots=True)
class NetApiTimeouts:
    """Independent timeout budgets for the asynchronous transport phases."""

    connect_seconds: float = 3.0
    send_seconds: float = 3.0
    response_seconds: float = 10.0

    def __post_init__(self) -> None:
        for name, value in (
            ("connect_seconds", self.connect_seconds),
            ("send_seconds", self.send_seconds),
            ("response_seconds", self.response_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")


@dataclass(frozen=True, slots=True)
class ReadRetryPolicy:
    """Explicit, bounded retry policy for read-only requests.

    ``max_attempts=1`` is the default and means no retries.  Only connection
    refusal/loss and timeouts are retryable; malformed or error responses are
    never retried.  A mutation rejects any policy with more than one attempt.
    """

    max_attempts: int = 1
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_READ_ATTEMPTS:
            raise ValueError("max_attempts must be between 1 and 3")
        if (
            not math.isfinite(self.delay_seconds)
            or not 0 <= self.delay_seconds <= MAX_RETRY_DELAY_SECONDS
        ):
            raise ValueError("delay_seconds must be finite and between 0 and 5")

    def permits_retry(self, error: NetApiError, attempt: int) -> bool:
        """Return whether another read-only attempt is permitted."""
        return attempt < self.max_attempts and error.code in {
            NetApiErrorCode.CONNECTION_REFUSED,
            NetApiErrorCode.CONNECTION_LOST,
            NetApiErrorCode.TIMEOUT,
        }


def encode_int32_le(value: int) -> bytes:
    """Encode a signed 32-bit integer in little-endian order."""
    try:
        return struct.pack("<i", value)
    except struct.error as error:
        raise ValueError("value is outside signed int32 range") from error


def decode_int32_le(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode one signed little-endian int32 and return value/new offset."""
    if offset < 0 or len(data) - offset < INT32_BYTES:
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            f"truncated int32 prefix at byte offset {offset}",
            phase=NetApiPhase.DECODING,
        )
    return struct.unpack_from("<i", data, offset)[0], offset + 4


def encode_pascal_string(value: str, *, max_bytes: int = MAX_PASCAL_BYTES) -> bytes:
    """Encode a UTF-8 string with its signed int32 byte length."""
    if not isinstance(value, str):
        raise TypeError("Pascal value must be a string")
    encoded = value.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"UTF-8 value exceeds maximum of {max_bytes} bytes")
    return encode_int32_le(len(encoded)) + encoded


def decode_pascal_string(
    data: bytes,
    offset: int = 0,
    *,
    max_bytes: int = MAX_PASCAL_BYTES,
) -> tuple[str, int]:
    """Strictly decode a Pascal UTF-8 string and return value/new offset."""
    length, payload_offset = decode_int32_le(data, offset)
    _validate_pascal_length(length, max_bytes=max_bytes)
    end = payload_offset + length
    if end > len(data):
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            f"truncated Pascal data: expected {length} bytes, got {len(data) - payload_offset}",
            phase=NetApiPhase.DECODING,
        )
    return _decode_utf8(data[payload_offset:end]), end


def canonical_json(value: Mapping[str, JsonValue]) -> str:
    """Serialize an object deterministically without ASCII escaping or NaN."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise NetApiError(
            NetApiErrorCode.INVALID_REQUEST,
            f"request parameters are not canonical JSON: {error}",
            phase=NetApiPhase.BEFORE_CONNECT,
        ) from error


def encode_request(
    client_id: str,
    api_func: str,
    params: Mapping[str, JsonValue] | None = None,
) -> bytes:
    """Build a complete trusted request frame.

    ``APIFunc`` is reserved and is only inserted from the separately validated
    ``api_func`` argument.  It can never be overridden through client params.
    """
    _validate_identifier(client_id, field="client_id")
    _validate_identifier(api_func, field="api_func")
    supplied = dict(params or {})
    if "APIFunc" in supplied:
        raise NetApiError(
            NetApiErrorCode.INVALID_REQUEST,
            "APIFunc is reserved and must not be supplied in params",
            phase=NetApiPhase.BEFORE_CONNECT,
        )
    payload: dict[str, JsonValue] = {**supplied, "APIFunc": api_func}
    payload_text = canonical_json(payload)
    try:
        return b"".join(
            (
                encode_int32_le(PROTOCOL_VERSION),
                encode_pascal_string(client_id),
                encode_pascal_string(CONTENT_TYPE),
                encode_pascal_string(payload_text),
            )
        )
    except (TypeError, ValueError) as error:
        raise NetApiError(
            NetApiErrorCode.INVALID_REQUEST,
            f"request cannot be encoded: {error}",
            phase=NetApiPhase.BEFORE_CONNECT,
        ) from error


def decode_response(
    frame: bytes,
    *,
    allow_empty_payload: bool = False,
) -> JsonValue:
    """Decode exactly two Pascal strings and reject all trailing bytes."""
    status, offset = decode_pascal_string(frame)
    payload, offset = decode_pascal_string(frame, offset)
    if offset != len(frame):
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            f"response has {len(frame) - offset} trailing byte(s)",
            phase=NetApiPhase.DECODING,
            send_started=True,
        )
    return _interpret_response(status, payload, allow_empty_payload=allow_empty_payload)


class NetApiClient:
    """One-connection-per-request asynchronous Workbench NET API client."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        client_id: str = "EnfusionMCP",
        timeouts: NetApiTimeouts | None = None,
    ) -> None:
        if not isinstance(host, str) or not host or "\x00" in host:
            raise ValueError("host must be a non-empty string without NUL")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= MAX_TCP_PORT:
            raise ValueError("port must be between 1 and 65535")
        _validate_identifier(client_id, field="client_id")
        self._host = host
        self._port = port
        self._client_id = client_id
        self._timeouts = timeouts or NetApiTimeouts()

    @property
    def workbench_address(self) -> tuple[str, int]:
        """Return the immutable TCP target used by this client."""

        return (self._host, self._port)

    async def call(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
        *,
        retry_policy: ReadRetryPolicy | None = None,
    ) -> JsonValue:
        """Call a read-only endpoint, with explicit bounded retry policy."""
        if api_func in MUTATION_ENDPOINTS:
            raise NetApiError(
                NetApiErrorCode.INVALID_REQUEST,
                f"{api_func} is mutation-capable and must use call_mutation() or call_reconcile()",
                phase=NetApiPhase.BEFORE_CONNECT,
            )
        return await self._call(
            api_func,
            params,
            kind=CallKind.READ_ONLY,
            retry_policy=retry_policy,
        )

    async def call_mutation(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> JsonValue:
        """Call an allowlisted mutation endpoint exactly once."""
        if api_func not in MUTATION_ENDPOINTS:
            raise NetApiError(
                NetApiErrorCode.INVALID_REQUEST,
                f"{api_func!r} is not an allowlisted mutation endpoint",
                phase=NetApiPhase.BEFORE_CONNECT,
            )
        return await self._call(
            api_func,
            params,
            kind=CallKind.MUTATION,
            retry_policy=ReadRetryPolicy(),
        )

    async def call_reconcile(
        self,
        api_func: str,
        params: Mapping[str, JsonValue],
        *,
        retry_policy: ReadRetryPolicy | None = None,
    ) -> JsonValue:
        """Use a mutation handler's trusted read-only reconciliation mode."""
        if api_func not in MUTATION_ENDPOINTS or params.get("mode") != "reconcile":
            raise NetApiError(
                NetApiErrorCode.INVALID_REQUEST,
                "reconciliation requires an allowlisted endpoint and mode='reconcile'",
                phase=NetApiPhase.BEFORE_CONNECT,
            )
        return await self._call(
            api_func,
            params,
            kind=CallKind.READ_ONLY,
            retry_policy=retry_policy,
        )

    async def _call(
        self,
        api_func: str,
        params: Mapping[str, JsonValue] | None,
        *,
        kind: CallKind,
        retry_policy: ReadRetryPolicy | None,
    ) -> JsonValue:
        """Internal common implementation after endpoint-kind enforcement."""
        policy = retry_policy or ReadRetryPolicy()

        # Validate and encode everything before opening a socket.  Invalid tool
        # input therefore cannot reach Workbench.
        request = encode_request(self._client_id, api_func, params)

        attempt = 1
        while True:
            try:
                return await self._single_attempt(
                    request,
                    api_func=api_func,
                    kind=kind,
                )
            except NetApiError as error:
                if kind is CallKind.MUTATION or not policy.permits_retry(error, attempt):
                    raise
                attempt += 1
                if policy.delay_seconds:
                    await asyncio.sleep(policy.delay_seconds)

    async def _single_attempt(
        self,
        request: bytes,
        *,
        api_func: str,
        kind: CallKind,
    ) -> JsonValue:
        phase = NetApiPhase.CONNECT
        send_started = False
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await self._connect()

            phase = NetApiPhase.PRE_SEND
            if writer.is_closing():
                raise NetApiError(
                    NetApiErrorCode.CONNECTION_LOST,
                    "Workbench NET API connection closed before request transmission",
                    phase=phase,
                )

            phase = NetApiPhase.SENDING
            # Set the uncertainty boundary before write(): the transport may
            # accept bytes even when write/drain subsequently raises.
            send_started = True
            await self._send(writer, request)

            phase = NetApiPhase.RESPONSE
            status_bytes, payload_bytes = await self._receive(reader)

            phase = NetApiPhase.DECODING
            status = _decode_utf8(status_bytes, send_started=True)
            payload = _decode_utf8(payload_bytes, send_started=True)
            return _interpret_response(
                status,
                payload,
                allow_empty_payload=api_func in DOCUMENTED_BODYLESS_ENDPOINTS,
            )
        except NetApiError as error:
            if kind is CallKind.MUTATION and send_started:
                if isinstance(error, UnknownOutcomeError):
                    raise
                raise UnknownOutcomeError(error) from error
            raise
        except asyncio.CancelledError as error:
            if kind is CallKind.MUTATION and send_started:
                cause = NetApiError(
                    NetApiErrorCode.CONNECTION_LOST,
                    "mutation task was cancelled after request transmission began",
                    phase=phase,
                    send_started=True,
                )
                raise UnknownOutcomeError(cause) from error
            raise
        except (ConnectionError, OSError, RuntimeError) as error:
            transport_error = NetApiError(
                NetApiErrorCode.CONNECTION_LOST,
                f"Workbench NET API transport failed: {error}",
                phase=phase,
                send_started=send_started,
            )
            if kind is CallKind.MUTATION and send_started:
                raise UnknownOutcomeError(transport_error) from error
            raise transport_error from error
        except Exception as error:
            unexpected_error = NetApiError(
                NetApiErrorCode.PROTOCOL_ERROR,
                f"unexpected Workbench NET API failure: {type(error).__name__}: {error}",
                phase=phase,
                send_started=send_started,
            )
            if kind is CallKind.MUTATION and send_started:
                raise UnknownOutcomeError(unexpected_error) from error
            raise unexpected_error from error
        finally:
            if writer is not None:
                await _close_writer_safely(writer)

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._timeouts.connect_seconds,
            )
        except TimeoutError as error:
            raise self._timeout_error(NetApiPhase.CONNECT, send_started=False) from error
        except ConnectionRefusedError as error:
            raise NetApiError(
                NetApiErrorCode.CONNECTION_REFUSED,
                "Workbench NET API refused the loopback connection. Start Arma Reforger "
                "Tools manually through Steam/Proton and enable its NET API.",
                phase=NetApiPhase.CONNECT,
            ) from error
        except OSError as error:
            raise NetApiError(
                NetApiErrorCode.CONNECTION_LOST,
                f"could not connect to Workbench NET API: {error}",
                phase=NetApiPhase.CONNECT,
            ) from error

    async def _send(self, writer: asyncio.StreamWriter, request: bytes) -> None:
        try:
            writer.write(request)
            await asyncio.wait_for(writer.drain(), timeout=self._timeouts.send_seconds)
            if writer.can_write_eof():
                writer.write_eof()
                await asyncio.wait_for(writer.drain(), timeout=self._timeouts.send_seconds)
        except TimeoutError as error:
            raise self._timeout_error(NetApiPhase.SENDING, send_started=True) from error
        except (ConnectionError, OSError, RuntimeError) as error:
            raise NetApiError(
                NetApiErrorCode.CONNECTION_LOST,
                f"connection failed while sending request: {error}",
                phase=NetApiPhase.SENDING,
                send_started=True,
            ) from error

    async def _receive(self, reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
        try:
            return await asyncio.wait_for(
                _read_exact_response(reader),
                timeout=self._timeouts.response_seconds,
            )
        except TimeoutError as error:
            raise self._timeout_error(NetApiPhase.RESPONSE, send_started=True) from error
        except _TruncatedFrameError as error:
            raise NetApiError(
                NetApiErrorCode.PROTOCOL_ERROR,
                str(error),
                phase=NetApiPhase.RESPONSE,
                send_started=True,
            ) from error
        except (ConnectionError, OSError) as error:
            raise NetApiError(
                NetApiErrorCode.CONNECTION_LOST,
                f"connection failed while receiving response: {error}",
                phase=NetApiPhase.RESPONSE,
                send_started=True,
            ) from error

    @staticmethod
    def _timeout_error(phase: NetApiPhase, *, send_started: bool) -> NetApiError:
        return NetApiError(
            NetApiErrorCode.TIMEOUT,
            f"Workbench NET API timed out during {phase.value}",
            phase=phase,
            send_started=send_started,
        )


async def _close_writer_safely(writer: asyncio.StreamWriter) -> None:
    """Bounded best-effort teardown that never replaces a classified outcome."""
    with suppress(BaseException):
        writer.close()
    with suppress(BaseException):
        await asyncio.wait_for(
            writer.wait_closed(),
            timeout=TEARDOWN_TIMEOUT_SECONDS,
        )


class _TruncatedFrameError(Exception):
    """Internal marker for an EOF inside a required frame component."""


async def _read_exact_response(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    status = await _read_pascal_bytes(reader, field="status")
    payload = await _read_pascal_bytes(reader, field="payload")
    trailing = await reader.read(1)
    if trailing:
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            "response contains trailing bytes after its two Pascal strings",
            phase=NetApiPhase.DECODING,
            send_started=True,
        )
    return status, payload


async def _read_pascal_bytes(reader: asyncio.StreamReader, *, field: str) -> bytes:
    try:
        prefix = await reader.readexactly(4)
    except asyncio.IncompleteReadError as error:
        raise _TruncatedFrameError(
            f"truncated {field} length prefix: expected 4 bytes, got {len(error.partial)}"
        ) from error
    length = struct.unpack("<i", prefix)[0]
    _validate_pascal_length(length, max_bytes=MAX_PASCAL_BYTES, send_started=True)
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as error:
        raise _TruncatedFrameError(
            f"truncated {field} data: expected {length} bytes, got {len(error.partial)}"
        ) from error


def _validate_pascal_length(
    length: int,
    *,
    max_bytes: int,
    send_started: bool = False,
) -> None:
    if length < 0:
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            f"negative Pascal string length {length}",
            phase=NetApiPhase.DECODING,
            send_started=send_started,
        )
    if length > max_bytes:
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            f"Pascal string length {length} exceeds maximum of {max_bytes} bytes",
            phase=NetApiPhase.DECODING,
            send_started=send_started,
        )


def _decode_utf8(data: bytes, *, send_started: bool = False) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            f"invalid UTF-8 in Pascal string at byte {error.start}",
            phase=NetApiPhase.DECODING,
            send_started=send_started,
        ) from error


def _interpret_response(
    status: str,
    payload: str,
    *,
    allow_empty_payload: bool,
) -> JsonValue:
    # Both strings have already been consumed before status is inspected.
    if status != OK_STATUS:
        raise WorkbenchApiError(status, payload)
    if payload == "":
        if allow_empty_payload:
            return None
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            "Workbench returned an empty payload for an endpoint that requires JSON",
            phase=NetApiPhase.DECODING,
            send_started=True,
        )
    try:
        decoded = json.loads(payload, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            f"Workbench returned invalid JSON payload: {error}",
            phase=NetApiPhase.DECODING,
            send_started=True,
        ) from error
    if not _is_json_value(decoded):
        raise NetApiError(
            NetApiErrorCode.PROTOCOL_ERROR,
            "Workbench payload is not a supported JSON value",
            phase=NetApiPhase.DECODING,
            send_started=True,
        )
    return cast(JsonValue, decoded)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is forbidden")


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _validate_identifier(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise NetApiError(
            NetApiErrorCode.INVALID_REQUEST,
            f"{field} must be a non-empty string without NUL",
            phase=NetApiPhase.BEFORE_CONNECT,
        )
