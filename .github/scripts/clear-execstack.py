#!/usr/bin/env python3
from __future__ import annotations

import struct
import sys
from pathlib import Path


PT_GNU_STACK = 0x6474E551
PF_X = 0x1


def _iter_files(raw_paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            files.extend(sorted(child for child in path.rglob("*") if child.is_file()))
        elif path.is_file():
            files.append(path)
    return files


def _elf_layout(handle):
    handle.seek(0)
    ident = handle.read(16)
    if len(ident) < 16 or ident[:4] != b"\x7fELF":
        return None

    elf_class = ident[4]
    data_encoding = ident[5]
    if data_encoding == 1:
        endian = "<"
    elif data_encoding == 2:
        endian = ">"
    else:
        raise RuntimeError("Unsupported ELF data encoding")

    if elf_class == 1:
        handle.seek(28)
        program_header_offset = struct.unpack(endian + "I", handle.read(4))[0]
        handle.seek(42)
        program_header_size = struct.unpack(endian + "H", handle.read(2))[0]
        program_header_count = struct.unpack(endian + "H", handle.read(2))[0]
        flags_offset = 24
    elif elf_class == 2:
        handle.seek(32)
        program_header_offset = struct.unpack(endian + "Q", handle.read(8))[0]
        handle.seek(54)
        program_header_size = struct.unpack(endian + "H", handle.read(2))[0]
        program_header_count = struct.unpack(endian + "H", handle.read(2))[0]
        flags_offset = 4
    else:
        raise RuntimeError("Unsupported ELF class")

    return program_header_offset, program_header_size, program_header_count, endian, flags_offset


def _gnu_stack_flag_offsets(handle) -> list[tuple[int, int, str]]:
    layout = _elf_layout(handle)
    if layout is None:
        return []

    program_header_offset, program_header_size, program_header_count, endian, flags_offset = layout
    results: list[tuple[int, int, str]] = []
    for index in range(program_header_count):
        header_offset = program_header_offset + index * program_header_size
        handle.seek(header_offset)
        program_type = struct.unpack(endian + "I", handle.read(4))[0]
        if program_type != PT_GNU_STACK:
            continue
        handle.seek(header_offset + flags_offset)
        flags = struct.unpack(endian + "I", handle.read(4))[0]
        results.append((header_offset + flags_offset, flags, endian))
    return results


def clear_execstack(path: Path) -> bool:
    with path.open("r+b") as handle:
        headers = _gnu_stack_flag_offsets(handle)
        changed = False
        for flag_offset, flags, endian in headers:
            if flags & PF_X:
                handle.seek(flag_offset)
                handle.write(struct.pack(endian + "I", flags & ~PF_X))
                changed = True
        return changed


def has_executable_stack(path: Path) -> bool:
    with path.open("rb") as handle:
        for _, flags, _ in _gnu_stack_flag_offsets(handle):
            if flags & PF_X:
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: clear-execstack.py <path> [<path> ...]", file=sys.stderr)
        return 1

    scanned = 0
    changed_paths: list[Path] = []
    failing_paths: list[Path] = []
    for path in _iter_files(args):
        try:
            with path.open("rb") as handle:
                if _elf_layout(handle) is None:
                    continue
        except OSError as exc:
            print(f"Failed to read {path}: {exc}", file=sys.stderr)
            return 1

        scanned += 1
        try:
            changed = clear_execstack(path)
            if has_executable_stack(path):
                failing_paths.append(path)
            elif changed:
                changed_paths.append(path)
        except (OSError, RuntimeError, struct.error) as exc:
            print(f"Failed to patch {path}: {exc}", file=sys.stderr)
            return 1

    for path in changed_paths:
        print(f"Cleared executable stack: {path}")

    if failing_paths:
        for path in failing_paths:
            print(f"Executable stack still present: {path}", file=sys.stderr)
        return 1

    print(f"Checked {scanned} ELF file(s); updated {len(changed_paths)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
