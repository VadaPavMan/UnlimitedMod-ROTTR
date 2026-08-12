"""
ROTTR Mod Menu - lightweight GUI trainer for Rise of the Tomb Raider
"""

import ctypes
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import pymem
import pymem.process

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
PAGE_EXECUTE_READWRITE = 0x40
MEM_RELEASE = 0x8000
PROCESS_NAME = "ROTTR.exe"
POLL_INTERVAL = 1.5  # seconds between attach/health checks while idle

kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None


# Low-level helpers


def rel_jmp(from_addr, to_addr):
    return b"\xe9" + struct.pack("<i", to_addr - (from_addr + 5))


def alloc_near(handle, target, size, search_range=0x70000000, granularity=0x10000):
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualAllocEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    for delta in range(0, search_range, granularity):
        for candidate in (target + delta, target - delta):
            page = candidate - (candidate % granularity)
            if page <= 0:
                continue
            addr = kernel32.VirtualAllocEx(
                handle,
                ctypes.c_void_p(page),
                size,
                MEM_COMMIT | MEM_RESERVE,
                PAGE_EXECUTE_READWRITE,
            )
            if addr:
                return addr
    return None


def free_near(handle, addr):
    try:
        kernel32.VirtualFreeEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_ulong,
        ]
        kernel32.VirtualFreeEx(handle, ctypes.c_void_p(addr), 0, MEM_RELEASE)
    except Exception:
        pass


# toggleable patch


class Mod:
    def __init__(
        self,
        key,
        label,
        pattern,
        patch_bytes=None,
        cave_code=None,
        patch_len=None,
        note="",
    ):
        self.key = key
        self.label = label
        self.note = note
        self.pattern = pattern
        self.patch_bytes = patch_bytes
        self.cave_code = cave_code
        self.patch_len = patch_len or (len(patch_bytes) if patch_bytes else None)
        self.reset()

    def reset(self):
        self.addr = None
        self.original = None
        self.cave_addr = None
        self.applied = False

    def locate(self, process, module):
        if self.addr is not None:
            return self.addr
        try:
            data = process.read_bytes(module.lpBaseOfDll, module.SizeOfImage)
        except Exception:
            return None
        idx = data.find(self.pattern)
        if idx == -1:
            return None
        self.addr = module.lpBaseOfDll + idx
        return self.addr

    def apply(self, process):
        if self.addr is None or self.applied:
            return False
        try:
            self.original = process.read_bytes(self.addr, self.patch_len)
            if self.cave_code is not None:
                size = len(self.cave_code) + 5
                self.cave_addr = alloc_near(process.process_handle, self.addr, size)
                if not self.cave_addr:
                    return False
                jmp_back = rel_jmp(
                    self.cave_addr + len(self.cave_code), self.addr + self.patch_len
                )
                cave = self.cave_code + jmp_back
                process.write_bytes(self.cave_addr, cave, len(cave))
                hook = rel_jmp(self.addr, self.cave_addr) + b"\x90" * (
                    self.patch_len - 5
                )
                process.write_bytes(self.addr, hook, len(hook))
            else:
                process.write_bytes(self.addr, self.patch_bytes, len(self.patch_bytes))
            self.applied = True
            return True
        except Exception:
            return False

    def remove(self, process):
        if not self.applied or self.original is None:
            return False
        try:
            process.write_bytes(self.addr, self.original, len(self.original))
            if self.cave_addr:
                free_near(process.process_handle, self.cave_addr)
                self.cave_addr = None
            self.applied = False
            return True
        except Exception:
            return False


# Mod (offsets)


AMMO_WITH_RELOAD = Mod(
    "ammo_with_reload",
    "Unlimited Ammo (with reload)",
    pattern=b"\x8b\x87\x88\x05\x00\x00",
    cave_code=b"\x8b\x87\x90\x05\x00\x00\x89\x87\x88\x05\x00\x00",
    patch_len=6,
)
AMMO_NO_RELOAD = Mod(
    "ammo_no_reload",
    "Unlimited Ammo (no reload needed)",
    pattern=b"\x8b\x87\x88\x05\x00\x00",
    patch_bytes=b"\xb8\xff\xff\x00\x00\x90",
)
SURVIVAL = Mod(
    "survival",
    "Unlimited Survival Instinct",
    pattern=b"\xf3\x0f\x11\x46\x10\x0f",
    patch_bytes=b"\x90\x90\x90\x90\x90",
)
RESOURCES = Mod(
    "resources",
    "Unlimited Resources",
    note="Gather at least one of a resource first.",
    pattern=b"\x66\x89\x18\x48\x8b\x8d\xa8\x03\x00\x00",
    cave_code=(
        b"\x51\x48\x8b\x08\x66\x3b\xd9\x7e\x05\x66\x89\x18\xeb\x00\x59"
        b"\x48\x8b\x8d\xa8\x03\x00\x00"
    ),
    patch_len=10,
)


# Background engine: attaches, watches health, applies/removes on request


class Engine:
    def __init__(self, mods, status_cb):
        self.mods = {m.key: m for m in mods}
        self.status_cb = status_cb
        self.pm = None
        self.module = None
        self.requests = queue.Queue()
        self.running = True
        self.attached = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def request(self, action, key):
        self.requests.put((action, key))

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            if not self.attached:
                self._try_attach()
                if not self.attached:
                    time.sleep(POLL_INTERVAL)
                    continue

            drained = False
            try:
                while True:
                    action, key = self.requests.get_nowait()
                    drained = True
                    self._handle(action, key)
            except queue.Empty:
                pass

            try:
                self.pm.read_bytes(self.module.lpBaseOfDll, 1)  # cheap liveness check
            except Exception:
                self._on_detach()
                continue

            time.sleep(0.4 if drained else POLL_INTERVAL)

    def _try_attach(self):
        try:
            self.pm = pymem.Pymem(PROCESS_NAME)
            self.module = pymem.process.module_from_name(
                self.pm.process_handle, PROCESS_NAME
            )
            if not self.module:
                raise RuntimeError("module not found")
            self.attached = True
            self.status_cb(f"Attached to {PROCESS_NAME}. Tick the mods you want.")
        except Exception:
            self.attached = False
            self.status_cb("Waiting for the game to start...")

    def _on_detach(self):
        self.attached = False
        for mod in self.mods.values():
            mod.reset()
        self.status_cb("Game closed - waiting to reattach...")

    def _handle(self, action, key):
        mod = self.mods.get(key)
        if not mod:
            return
        if action == "apply":
            mod.locate(self.pm, self.module)
            ok = mod.apply(self.pm)
            self.status_cb(
                f"{mod.label}: {'ON' if ok else 'failed - pattern not found (game may have updated)'}"
            )
        elif action == "remove":
            ok = mod.remove(self.pm)
            self.status_cb(f"{mod.label}: {'OFF' if ok else 'nothing to remove'}")


# GUi

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class TrainerApp:
    def __init__(self, root):
        self.root = root
        root.title("ROTTR Mod Menu")
        root.geometry("380x280")
        root.resizable(False, False)

        icon_path = get_resource_path("mod.ico")
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
        style.configure(
            ".",
            background=bg,
            foreground=fg,
            fieldbackground=bg,
        )
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

        ttk.Label(frame, text="Unlimited Ammo", font=("Segoe UI", 10, "bold")).pack(
            anchor="w"
        )
        self.ammo_var = tk.StringVar(value="off")
        ttk.Radiobutton(
            frame,
            text="Off",
            variable=self.ammo_var,
            value="off",
            command=self.on_ammo_change,
        ).pack(anchor="w")
        ttk.Radiobutton(
            frame,
            text="With Reload",
            variable=self.ammo_var,
            value="with_reload",
            command=self.on_ammo_change,
        ).pack(anchor="w")
        ttk.Radiobutton(
            frame,
            text="Without Reload",
            variable=self.ammo_var,
            value="no_reload",
            command=self.on_ammo_change,
        ).pack(anchor="w")

        ttk.Separator(frame).pack(fill="x", pady=8)

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
            text="Unlimited Resources",
            variable=self.resources_var,
            command=self.on_resources_change,
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="(gather at least one of a resource first)",
            foreground=note_fg,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=20)

        ttk.Separator(frame).pack(fill="x", pady=8)

        self.status_var = tk.StringVar(value="Starting...")
        self.latest_status = "Starting..."
        ttk.Label(
            frame, textvariable=self.status_var, foreground="#0a7a3d", wraplength=340
        ).pack(anchor="w")

        self.status_queue = queue.Queue()
        self.engine = Engine(
            [AMMO_WITH_RELOAD, AMMO_NO_RELOAD, SURVIVAL, RESOURCES],
            self.status_queue.put,
        )

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.poll_status)

    def _compose_status(self):
        active_mods = []
        ammo_choice = self.ammo_var.get()
        if ammo_choice == "with_reload":
            active_mods.append("Unlimited Ammo: With Reload")
        elif ammo_choice == "no_reload":
            active_mods.append("Unlimited Ammo: Without Reload")
        if self.survival_var.get():
            active_mods.append("Unlimited Survival Instinct: ON")
        if self.resources_var.get():
            active_mods.append("Unlimited Resources: ON")

        lines = [self.latest_status] if self.latest_status else []
        if active_mods:
            lines.append("Active mods:")
            lines.extend(active_mods)
        elif not lines:
            lines.append("No mods active")
        return "\n".join(lines)

    def poll_status(self):
        try:
            while True:
                self.latest_status = self.status_queue.get_nowait()
        except queue.Empty:
            pass
        self.status_var.set(self._compose_status())
        self.root.after(200, self.poll_status)

    def on_ammo_change(self):
        choice = self.ammo_var.get()
        if choice != "with_reload":
            self.engine.request("remove", "ammo_with_reload")
        if choice != "no_reload":
            self.engine.request("remove", "ammo_no_reload")
        if choice == "with_reload":
            self.engine.request("apply", "ammo_with_reload")
        elif choice == "no_reload":
            self.engine.request("apply", "ammo_no_reload")

    def on_survival_change(self):
        self.engine.request(
            "apply" if self.survival_var.get() else "remove", "survival"
        )

    def on_resources_change(self):
        self.engine.request(
            "apply" if self.resources_var.get() else "remove", "resources"
        )

    def on_close(self):
        self.engine.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    TrainerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
