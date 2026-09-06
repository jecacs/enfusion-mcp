from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import cast

import pytest

from enfusion_mcp.net_api import (
    CONTENT_TYPE,
    MAX_PASCAL_BYTES,
    PROTOCOL_VERSION,
    JsonValue,
    NetApiClient,
    NetApiError,
    NetApiErrorCode,
    NetApiPhase,
    NetApiTimeouts,
    ReadRetryPolicy,
    UnknownOutcomeError,
    WorkbenchApiError,
    decode_int32_le,
    decode_pascal_string,
    decode_response,
    encode_int32_le,
    encode_pascal_string,
    encode_request,
)

ConnectionHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


@asynccontextmanager
async def loopback_server(handler: ConnectionHandler) -> AsyncIterator[int]:
    """Run a fake server only on 127.0.0.1 and an OS-assigned port."""
    tasks: set[asyncio.Task[None]] = set()

    async def dispatch(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await handler(reader, writer)
        finally:
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()

    def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(dispatch(reader, writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    socket = server.sockets[0]
    address = cast(tuple[str, int], socket.getsockname())
    try:
        yield address[1]
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def read_request(reader: asyncio.StreamReader) -> bytes:
    return await reader.read()


def parse_request(frame: bytes) -> tuple[str, str, dict[str, JsonValue]]:
    version, offset = decode_int32_le(frame)
    assert version == PROTOCOL_VERSION
    client_id, offset = decode_pascal_string(frame, offset)
    content_type, offset = decode_pascal_string(frame, offset)
    payload, offset = decode_pascal_string(frame, offset)
    assert offset == len(frame)
    parsed = cast(dict[str, JsonValue], json.loads(payload))
    return client_id, content_type, parsed


def response_frame(status: str = "Ok", payload: str = "{}") -> bytes:
    return encode_pascal_string(status) + encode_pascal_string(payload)


def short_timeouts() -> NetApiTimeouts:
    return NetApiTimeouts(connect_seconds=0.2, send_seconds=0.2, response_seconds=0.2)


def test_int32_little_endian_golden_values() -> None:
    assert encode_int32_le(1) == b"\x01\x00\x00\x00"
    assert encode_int32_le(256) == b"\x00\x01\x00\x00"
    assert encode_int32_le(-1) == b"\xff\xff\xff\xff"
    assert decode_int32_le(b"x\x01\x00\x00\x00", 1) == (1, 5)


def test_int32_rejects_out_of_range_and_truncated_data() -> None:
    with pytest.raises(ValueError, match="int32"):
        encode_int32_le(2**31)
    with pytest.raises(NetApiError, match="truncated int32") as raised:
        decode_int32_le(b"\x01\x00")
    assert raised.value.code is NetApiErrorCode.PROTOCOL_ERROR


def test_pascal_length_is_utf8_bytes_for_cyrillic_and_emoji() -> None:
    encoded = encode_pascal_string("Привет🌿")
    assert encoded[:4] == b"\x10\x00\x00\x00"
    assert decode_pascal_string(encoded) == ("Привет🌿", len(encoded))


def test_request_has_literal_golden_wire_bytes_and_canonical_json() -> None:
    actual = encode_request("C", "Ping", {"b": "🌿", "a": "Привет"})
    expected = bytes.fromhex(
        "01000000"  # protocol version
        "0100000043"  # client ID: C
        "070000004a736f6e525043"  # JsonRPC
        "30000000"  # 48 UTF-8 payload bytes
        "7b2241504946756e63223a2250696e67222c2261223a22"
        "d09fd180d0b8d0b2d0b5d182222c2262223a22f09f8cbf227d"
    )
    assert actual == expected
    assert parse_request(actual) == (
        "C",
        CONTENT_TYPE,
        {"APIFunc": "Ping", "a": "Привет", "b": "🌿"},
    )


def test_response_has_literal_golden_wire_bytes() -> None:
    frame = bytes.fromhex(
        "020000004f6b"  # Ok
        "14000000"  # 20 UTF-8 payload bytes
        "7b2278223a22d0bad183d181d182f09f8cbf227d"
    )
    assert decode_response(frame) == {"x": "куст🌿"}


def test_request_rejects_reserved_api_func_and_non_finite_json() -> None:
    with pytest.raises(NetApiError, match="reserved") as reserved:
        encode_request("client", "Trusted", {"APIFunc": "Attacker"})
    assert reserved.value.code is NetApiErrorCode.INVALID_REQUEST
    assert reserved.value.phase is NetApiPhase.BEFORE_CONNECT

    with pytest.raises(NetApiError, match="canonical JSON"):
        encode_request("client", "Trusted", {"x": float("nan")})


@pytest.mark.parametrize(("client_id", "api_func"), [("", "Ping"), ("ok", ""), ("a\x00b", "Ping")])
def test_request_rejects_invalid_identifiers(client_id: str, api_func: str) -> None:
    with pytest.raises(NetApiError) as raised:
        encode_request(client_id, api_func)
    assert raised.value.code is NetApiErrorCode.INVALID_REQUEST
    assert raised.value.phase is NetApiPhase.BEFORE_CONNECT


def test_response_requires_two_pascal_strings_even_for_error_status() -> None:
    frame = encode_pascal_string("Undefined API func")
    with pytest.raises(NetApiError, match="truncated int32") as raised:
        decode_response(frame)
    assert not isinstance(raised.value, WorkbenchApiError)


def test_error_status_preserves_payload() -> None:
    payload = '{"detail":"handler failed","created":2}'
    with pytest.raises(WorkbenchApiError) as raised:
        decode_response(response_frame("HandlerError", payload))
    assert raised.value.status == "HandlerError"
    assert raised.value.payload == payload
    assert raised.value.code is NetApiErrorCode.API_ERROR


def test_empty_payload_is_explicitly_policy_controlled() -> None:
    frame = response_frame(payload="")
    with pytest.raises(NetApiError, match="empty payload"):
        decode_response(frame)
    assert decode_response(frame, allow_empty_payload=True) is None


def test_response_rejects_negative_and_oversized_lengths_before_data() -> None:
    with pytest.raises(NetApiError, match="negative"):
        decode_response(struct.pack("<i", -1))
    with pytest.raises(NetApiError, match="exceeds maximum"):
        decode_response(struct.pack("<i", MAX_PASCAL_BYTES + 1))


@pytest.mark.parametrize(
    "frame",
    [
        b"\x02\x00",
        struct.pack("<i", 3) + b"ab",
        encode_pascal_string("Ok") + b"\x02\x00",
        encode_pascal_string("Ok") + struct.pack("<i", 5) + b"{}",
    ],
)
def test_response_rejects_truncated_length_or_data(frame: bytes) -> None:
    with pytest.raises(NetApiError, match="truncated") as raised:
        decode_response(frame)
    assert raised.value.code is NetApiErrorCode.PROTOCOL_ERROR


def test_response_rejects_invalid_utf8_and_trailing_bytes() -> None:
    invalid_utf8 = struct.pack("<i", 1) + b"\xff" + encode_pascal_string("{}")
    with pytest.raises(NetApiError, match="invalid UTF-8"):
        decode_response(invalid_utf8)
    with pytest.raises(NetApiError, match="trailing"):
        decode_response(response_frame() + b"x")


def test_response_rejects_invalid_or_non_finite_json() -> None:
    with pytest.raises(NetApiError, match="invalid JSON"):
        decode_response(response_frame(payload="{"))
    with pytest.raises(NetApiError, match="non-finite"):
        decode_response(response_frame(payload='{"x":NaN}'))


async def test_fragmented_tcp_response_and_request_contract() -> None:
    observed: list[tuple[str, str, dict[str, JsonValue]]] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        observed.append(parse_request(await read_request(reader)))
        frame = response_frame(payload='{"message":"Привет 🌿"}')
        for byte in frame:
            writer.write(bytes((byte,)))
            await writer.drain()
            await asyncio.sleep(0)

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, client_id="Py Клиент", timeouts=short_timeouts())
        result = await client.call("EnfusionMCP_GetContext", {"mode": "read"})

    assert result == {"message": "Привет 🌿"}
    assert observed == [
        (
            "Py Клиент",
            "JsonRPC",
            {"APIFunc": "EnfusionMCP_GetContext", "mode": "read"},
        )
    ]


@pytest.mark.parametrize(
    "partial",
    [
        b"\x02\x00",  # disconnect inside the four-byte prefix
        struct.pack("<i", 4) + b"ab",  # disconnect inside Pascal data
        struct.pack("<i", 4) + "🌿".encode()[:2],  # inside multibyte UTF-8
    ],
)
async def test_tcp_disconnects_inside_prefix_data_or_utf8(partial: bytes) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_request(reader)
        writer.write(partial)
        await writer.drain()

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        with pytest.raises(NetApiError, match="truncated") as raised:
            await client.call("IsWorkbenchRunning")
    assert raised.value.code is NetApiErrorCode.PROTOCOL_ERROR
    assert raised.value.phase is NetApiPhase.RESPONSE


async def test_tcp_rejects_full_invalid_utf8_and_trailing_data() -> None:
    responses = iter(
        (
            struct.pack("<i", 1) + b"\xff" + encode_pascal_string("{}"),
            response_frame() + b"trailing",
        )
    )

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_request(reader)
        writer.write(next(responses))
        await writer.drain()

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        with pytest.raises(NetApiError, match="invalid UTF-8") as invalid:
            await client.call("IsWorkbenchRunning")
        assert invalid.value.phase is NetApiPhase.DECODING
        with pytest.raises(NetApiError, match="trailing") as trailing:
            await client.call("IsWorkbenchRunning")
        assert trailing.value.phase is NetApiPhase.DECODING


async def test_tcp_error_status_reads_and_preserves_second_string() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_request(reader)
        writer.write(response_frame("Undefined API func", '{"endpoint":"missing"}'))
        await writer.drain()

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        with pytest.raises(WorkbenchApiError) as raised:
            await client.call("Missing")
    assert raised.value.payload == '{"endpoint":"missing"}'


async def test_bodyless_policy_is_endpoint_specific() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_request(reader)
        writer.write(response_frame(payload=""))
        await writer.drain()

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        assert await client.call("BringModuleWindowToFront", {"ModuleName": "WorldEditor"}) is None
        with pytest.raises(NetApiError, match="empty payload"):
            await client.call("EnfusionMCP_GetContext")


async def test_each_request_uses_a_fresh_connection() -> None:
    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        await read_request(reader)
        writer.write(response_frame(payload=f'{{"connection":{connection_count}}}'))
        await writer.drain()

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        first = await client.call("IsWorkbenchRunning")
        second = await client.call("IsWorldEditorRunning")

    assert first == {"connection": 1}
    assert second == {"connection": 2}
    assert connection_count == 2


async def test_invalid_arguments_never_connect() -> None:
    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        await read_request(reader)
        writer.write(response_frame())

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        with pytest.raises(NetApiError, match="reserved"):
            await client.call("Trusted", {"APIFunc": "Override"})
    assert connection_count == 0


async def test_read_timeout_after_send_has_response_phase() -> None:
    request_seen = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_request(reader)
        request_seen.set()
        await asyncio.Event().wait()

    timeouts = NetApiTimeouts(connect_seconds=0.2, send_seconds=0.2, response_seconds=0.02)
    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=timeouts)
        with pytest.raises(NetApiError) as raised:
            await client.call("IsWorkbenchRunning")
    assert request_seen.is_set()
    assert raised.value.code is NetApiErrorCode.TIMEOUT
    assert raised.value.phase is NetApiPhase.RESPONSE
    assert raised.value.send_started is True


async def test_mutation_timeout_after_send_is_unknown_outcome_and_not_retried() -> None:
    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        await read_request(reader)
        await asyncio.Event().wait()

    timeouts = NetApiTimeouts(connect_seconds=0.2, send_seconds=0.2, response_seconds=0.02)
    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=timeouts)
        with pytest.raises(UnknownOutcomeError) as raised:
            await client.call_mutation("EnfusionMCP_VegetationApply", {})
    assert connection_count == 1
    assert raised.value.code is NetApiErrorCode.UNKNOWN_OUTCOME
    assert raised.value.cause.code is NetApiErrorCode.TIMEOUT
    assert raised.value.phase is NetApiPhase.RESPONSE


async def test_lost_mutation_response_is_unknown_and_never_sends_second_batch() -> None:
    applied_batches = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal applied_batches
        await read_request(reader)
        applied_batches += 1
        # Simulate mutation success followed by a lost response.

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        with pytest.raises(UnknownOutcomeError) as raised:
            await client.call_mutation("EnfusionMCP_VegetationApply", {})
    assert applied_batches == 1
    assert raised.value.cause.code is NetApiErrorCode.PROTOCOL_ERROR


async def test_mutation_endpoint_rejects_read_call_before_connect() -> None:
    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        await read_request(reader)

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())
        with pytest.raises(NetApiError, match="must use call_mutation") as raised:
            await client.call(
                "EnfusionMCP_VegetationApply",
                {},
                retry_policy=ReadRetryPolicy(max_attempts=2),
            )
    assert raised.value.phase is NetApiPhase.BEFORE_CONNECT
    assert connection_count == 0


async def test_explicit_bounded_read_retry_can_recover_from_timeout() -> None:
    connection_count = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connection_count
        connection_count += 1
        await read_request(reader)
        if connection_count == 1:
            await asyncio.Event().wait()
        writer.write(response_frame(payload='{"ok":true}'))
        await writer.drain()

    timeouts = NetApiTimeouts(connect_seconds=0.2, send_seconds=0.2, response_seconds=0.02)
    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=timeouts)
        result = await client.call(
            "IsWorkbenchRunning",
            retry_policy=ReadRetryPolicy(max_attempts=2),
        )
    assert result == {"ok": True}
    assert connection_count == 2


async def test_connect_timeout_occurs_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never_connect(
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        del host, port
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(asyncio, "open_connection", never_connect)
    client = NetApiClient(
        "127.0.0.1",
        1,
        timeouts=NetApiTimeouts(
            connect_seconds=0.01,
            send_seconds=0.2,
            response_seconds=0.2,
        ),
    )
    with pytest.raises(NetApiError) as raised:
        await client.call("IsWorkbenchRunning")
    assert raised.value.code is NetApiErrorCode.TIMEOUT
    assert raised.value.phase is NetApiPhase.CONNECT
    assert raised.value.send_started is False


class BlockingWriter:
    """Minimal StreamWriter-compatible double that blocks in drain()."""

    def __init__(self, *, closing: bool = False) -> None:
        self.write_called = False
        self.closed = False
        self.closing = closing

    def is_closing(self) -> bool:
        return self.closing

    def write(self, data: bytes) -> None:
        assert data
        self.write_called = True

    async def drain(self) -> None:
        await asyncio.Event().wait()

    def can_write_eof(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


async def test_mutation_send_timeout_is_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_writer = BlockingWriter()

    async def fake_connect(
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        del host, port
        return asyncio.StreamReader(), cast(asyncio.StreamWriter, fake_writer)

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    client = NetApiClient(
        "127.0.0.1",
        1,
        timeouts=NetApiTimeouts(
            connect_seconds=0.2,
            send_seconds=0.01,
            response_seconds=0.2,
        ),
    )
    with pytest.raises(UnknownOutcomeError) as raised:
        await client.call_mutation("EnfusionMCP_VegetationApply", {})
    assert fake_writer.write_called is True
    assert fake_writer.closed is True
    assert raised.value.cause.code is NetApiErrorCode.TIMEOUT
    assert raised.value.phase is NetApiPhase.SENDING


async def test_closed_connection_before_send_is_safe_pre_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_writer = BlockingWriter(closing=True)

    async def fake_connect(
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        del host, port
        return asyncio.StreamReader(), cast(asyncio.StreamWriter, fake_writer)

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    client = NetApiClient("127.0.0.1", 1, timeouts=short_timeouts())
    with pytest.raises(NetApiError) as raised:
        await client.call_mutation("EnfusionMCP_VegetationApply", {})
    assert not isinstance(raised.value, UnknownOutcomeError)
    assert raised.value.code is NetApiErrorCode.CONNECTION_LOST
    assert raised.value.phase is NetApiPhase.PRE_SEND
    assert raised.value.send_started is False
    assert fake_writer.write_called is False


async def test_unexpected_post_send_failure_is_unknown_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await read_request(reader)
        await asyncio.Event().wait()

    async with loopback_server(handler) as port:
        client = NetApiClient("127.0.0.1", port, timeouts=short_timeouts())

        async def fail_unexpectedly(_reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
            raise ValueError("fault injection after send")

        monkeypatch.setattr(client, "_receive", fail_unexpectedly)
        with pytest.raises(UnknownOutcomeError) as raised:
            await client.call_mutation("EnfusionMCP_VegetationApply", {})

    assert raised.value.cause.code is NetApiErrorCode.PROTOCOL_ERROR
    assert raised.value.cause.send_started is True


class HangingCloseWriter(BlockingWriter):
    async def drain(self) -> None:
        return None

    async def wait_closed(self) -> None:
        await asyncio.Event().wait()


async def test_hanging_writer_teardown_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_writer = HangingCloseWriter()

    async def fake_connect(
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        del host, port
        reader = asyncio.StreamReader()
        reader.feed_data(response_frame())
        reader.feed_eof()
        return reader, cast(asyncio.StreamWriter, fake_writer)

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    client = NetApiClient("127.0.0.1", 1, timeouts=short_timeouts())
    result = await asyncio.wait_for(client.call("IsWorkbenchRunning"), timeout=1)
    assert result == {}
    assert fake_writer.closed is True


async def test_reconcile_mode_is_read_only_but_strictly_shaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_count = 0

    async def fake_connect(
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        nonlocal connection_count
        del host, port
        connection_count += 1
        raise ConnectionRefusedError

    monkeypatch.setattr(asyncio, "open_connection", fake_connect)
    client = NetApiClient("127.0.0.1", 1, timeouts=short_timeouts())
    with pytest.raises(NetApiError):
        await client.call_reconcile(
            "EnfusionMCP_VegetationApply",
            {"mode": "reconcile"},
            retry_policy=ReadRetryPolicy(max_attempts=2),
        )
    assert connection_count == 2

    with pytest.raises(NetApiError, match="requires"):
        await client.call_reconcile("EnfusionMCP_VegetationApply", {"mode": "create"})


async def test_connection_refused_has_manual_start_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        del host, port
        raise ConnectionRefusedError

    monkeypatch.setattr(asyncio, "open_connection", refuse)
    client = NetApiClient("127.0.0.1", 1, timeouts=short_timeouts())
    with pytest.raises(NetApiError, match="Start Arma Reforger Tools manually") as raised:
        await client.call("IsWorkbenchRunning")
    assert raised.value.code is NetApiErrorCode.CONNECTION_REFUSED
    assert raised.value.phase is NetApiPhase.CONNECT
    assert raised.value.send_started is False


def test_timeout_and_retry_policy_validation() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        NetApiTimeouts(connect_seconds=0)
    with pytest.raises(ValueError, match="between 1 and 3"):
        ReadRetryPolicy(max_attempts=4)
    with pytest.raises(ValueError, match="between 0 and 5"):
        ReadRetryPolicy(delay_seconds=float("inf"))
