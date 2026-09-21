#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制台粘连：让 --windowed 打包的守护 exe「后台全静默」与「手动看输出」兼得。

背景（2026-09 实测）：计划任务每 N 分钟调一次 hhu_login.exe。exe 若按 PyInstaller
默认（控制台子系统）打包，Windows 每次启动都会给它分配一个黑色控制台窗口——
于是守护变成定时闪一下黑框。所以守护 exe 必须按 --windowed（GUI 子系统）打包：
进程天生没有控制台，计划任务拉起时连窗口都不创建（实测 GetConsoleWindow()==0）。

代价是手动运行时 print 没有去处，本模块负责把输出接回来：

    父进程有控制台  -> AttachConsole：接回那个窗口（在 cmd / PowerShell 里直接运行）
    双击快捷方式    -> AllocConsole：自己开一个（只有 --check/--setup 等交互式子命令）
    计划任务拉起    -> 什么都不开，输出丢弃（守护路径的硬保证，绝不申请窗口）

中文输出走 WriteConsoleW（CPython 自己写控制台也是这个 API），不受控制台代码页影响；
控制台输入按控制台代码页解码，GBK 控制台下输入中文不乱码。
非 Windows 上一律不动标准流（本模块被 hhu_login 导入，跨平台 CI 也会 import 它）。
"""
from __future__ import annotations

import atexit
import ctypes
import io
import os
import sys
import time

IS_WIN = os.name == "nt"

# 自建的控制台窗口会随进程退出立刻消失，多行报告（--check / --setup）用户根本来不及看，
# 所以自建窗口时留一个"按任意键关闭"的缓冲；给个上限，避免被自动化调用时卡死
HOLD_SECONDS = 60

ATTACH_PARENT_PROCESS = 0xFFFFFFFF
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = 0xFFFFFFFFFFFFFFFF

# 自己声明 ctypes 类型别名，不用 ctypes.wintypes——后者在非 Windows 上是坑
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p

_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [LPCWSTR, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE]
        k.CreateFileW.restype = HANDLE
        k.AttachConsole.argtypes = [DWORD]
        k.AttachConsole.restype = BOOL
        k.AllocConsole.argtypes = []
        k.AllocConsole.restype = BOOL
        k.WriteConsoleW.argtypes = [HANDLE, LPCWSTR, DWORD, ctypes.POINTER(DWORD), HANDLE]
        k.WriteConsoleW.restype = BOOL
        k.SetConsoleTitleW.argtypes = [LPCWSTR]
        k.GetConsoleCP.argtypes = []
        k.GetConsoleCP.restype = ctypes.c_uint
        k.GetConsoleWindow.argtypes = []
        k.GetConsoleWindow.restype = HANDLE
        _k32 = k
    return _k32


# ---------------------------------------------------------------- 备用流

class _NullIO(io.TextIOBase):
    """没有控制台时的黑洞：print 不报错、isatty() 为假、读取直接 EOF。"""

    def write(self, s):
        return len(s)

    def flush(self):
        return None

    def isatty(self):
        return False

    def readable(self):
        return True

    def readline(self, size=-1):
        return ""

    def reconfigure(self, **kw):
        return None


class _ConsoleWriter(io.TextIOBase):
    """WriteConsoleW 输出：中文在任何控制台代码页下都不乱码。"""

    def __init__(self, handle):
        self._h = handle

    def write(self, s):
        text = str(s)
        if not text:
            return 0
        k = _kernel32()
        left = text
        while left:  # WriteConsoleW 单次上限约 32K 字符，切块写更保险
            chunk, left = left[:8192], left[8192:]
            written = DWORD(0)
            if not k.WriteConsoleW(self._h, chunk, len(chunk), ctypes.byref(written), None):
                break
        return len(text)

    def flush(self):
        return None

    def isatty(self):
        return True

    def reconfigure(self, **kw):
        return None

    @property
    def encoding(self):
        return "utf-8"


def _usable(stream) -> bool:
    """流已指向有效目标（真控制台 / 管道 / 重定向文件）时为真——那就别动它。"""
    if stream is None:
        return False
    try:
        return stream.fileno() >= 0
    except Exception:
        return False


def _console_input_encoding() -> str:
    """控制台输入按控制台代码页解码（本机默认 GBK 936）。"""
    try:
        cp = _kernel32().GetConsoleCP()
    except Exception:
        return "gbk"
    return {936: "gbk", 65001: "utf-8", 950: "big5", 932: "cp932", 949: "cp949"}.get(cp, "cp%d" % cp)


def _open_console(name: str, mode: str, encoding: str = "utf-8"):
    k = _kernel32()
    h = k.CreateFileW(name, GENERIC_READ | GENERIC_WRITE,
                      FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if not h or h == INVALID_HANDLE_VALUE:
        return None
    if mode == "w":
        return _ConsoleWriter(h)
    # 输入仍走文本流：input()/getpass 需要 readline 语义
    return open(name, "r", encoding=encoding, errors="replace")


def _bind_console(title: str = "") -> bool:
    """把标准流接到当前控制台。调用前必须已 AttachConsole / AllocConsole。"""
    out = _open_console("CONOUT$", "w")
    err = _open_console("CONOUT$", "w")
    if out is None or err is None:
        return False
    try:
        stdin = _open_console("CONIN$", "r", _console_input_encoding())
    except OSError:
        stdin = None
    if stdin is None:
        stdin = _NullIO()
    sys.stdout, sys.stderr, sys.stdin = out, err, stdin
    # __std*__ 同步替换：getpass 靠 sys.stdin is sys.__stdin__ 决定用
    # msvcrt 无回显通道，不换的话密码会明着回显
    sys.__stdout__, sys.__stderr__, sys.__stdin__ = out, err, stdin
    if title:
        try:
            _kernel32().SetConsoleTitleW(title)
        except Exception:
            pass
    return True


def _bind_null() -> None:
    """没控制台时兜底：只替换不可用的流，避免标准库拿到 None 抛 AttributeError。"""
    for name, dunder in (("stdout", "__stdout__"), ("stderr", "__stderr__"),
                         ("stdin", "__stdin__")):
        if not _usable(getattr(sys, name)):
            sink = _NullIO()
            setattr(sys, name, sink)
            setattr(sys, dunder, sink)


# ---------------------------------------------------------------- 入口

def _hold_console(seconds: int) -> None:
    """留给用户读结果的时间：按任意键立即关闭，最多等 seconds 秒。"""
    try:
        import msvcrt
    except ImportError:
        return
    print("")
    print("（按任意键关闭本窗口；%d 秒后自动关闭）" % seconds)
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if msvcrt.kbhit():
                msvcrt.getwch()
                return
        except Exception:
            return
        time.sleep(0.1)


def setup(want_window: bool = False, title: str = "hhu-autologin",
          hold_open: bool = False) -> str:
    """接好标准流并返回状态：

    console   —— 本来就有有效输出目标（源码运行 / 重定向 / 控制台版 exe），未改动
    attached  —— 接回了已有控制台（不会创建窗口）
    allocated —— 自己开了一个控制台窗口（仅交互式子命令）
    none      —— 无控制台也无需求：全静默（计划任务守护路径）

    hold_open 只在 allocated 时生效：自建窗口会随进程退出消失，多行报告需要留住。
    """
    if not IS_WIN:
        return "console"
    if _usable(sys.stdout) and _usable(sys.stderr):
        return "console"
    k = _kernel32()
    # AttachConsole 附到父进程的控制台——只接线，不创建窗口。已经附着的进程再调会失败，
    # 所以用 GetConsoleWindow 兜底判断"本来就有控制台可用"
    k.AttachConsole(ATTACH_PARENT_PROCESS)
    if k.GetConsoleWindow() and _bind_console():
        return "attached"
    if want_window and k.AllocConsole() and _bind_console(title):
        if hold_open:
            atexit.register(_hold_console, HOLD_SECONDS)
        return "allocated"
    _bind_null()
    return "none"
