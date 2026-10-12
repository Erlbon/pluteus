"""A tiny RAR (format 1.5 to 4.x) writer for tests: every file is STORED (no compression), which is all a test needs and
which libarchive reads like any other RAR. Real RAR files cannot be made without the proprietary `rar` program."""
import os
import struct
import zlib


def _block(head_type: int, flags: int, body: bytes) -> bytes:
    size = 7 + len(body)
    rest = struct.pack("<BHH", head_type, flags, size) + body
    return struct.pack("<H", zlib.crc32(rest) & 0xFFFF) + rest


def make_rar(path, entries):
    """entries: [(name, bytes)]; names use '/' and are stored as given. Returns the path."""
    out = bytearray(b"Rar!\x1a\x07\x00")
    out += _block(0x73, 0, b"\x00\x00\x00\x00\x00\x00")                      # main header
    for name, data in entries:
        raw = name.encode("utf-8")
        body = struct.pack("<IIBIIBBHI", len(data), len(data), 3, zlib.crc32(data) & 0xFFFFFFFF, 0x5A4F0000, 20, 0x30,
                           len(raw), 0x20) + raw                              # host OS 3 (unix), method 0x30 = store
        out += _block(0x74, 0x8000, body) + data                              # 0x8000: the header is followed by its data
    out += bytes.fromhex("c43d7b00400700")                                    # end of archive
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(out)
    return path
