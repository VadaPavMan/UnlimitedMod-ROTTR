"""ROTTR Mod Menu v2.7.0

A lightweight, toggleable trainer for Rise of the Tomb Raider.
The unstable health patch remains removed. Includes conservative AOB-based
skill-point and Endurance hooks plus an experimental resource-gain multiplier.
"""

from __future__ import annotations

import ctypes
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk
from typing import Optional

import pymem
import pymem.process


APP_VERSION = "2.7.0"
PROCESS_NAME = "ROTTR.exe"
POLL_INTERVAL = 1.5

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_EXECUTE_READWRITE = 0x40

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None


if kernel32 is not None:
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualAllocEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.VirtualFreeEx.restype = wintypes.BOOL
    kernel32.VirtualFreeEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel32.VirtualProtectEx.restype = wintypes.BOOL
    kernel32.VirtualProtectEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.FlushInstructionCache.restype = wintypes.BOOL
    kernel32.FlushInstructionCache.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]


def as_handle(handle) -> wintypes.HANDLE:
    """Normalize pymem's process handle for Win32 API calls."""
    if isinstance(handle, ctypes.c_void_p):
        return handle
    return wintypes.HANDLE(int(handle))


def win_error(message: str) -> OSError:
    """Return a useful Windows API error, with a portable fallback."""
    if hasattr(ctypes, "WinError"):
        return ctypes.WinError(ctypes.get_last_error(), message)
    return OSError(message)


def rel_jmp(from_addr: int, to_addr: int) -> bytes:
    """Build an x64 near relative JMP and validate its signed 32-bit range."""
    displacement = to_addr - (from_addr + 5)
    if not -(2**31) <= displacement < 2**31:
        raise ValueError(
            f"Relative jump out of range: source=0x{from_addr:X}, "
            f"destination=0x{to_addr:X}"
        )
    return b"\xE9" + struct.pack("<i", displacement)


def alloc_near(
    handle,
    target: int,
    size: int,
    search_range: int = 0x70000000,
    granularity: int = 0x10000,
) -> Optional[int]:
    """Allocate executable memory within rel32 reach of the patch site."""
    if kernel32 is None:
        return None

    process_handle = as_handle(handle)
    origin = target - (target % granularity)

    # Search outward from the module, trying positive and negative addresses.
    # Stop at the first valid allocation rather than scanning the whole range.
    for delta in range(0, search_range, granularity):
        candidates = (origin + delta, origin - delta) if delta else (origin,)
        for candidate in candidates:
            if candidate <= 0:
                continue
            address = kernel32.VirtualAllocEx(
                process_handle,
                ctypes.c_void_p(candidate),
                size,
                MEM_COMMIT | MEM_RESERVE,
                PAGE_EXECUTE_READWRITE,
            )
            if address:
                allocated = int(address)
                try:
                    rel_jmp(target, allocated)
                    rel_jmp(allocated, target + 5)
                except ValueError:
                    kernel32.VirtualFreeEx(
                        process_handle, ctypes.c_void_p(allocated), 0, MEM_RELEASE
                    )
                    continue
                return allocated
    return None


def free_near(handle, addr: Optional[int]) -> None:
    if kernel32 is None or not addr:
        return
    try:
        kernel32.VirtualFreeEx(as_handle(handle), ctypes.c_void_p(addr), 0, MEM_RELEASE)
    except Exception:
        pass


def write_code(process: pymem.Pymem, address: int, payload: bytes) -> None:
    """Write a patch while managing page protection and the instruction cache."""
    if kernel32 is None:
        raise OSError("Windows memory APIs are unavailable on this platform.")

    handle = as_handle(process.process_handle)
    old_protect = wintypes.DWORD(0)
    changed = kernel32.VirtualProtectEx(
        handle,
        ctypes.c_void_p(address),
        len(payload),
        PAGE_EXECUTE_READWRITE,
        ctypes.byref(old_protect),
    )
    if not changed:
        raise win_error(f"VirtualProtectEx failed at 0x{address:X}")

    operation_error: Optional[BaseException] = None
    try:
        process.write_bytes(address, payload, len(payload))
        flushed = kernel32.FlushInstructionCache(
            handle, ctypes.c_void_p(address), len(payload)
        )
        if not flushed:
            raise win_error(f"FlushInstructionCache failed at 0x{address:X}")
    except BaseException as exc:
        operation_error = exc
    finally:
        restored = kernel32.VirtualProtectEx(
            handle,
            ctypes.c_void_p(address),
            len(payload),
            old_protect.value,
            ctypes.byref(wintypes.DWORD()),
        )
        if not restored and operation_error is None:
            operation_error = win_error(
                f"Could not restore memory protection at 0x{address:X}"
            )

    if operation_error is not None:
        raise operation_error


def write_allocated_code(process: pymem.Pymem, address: int, payload: bytes) -> None:
    """Write into a fresh RWX allocation and flush the instruction cache."""
    process.write_bytes(address, payload, len(payload))
    if kernel32 is not None:
        flushed = kernel32.FlushInstructionCache(
            as_handle(process.process_handle), ctypes.c_void_p(address), len(payload)
        )
        if not flushed:
            raise win_error(f"FlushInstructionCache failed at code cave 0x{address:X}")


class Mod:
    """One toggleable code patch identified by an AOB signature."""

    def __init__(
        self,
        key: str,
        label: str,
        pattern: bytes,
        patch_bytes: Optional[bytes] = None,
        cave_code: Optional[bytes] = None,
        patch_len: Optional[int] = None,
        note: str = "",
        require_unique: bool = False,
        match_offset: int = 0,
    ) -> None:
        self.key = key
        self.label = label
        self.note = note
        self.pattern = pattern
        self.patch_bytes = patch_bytes
        self.cave_code = cave_code
        self.patch_len = patch_len or (len(patch_bytes) if patch_bytes else None)
        self.require_unique = require_unique
        self.match_offset = match_offset
        self.reset()

    def reset(self) -> None:
        self.addr: Optional[int] = None
        self.original: Optional[bytes] = None
        self.cave_addr: Optional[int] = None
        self.applied = False
        self.match_count = 0
        self.last_error = ""

    @staticmethod
    def _find_all(data: bytes, pattern: bytes) -> list[int]:
        matches: list[int] = []
        start = 0
        while True:
            index = data.find(pattern, start)
            if index < 0:
                break
            matches.append(index)
            start = index + 1
        return matches

    def locate(self, process: pymem.Pymem, module) -> Optional[int]:
        if self.addr is not None:
            return self.addr
        if not self.pattern:
            self.last_error = "The AOB signature is empty."
            return None

        try:
            image = process.read_bytes(module.lpBaseOfDll, module.SizeOfImage)
        except Exception as exc:
            self.last_error = f"Could not read the game module: {exc}"
            return None

        matches = self._find_all(image, self.pattern)
        self.match_count = len(matches)
        if not matches:
            self.last_error = "AOB signature not found in ROTTR.exe."
            return None
        if self.require_unique and len(matches) != 1:
            self.last_error = (
                f"AOB signature is ambiguous: found {len(matches)} matches; "
                "no patch was applied."
            )
            return None

        self.addr = int(module.lpBaseOfDll) + matches[0] + self.match_offset
        if self.addr < int(module.lpBaseOfDll) or self.addr + (self.patch_len or 0) > int(module.lpBaseOfDll) + int(module.SizeOfImage):
            self.addr = None
            self.last_error = "Calculated patch address falls outside ROTTR.exe; refusing to patch."
            return None
        if len(matches) > 1:
            self.last_error = (
                f"Warning: signature occurs {len(matches)} times; using first match."
            )
        else:
            self.last_error = ""
        return self.addr

    def apply(self, process: pymem.Pymem) -> bool:
        if self.applied:
            return True
        if self.addr is None:
            self.last_error = self.last_error or "The instruction has not been located."
            return False
        if self.patch_len is None or self.patch_len <= 0:
            self.last_error = "Patch length is not configured."
            return False
        if self.cave_code is None:
            if self.patch_bytes is None or len(self.patch_bytes) != self.patch_len:
                self.last_error = "Patch bytes do not match the configured patch length."
                return False
        elif self.patch_len < 5:
            self.last_error = "A code-cave hook needs at least five patch bytes."
            return False

        try:
            self.original = process.read_bytes(self.addr, self.patch_len)
        except Exception as exc:
            self.last_error = f"Could not read original bytes: {exc}"
            return False

        try:
            if self.cave_code is not None:
                cave_size = len(self.cave_code) + 5
                self.cave_addr = alloc_near(
                    process.process_handle, self.addr, cave_size
                )
                if not self.cave_addr:
                    raise OSError("Could not allocate executable memory near the patch.")

                jump_back = rel_jmp(
                    self.cave_addr + len(self.cave_code), self.addr + self.patch_len
                )
                cave = self.cave_code + jump_back
                write_allocated_code(process, self.cave_addr, cave)

                hook = rel_jmp(self.addr, self.cave_addr)
                hook += b"\x90" * (self.patch_len - len(hook))
                write_code(process, self.addr, hook)
            else:
                assert self.patch_bytes is not None
                write_code(process, self.addr, self.patch_bytes)

            self.applied = True
            if not self.last_error.startswith("Warning:"):
                self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"Patch failed: {exc}"
            # Best-effort rollback. Do not free a cave if target restoration failed,
            # because a partially written jump could still point into that cave.
            target_restored = False
            try:
                write_code(process, self.addr, self.original)
                target_restored = True
            except Exception:
                target_restored = False
            if target_restored:
                free_near(process.process_handle, self.cave_addr)
                self.cave_addr = None
                self.applied = False
            else:
                # Preserve state so shutdown/removal can retry restoring original bytes.
                self.applied = True
            return False

    def remove(self, process: pymem.Pymem) -> bool:
        if not self.applied:
            return True
        if self.addr is None or self.original is None:
            self.last_error = "Cannot restore patch: original bytes/address are missing."
            return False
        try:
            write_code(process, self.addr, self.original)
        except Exception as exc:
            self.last_error = f"Could not restore original bytes: {exc}"
            return False

        free_near(process.process_handle, self.cave_addr)
        self.cave_addr = None
        self.applied = False
        self.original = None
        self.last_error = ""
        return True


class MultiSiteMod:
    """Apply the same known patch at every expected occurrence of an AOB.

    Used for the reload-ammo instruction, which has three known weapon code
    paths in the target build. It refuses to patch if the occurrence count
    differs, rather than guessing which code paths are safe.
    """

    def __init__(self, prototype: Mod, expected_matches: int) -> None:
        self.prototype = prototype
        self.key = prototype.key
        self.label = prototype.label
        self.note = prototype.note
        self.pattern = prototype.pattern
        self.patch_len = prototype.patch_len
        self.expected_matches = expected_matches
        self.match_count = 0
        self.last_error = ""
        self.patches: list[Mod] = []
        self.applied = False

    def reset(self) -> None:
        self.patches = []
        self.match_count = 0
        self.last_error = ""
        self.applied = False

    def locate(self, process: pymem.Pymem, module) -> Optional[int]:
        if self.patches:
            return self.patches[0].addr
        try:
            image = process.read_bytes(module.lpBaseOfDll, module.SizeOfImage)
        except Exception as exc:
            self.last_error = f"Could not read the game module: {exc}"
            return None

        matches = Mod._find_all(image, self.pattern)
        self.match_count = len(matches)
        if len(matches) != self.expected_matches:
            self.last_error = (
                f"Expected {self.expected_matches} reload-ammo signatures, found "
                f"{len(matches)}; no patch was applied."
            )
            return None

        base = int(module.lpBaseOfDll)
        module_end = base + int(module.SizeOfImage)
        patches: list[Mod] = []
        for offset in matches:
            address = base + offset + self.prototype.match_offset
            if address < base or address + (self.patch_len or 0) > module_end:
                self.last_error = "A reload-ammo patch address falls outside ROTTR.exe."
                return None
            patch = Mod(
                key=self.key,
                label=self.label,
                pattern=self.pattern,
                patch_bytes=self.prototype.patch_bytes,
                cave_code=self.prototype.cave_code,
                patch_len=self.prototype.patch_len,
                note=self.note,
                require_unique=False,
                match_offset=self.prototype.match_offset,
            )
            patch.addr = address
            patch.match_count = len(matches)
            patches.append(patch)
        self.patches = patches
        self.last_error = ""
        return patches[0].addr

    def apply(self, process: pymem.Pymem) -> bool:
        if self.applied:
            return True
        if not self.patches:
            self.last_error = self.last_error or "Reload-ammo signatures have not been located."
            return False
        applied: list[Mod] = []
        for patch in self.patches:
            if not patch.apply(process):
                # Roll back earlier sites before reporting failure.
                rollback_ok = True
                for previous in reversed(applied):
                    if not previous.remove(process):
                        rollback_ok = False
                self.applied = not rollback_ok
                self.last_error = (
                    f"Patch failed at one of {self.expected_matches} weapon paths: "
                    f"{patch.last_error}"
                )
                return False
            applied.append(patch)
        self.applied = True
        self.last_error = ""
        return True

    def remove(self, process: pymem.Pymem) -> bool:
        failures: list[str] = []
        for patch in reversed(self.patches):
            if patch.applied and not patch.remove(process):
                failures.append(patch.last_error)
        self.applied = any(patch.applied for patch in self.patches)
        if failures:
            self.last_error = "; ".join(failures)
            return False
        self.last_error = ""
        return True


# Existing stable features. The experimental health patch was removed after
# repeatable crashes in Rise of the Tomb Raider: 20 Year Celebration (Epic).
AMMO_WITH_RELOAD_BASE = Mod(
    key="ammo_with_reload",
    label="Unlimited Ammo (with reload)",
    pattern=b"\x8B\x87\x88\x05\x00\x00",
    cave_code=b"\x8B\x87\x90\x05\x00\x00\x89\x87\x88\x05\x00\x00",
    patch_len=6,
    note="Copies the reserve-ammo field into the magazine field at this code path.",
)
# The source AOB appeared three times in the tested game build. Patch all three
# known weapon paths so the mode is not limited to one weapon/code path.
AMMO_WITH_RELOAD = MultiSiteMod(AMMO_WITH_RELOAD_BASE, expected_matches=3)

# Corrected from ROTTR.CT's No Reload AOB script:
#   aobscanmodule(norelad,ROTTR.exe,29 18 49 8B 86 70 03 00 00)
#   norelad: nop; nop
#   [DISABLE] db 29 18
# The old Python implementation incorrectly patched the same 6-byte read
# instruction as the 'with reload' option.
AMMO_NO_RELOAD = Mod(
    key="ammo_no_reload",
    label="Unlimited Ammo (no reload needed)",
    pattern=b"\x29\x18\x49\x8B\x86\x70\x03\x00\x00",
    patch_bytes=b"\x90\x90",
    patch_len=2,
    note="Disables the ammo decrement instruction identified in ROTTR.CT.",
    require_unique=True,
)

SURVIVAL = Mod(
    key="survival",
    label="Unlimited Survival Instinct",
    pattern=b"\xF3\x0F\x11\x46\x10\x0F",
    patch_bytes=b"\x90\x90\x90\x90\x90",
    patch_len=5,
)

RESOURCES = Mod(
    key="resources",
    label="Unlimited Resources",
    note="Gather at least one of a resource first.",
    pattern=b"\x66\x89\x18\x48\x8B\x8D\xA8\x03\x00\x00",
    # Preserve the flags from the original MOV instruction. The comparison
    # inside this hook changes RFLAGS, while the original instruction does not.
    # Leaking changed flags could affect unrelated game logic after the hook.
    cave_code=(
        b"\x9C"                                      # pushfq
        b"\x51"                                      # push rcx
        b"\x48\x8B\x08"                          # mov rcx,[rax]
        b"\x66\x3B\xD9"                          # cmp bx,cx
        b"\x7E\x05"                                # jle skip_write
        b"\x66\x89\x18"                          # mov [rax],bx
        b"\xEB\x00"                                # short jump (next instruction)
        b"\x59"                                      # pop rcx
        b"\x9D"                                      # popfq
        b"\x48\x8B\x8D\xA8\x03\x00\x00"  # original next instruction
    ),
    patch_len=10,
)

# Experimental resource-gain multiplier derived from the legacy table's
# "New Double Resource" code path. It repeats the original 16-bit addition,
# then replays the pointer increment. This changes the amount added when this
# path runs; it does NOT increase the game's maximum carrying capacity.
DOUBLE_RESOURCE_GAIN = Mod(
    key="double_resource_gain",
    label="Double Resource Gains (experimental)",
    pattern=b"\x66\x01\x43\x0C\x48\x83\xC7\x08",
    cave_code=(
        b"\x66\x01\x43\x0C"  # add word ptr [rbx+0C],ax (original)
        b"\x66\x01\x43\x0C"  # repeat addition to double this gain
        b"\x48\x83\xC7\x08"  # add rdi,08 (original)
    ),
    patch_len=8,
    require_unique=True,
    note=(
        "Experimental legacy-table port. Doubles gains on this specific code path; "
        "does not increase maximum ammo/resource carrying capacity."
    ),
)


# Legacy Cheat Engine "Skills" feature. The AOB begins at two complete
# instructions (7 bytes). The cave sets the count to 99, then replays both
# original instructions. A unique signature is required; unsupported builds
# safely refuse the toggle instead of guessing an address.
SKILL_POINTS = Mod(
    key="skill_points",
    label="Maintain 99 Skill Points",
    pattern=b"\x8B\x58\x44\x8B\x44\x24\x38",
    cave_code=(
        b"\xC7\x40\x44\x63\x00\x00\x00"  # mov dword ptr [rax+44],99
        b"\x8B\x58\x44"                          # mov ebx,[rax+44]
        b"\x8B\x44\x24\x38"                    # mov eax,[rsp+38]
    ),
    patch_len=7,
    require_unique=True,
    note="Legacy table port; applies only when its full 7-byte AOB is unique.",
)

# Endurance-mode needs from the newer supplied table. These replace one
# 5-byte instruction at a time and require an exact unique match. These
# signatures are short, so they intentionally fail closed on ambiguous builds.
INFINITE_WARMTH = Mod(
    key="infinite_warmth",
    label="Infinite Warmth (Endurance)",
    pattern=b"\xF3\x0F\x11\x70\x28",  # movss [rax+28],xmm6
    cave_code=b"\xC7\x40\x28\x00\x00\x7A\x44",  # [rax+28] = 1000.0f
    patch_len=5,
    require_unique=True,
    note="Endurance-mode hook; needs one exact match in this executable.",
)

INFINITE_HUNGER = Mod(
    key="infinite_hunger",
    label="Infinite Hunger (Endurance)",
    pattern=b"\xF3\x0F\x10\x48\x28",  # movss xmm1,[rax+28]
    cave_code=(
        b"\xC7\x40\x28\x00\x00\x7A\x44"  # [rax+28] = 1000.0f
        b"\xF3\x0F\x10\x48\x28"              # replay movss xmm1,[rax+28]
    ),
    patch_len=5,
    require_unique=True,
    note="Endurance-mode hook; needs one exact match in this executable.",
)


class Engine:
    """Single-owner worker for process access, patching, and cleanup."""

    def __init__(self, mods: list[Mod | MultiSiteMod], status_cb) -> None:
        self.mods = {mod.key: mod for mod in mods}
        self.status_cb = status_cb
        self.pm: Optional[pymem.Pymem] = None
        self.module = None
        self.requests: queue.Queue[tuple[str, Optional[str]]] = queue.Queue()
        self.running = True
        self.attached = False
        self.desired = {key: False for key in self.mods}
        self._status_text = ""
        self._last_attach_attempt = 0.0
        self.thread = threading.Thread(target=self._loop, name="ROTTR-Trainer", daemon=True)
        self.thread.start()

    def _emit(self, message: str, key: Optional[str] = None) -> None:
        event = {"message": message}
        if key is not None and key in self.mods:
            event["key"] = key
            event["applied"] = self.mods[key].applied
        self.status_cb(event)

    def _set_status(self, message: str) -> None:
        if message != self._status_text:
            self._status_text = message
            self._emit(message)

    def request(self, action: str, key: str) -> None:
        if key in self.mods and action in ("apply", "remove"):
            self.requests.put((action, key))

    def stop(self, timeout: float = 4.0) -> None:
        self.requests.put(("shutdown", None))
        self.thread.join(timeout=timeout)

    def _close_process(self) -> None:
        pm = self.pm
        self.pm = None
        self.module = None
        if pm is not None:
            try:
                pm.close_process()
            except Exception:
                pass

    def _reset_mods(self) -> None:
        for key, mod in self.mods.items():
            mod.reset()
            self._emit("Patch state cleared because the game process detached.", key)

    def _try_attach(self) -> bool:
        self._last_attach_attempt = time.monotonic()
        candidate = None
        try:
            candidate = pymem.Pymem(PROCESS_NAME)
            module = pymem.process.module_from_name(
                candidate.process_handle, PROCESS_NAME
            )
            if module is None:
                raise RuntimeError(f"Module {PROCESS_NAME} was not found in the process.")
            self.pm = candidate
            self.module = module
            self.attached = True
            self._set_status(f"Attached to {PROCESS_NAME}. Select the cheats to enable.")
            # Reapply the latest requested state after a game restart/re-attach.
            for key, enabled in self.desired.items():
                if enabled:
                    self._apply_mod(key)
            return True
        except Exception:
            if candidate is not None and candidate is not self.pm:
                try:
                    candidate.close_process()
                except Exception:
                    pass
            self.attached = False
            self._set_status(f"Waiting for {PROCESS_NAME}...")
            return False

    def _detach(self) -> None:
        # Try to restore patches before forgetting their addresses. If the process
        # really has exited, these writes will fail harmlessly and are caught below.
        self._restore_all()
        self.attached = False
        self._reset_mods()
        self._close_process()
        self._set_status(f"Game closed or inaccessible; waiting for {PROCESS_NAME}...")

    def _apply_mod(self, key: str) -> None:
        mod = self.mods[key]
        if not self.attached or self.pm is None or self.module is None:
            self._emit("Cheat queued; waiting for the game process.", key)
            return

        address = mod.locate(self.pm, self.module)
        if address is None:
            self._emit(f"{mod.label}: NOT ENABLED — {mod.last_error}", key)
            return

        ok = mod.apply(self.pm)
        if ok:
            details = f" (AOB matches: {mod.match_count})" if mod.match_count > 1 else ""
            warning = f" — {mod.last_error}" if mod.last_error.startswith("Warning:") else ""
            self._emit(f"{mod.label}: ON{details}{warning}", key)
        else:
            self._emit(f"{mod.label}: NOT ENABLED — {mod.last_error}", key)

    def _remove_mod(self, key: str) -> None:
        mod = self.mods[key]
        if not self.attached or self.pm is None:
            # The process has disappeared; its address space no longer exists.
            if mod.applied:
                mod.reset()
            self._emit(f"{mod.label}: OFF (game process is not attached)", key)
            return
        if not mod.applied:
            self._emit(f"{mod.label}: OFF", key)
            return
        if mod.remove(self.pm):
            self._emit(f"{mod.label}: OFF", key)
        else:
            self._emit(f"{mod.label}: RESTORE FAILED — {mod.last_error}", key)

    def _restore_all(self) -> None:
        if self.pm is None:
            return
        for key, mod in self.mods.items():
            if mod.applied:
                if mod.remove(self.pm):
                    self._emit(f"{mod.label}: restored on shutdown", key)
                else:
                    self._emit(
                        f"{mod.label}: WARNING — could not restore on shutdown: "
                        f"{mod.last_error}",
                        key,
                    )

    def _handle_request(self, action: str, key: Optional[str]) -> bool:
        if action == "shutdown":
            self._restore_all()
            self.running = False
            return False
        if key is None or key not in self.mods:
            return True
        self.desired[key] = action == "apply"
        if action == "apply":
            self._apply_mod(key)
        elif action == "remove":
            self._remove_mod(key)
        return True

    def _loop(self) -> None:
        try:
            while self.running:
                try:
                    action, key = self.requests.get(timeout=0.2)
                    if not self._handle_request(action, key):
                        break
                    # Drain already queued clicks promptly and in order.
                    while self.running:
                        try:
                            action, key = self.requests.get_nowait()
                        except queue.Empty:
                            break
                        if not self._handle_request(action, key):
                            break
                except queue.Empty:
                    pass

                if not self.running:
                    break

                if not self.attached:
                    if time.monotonic() - self._last_attach_attempt >= POLL_INTERVAL:
                        self._try_attach()
                    continue

                try:
                    assert self.pm is not None and self.module is not None
                    self.pm.read_bytes(self.module.lpBaseOfDll, 1)
                except Exception:
                    self._detach()
        finally:
            # Covers unexpected worker exit as well as the regular shutdown path.
            self._restore_all()
            self._close_process()
            self.attached = False


class TrainerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"ROTTR Mod Menu v{APP_VERSION}")
        root.geometry("440x550")
        root.resizable(False, False)

        icon_path = self.get_resource_path("mod.ico")
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass

        bg = "#252525"
        fg = "#e8e8e8"
        accent = "#76c7b7"
        note_fg = "#b0b0b0"

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=bg, foreground=fg, fieldbackground=bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TRadiobutton", background=bg, foreground=fg)
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TSeparator", background="#3a3a3a")
        style.map("TRadiobutton", background=[("selected", bg)])
        style.map("TCheckbutton", background=[("selected", bg)])
        root.configure(bg=bg)

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Ammo Mode", font=("Segoe UI", 10, "bold")).pack(
            anchor="w"
        )
        self.ammo_var = tk.StringVar(value="off")
        self._last_ammo_choice = "off"
        for text, value in (
            ("Ammo (with reload)", "with_reload"),
            ("Ammo (without reload)", "no_reload"),
        ):
            ttk.Radiobutton(
                frame,
                text=text,
                variable=self.ammo_var,
                value=value,
                command=self.on_ammo_change,
            ).pack(anchor="w")

        ttk.Separator(frame).pack(fill="x", pady=7)

        self.survival_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Unlimited Survival Instinct",
            variable=self.survival_var,
            command=self.on_survival_change,
        ).pack(anchor="w")

        self.resources_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Unlimited Resources + Ammo",
            variable=self.resources_var,
            command=self.on_resources_change,
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="(resource behavior depends on the active game code path)",
            foreground=note_fg,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=20)

        self.double_resource_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Double Resource Gains (experimental)",
            variable=self.double_resource_var,
            command=self.on_double_resource_change,
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="(doubles gains on one legacy code path; not a capacity increase)",
            foreground=note_fg,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=20)

        self.skill_points_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Maintain 99 Skill Points",
            variable=self.skill_points_var,
            command=self.on_skill_points_change,
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="(legacy signature; only applies when exactly one match is found)",
            foreground=note_fg,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=20)

        ttk.Separator(frame).pack(fill="x", pady=5)
        ttk.Label(frame, text="Endurance Mode", font=("Segoe UI", 9, "bold")).pack(
            anchor="w"
        )
        self.warmth_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Infinite Warmth",
            variable=self.warmth_var,
            command=self.on_warmth_change,
        ).pack(anchor="w")
        self.hunger_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Infinite Hunger",
            variable=self.hunger_var,
            command=self.on_hunger_change,
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="(legacy code signatures; each hook requires exactly one match)",
            foreground=note_fg,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=20)

        ttk.Separator(frame).pack(fill="x", pady=5)
        self.status_var = tk.StringVar(value=f"Waiting for {PROCESS_NAME}...")
        self.latest_status = self.status_var.get()
        self.actual_states = {
            "ammo_with_reload": False,
            "ammo_no_reload": False,
            "survival": False,
            "resources": False,
            "double_resource_gain": False,
            "skill_points": False,
            "infinite_warmth": False,
            "infinite_hunger": False,
        }
        ttk.Label(
            frame,
            textvariable=self.status_var,
            foreground=accent,
            wraplength=380,
            justify="left",
        ).pack(anchor="w")

        self.status_queue: queue.Queue[dict] = queue.Queue()
        self.engine = Engine(
            [
                AMMO_WITH_RELOAD,
                AMMO_NO_RELOAD,
                SURVIVAL,
                RESOURCES,
                DOUBLE_RESOURCE_GAIN,
                SKILL_POINTS,
                INFINITE_WARMTH,
                INFINITE_HUNGER,
            ],
            self.status_queue.put,
        )
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(150, self.poll_status)

    @staticmethod
    def get_resource_path(relative_path: str) -> str:
        try:
            base_path = sys._MEIPASS  # type: ignore[attr-defined]
        except AttributeError:
            base_path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_path, relative_path)

    def _compose_status(self) -> str:
        active_labels = []
        if self.actual_states.get("ammo_with_reload"):
            active_labels.append("Ammo (with reload)")
        if self.actual_states.get("ammo_no_reload"):
            active_labels.append("Ammo (no reload)")
        if self.actual_states.get("survival"):
            active_labels.append("Survival Instinct")
        if self.actual_states.get("resources"):
            active_labels.append("Resources + Ammo")
        if self.actual_states.get("double_resource_gain"):
            active_labels.append("Double Resource Gains")
        if self.actual_states.get("skill_points"):
            active_labels.append("99 Skill Points")
        if self.actual_states.get("infinite_warmth"):
            active_labels.append("Infinite Warmth")
        if self.actual_states.get("infinite_hunger"):
            active_labels.append("Infinite Hunger")
        lines = [self.latest_status]
        lines.append("Applied cheats: " + (", ".join(active_labels) if active_labels else "none"))
        return "\n".join(lines)

    def poll_status(self) -> None:
        try:
            while True:
                event = self.status_queue.get_nowait()
                self.latest_status = event.get("message", self.latest_status)
                key = event.get("key")
                if key in self.actual_states:
                    self.actual_states[key] = bool(event.get("applied", False))
        except queue.Empty:
            pass
        self.status_var.set(self._compose_status())
        if self.root.winfo_exists():
            self.root.after(150, self.poll_status)

    def on_ammo_change(self) -> None:
        choice = self.ammo_var.get()
        # Clicking the already-selected radio option toggles it off, so an
        # explicit "Off" radio option is not needed.
        if choice == self._last_ammo_choice:
            choice = "off"
            self.ammo_var.set("off")
        self._last_ammo_choice = choice
        self.engine.request("remove", "ammo_with_reload")
        self.engine.request("remove", "ammo_no_reload")
        if choice == "with_reload":
            self.engine.request("apply", "ammo_with_reload")
        elif choice == "no_reload":
            self.engine.request("apply", "ammo_no_reload")

    def on_survival_change(self) -> None:
        self.engine.request(
            "apply" if self.survival_var.get() else "remove", "survival"
        )

    def on_double_resource_change(self) -> None:
        self.engine.request(
            "apply" if self.double_resource_var.get() else "remove",
            "double_resource_gain",
        )

    def on_skill_points_change(self) -> None:
        self.engine.request(
            "apply" if self.skill_points_var.get() else "remove", "skill_points"
        )

    def on_warmth_change(self) -> None:
        self.engine.request(
            "apply" if self.warmth_var.get() else "remove", "infinite_warmth"
        )

    def on_hunger_change(self) -> None:
        self.engine.request(
            "apply" if self.hunger_var.get() else "remove", "infinite_hunger"
        )

    def on_resources_change(self) -> None:
        # Resource and ammo patches target different instruction sites, but the
        # user's runtime test showed ammo behavior changes when the resource
        # hook is toggled. Keep the features logically independent and refresh
        # the selected ammo patch after the resource operation, so its final
        # installed state does not depend on patch activation order.
        self.engine.request(
            "apply" if self.resources_var.get() else "remove", "resources"
        )

        # The ammo UI is mutually exclusive: refresh only the selected mode.
        # Queue operations are processed serially by Engine, so this runs after
        # the resource toggle. Do not enable ammo if the user selected Off.
        choice = self.ammo_var.get()
        if choice in ("with_reload", "no_reload"):
            self.engine.request("remove", "ammo_with_reload")
            self.engine.request("remove", "ammo_no_reload")
            selected_key = (
                "ammo_with_reload" if choice == "with_reload" else "ammo_no_reload"
            )
            self.engine.request("apply", selected_key)

    def on_close(self) -> None:
        # The worker restores active patches before releasing the process handle.
        self.engine.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("ROTTR Mod Menu can only run on Windows.")
    if struct.calcsize("P") != 8:
        raise SystemExit("Use 64-bit Python to run ROTTR Mod Menu.")
    root = tk.Tk()
    TrainerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
