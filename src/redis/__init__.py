from __future__ import annotations

from dataclasses import dataclass
from socket import create_connection
from typing import Iterator, cast
from urllib.parse import urlparse

__all__ = ["Redis"]


def _encode(parts: list[str]) -> bytes:
    payload = [f"*{len(parts)}\r\n".encode()]
    for part in parts:
        raw = part.encode()
        payload.append(f"${len(raw)}\r\n".encode())
        payload.append(raw)
        payload.append(b"\r\n")
    return b"".join(payload)


@dataclass(slots=True)
class _Reader:
    sock: object

    def readline(self) -> bytes:
        buf = bytearray()
        while True:
            chunk = self.sock.recv(1)  # type: ignore[attr-defined]
            if not chunk:
                raise ConnectionError("redis connection closed")
            buf += chunk
            if buf.endswith(b"\r\n"):
                return bytes(buf[:-2])

    def read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))  # type: ignore[attr-defined]
            if not chunk:
                raise ConnectionError("redis connection closed")
            buf += chunk
        return bytes(buf)

    def read_response(self) -> object | None:
        prefix = self.sock.recv(1)  # type: ignore[attr-defined]
        if not prefix:
            raise ConnectionError("redis connection closed")
        if prefix == b"+":
            return self.readline().decode()
        if prefix == b"-":
            raise RuntimeError(self.readline().decode())
        if prefix == b":":
            return int(self.readline())
        if prefix == b"$":
            length = int(self.readline())
            if length == -1:
                return None
            data = self.read_exact(length)
            self.read_exact(2)
            return data.decode()
        if prefix == b"*":
            length = int(self.readline())
            if length == -1:
                return None
            items: list[object | None] = []
            for _ in range(length):
                items.append(self.read_response())
            return items
        raise RuntimeError(f"unexpected redis response prefix: {prefix!r}")


class Redis:
    def __init__(self, host: str, port: int, db: int = 0, decode_responses: bool = False):
        self.host = host
        self.port = port
        self.db = db
        self.decode_responses = decode_responses
        self._sock = create_connection((host, port), timeout=3)
        self._reader = _Reader(self._sock)
        if db:
            self.execute_command("SELECT", str(db))

    @classmethod
    def from_url(cls, url: str, decode_responses: bool = False) -> "Redis":
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        db = int(parsed.path.lstrip("/") or 0)
        return cls(host, port, db=db, decode_responses=decode_responses)

    def execute_command(self, *parts: str) -> object | None:
        self._sock.sendall(_encode([str(part) for part in parts]))
        return self._reader.read_response()

    def ping(self) -> bool:
        return self.execute_command("PING") == "PONG"

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        args = ["HSET", key]
        for field, value in mapping.items():
            args.extend([field, value])
        result = self.execute_command(*args)
        if result is None:
            return 0
        return int(cast(int | str, result))

    def hget(self, key: str, field: str) -> str | None:
        result = self.execute_command("HGET", key, field)
        return None if result is None else str(result)

    def expire(self, key: str, seconds: int) -> int:
        result = self.execute_command("EXPIRE", key, str(seconds))
        if result is None:
            return 0
        return int(cast(int | str, result))

    def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        result = self.execute_command("DEL", *keys)
        if result is None:
            return 0
        return int(cast(int | str, result))

    def scan_iter(self, match: str, count: int = 100) -> Iterator[str]:
        cursor = "0"
        while True:
            result = self.execute_command("SCAN", cursor, "MATCH", match, "COUNT", str(count))
            if not isinstance(result, list) or len(result) != 2:
                return
            cursor = str(result[0])
            for item in result[1]:
                if item is not None:
                    yield str(item)
            if cursor == "0":
                return

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass
