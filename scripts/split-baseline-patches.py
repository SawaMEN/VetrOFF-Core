#!/usr/bin/env python3
"""Split a Git binary diff into ordered, subsystem-scoped patches.

Never rewrite a diff section or reorder file changes. The original byte stream
must be exactly reconstructible from the new series. Verify the resulting
source trees with scripts/prepare.sh before committing any generated changes.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "0001-fork-baseline.patch"
COMPONENTS = ("hiddify-core", "hiddify-sing-box")
NESTED = {
    "v2", "protocol", "transport", "common", "experimental", "hiddify",
    "platform", "dns", "service", "option", "adapter",
}


def subsystem(section: bytes) -> str:
    header = section.split(b"\n", 1)[0]
    # Git quotes unusual path names. Only recognize ordinary directory prefixes;
    # unusual or root-level names are kept in a conservative root group.
    match = re.match(rb'^diff --git "?a/([A-Za-z0-9_.-]+)/(?:(['
                     rb'A-Za-z0-9_.-]+)/)?', header)
    if not match:
        return "root"
    first = match[1].decode("ascii")
    second = match[2].decode("ascii") if match[2] else ""
    name = f"{first}-{second}" if first in NESTED and second else first
    return name.strip(".-") or "root"


def split_diff(raw: bytes) -> list[tuple[str, bytes]]:
    offsets = [m.start() for m in re.finditer(rb"(?m)^diff --git ", raw)]
    if not offsets or offsets[0] != 0:
        raise ValueError("expected an unwrapped Git diff beginning with diff --git")
    offsets.append(len(raw))
    groups: list[tuple[str, bytearray]] = []
    for start, end in zip(offsets, offsets[1:]):
        section = raw[start:end]
        name = subsystem(section)
        if not groups or groups[-1][0] != name:
            groups.append((name, bytearray()))
        groups[-1][1].extend(section)
    parts = [
        (f"0001-{i:04d}-{name}.patch", bytes(data))
        for i, (name, data) in enumerate(groups, start=1)
    ]
    if b"".join(data for _, data in parts) != raw:
        raise ValueError("split changed the original byte stream")
    return parts


def split_component(directory: Path) -> None:
    baseline = directory / BASELINE
    series = directory / "series"
    raw = baseline.read_bytes()
    parts = split_diff(raw)
    if len(parts) < 2:
        raise ValueError(f"{directory.name}: baseline did not split")
    original_series = series.read_bytes()
    original_lines = original_series.splitlines(keepends=True)
    if sum(line.rstrip(b"\r\n") == BASELINE.encode() for line in original_lines) != 1:
        raise ValueError(f"{directory.name}: expected exactly one baseline entry")
    for name, _ in parts:
        if (directory / name).exists():
            raise FileExistsError(directory / name)
    for name, data in parts:
        (directory / name).write_bytes(data)
    replacement = b"".join(name.encode("ascii") + b"\n" for name, _ in parts)
    series.write_bytes(b"".join(
        replacement if line.rstrip(b"\r\n") == BASELINE.encode() else line
        for line in original_lines
    ))
    if b"".join((directory / name).read_bytes() for name, _ in parts) != raw:
        raise ValueError(f"{directory.name}: on-disk reconstruction mismatch")
    baseline.unlink()
    print(f"{directory.name}: {len(parts)} parts, {len(raw)} bytes preserved, "
          f"original SHA-256={hashlib.sha256(raw).hexdigest()}")


def main() -> None:
    for component in COMPONENTS:
        split_component(ROOT / "patches" / component)


if __name__ == "__main__":
    main()
