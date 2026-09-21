# -*- coding: utf-8 -*-
"""入口控制台策略的单元测试——守护路径永不申请窗口（黑窗防回归）。

背景见 hhu_console 模块说明：计划任务每 N 分钟拉起一次守护，只要它申请了控制台，
用户就会看到黑框定时闪。这里锁住"谁可以要窗口"这条不变量。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hhu_console as hc
import hhu_login as hl

INTERACTIVE = ("--version", "--check", "--setup", "--logout", "--clear-flag", "--loop")


def test_guard_path_never_wants_window():
    """计划任务调用的两种形态：裸跑一次、带 --quiet。--quiet 压过一切。"""
    assert hl.console_wanted([]) is False
    assert hl.console_wanted(["--quiet"]) is False
    assert hl.console_wanted(["--quiet", "--loop"]) is False
    assert hl.console_wanted(["--unknown"]) is False


def test_interactive_commands_want_window():
    for flag in INTERACTIVE:
        assert hl.console_wanted([flag]) is True, flag


def test_hold_open_only_for_multiline_reports():
    """只有 --check / --setup 需要在自建窗口里留住输出，其余不阻塞退出。"""
    assert hl.console_hold(["--check"]) is True
    assert hl.console_hold(["--setup"]) is True
    assert hl.console_hold(["--quiet", "--check"]) is False
    assert hl.console_hold(["--clear-flag"]) is False
    assert hl.console_hold([]) is False


def test_setup_keeps_usable_streams_untouched():
    """已有有效输出目标（管道 / 重定向 / 真控制台）时不许动标准流。"""

    class _FakePipe:
        def fileno(self):
            return 1

        def write(self, s):
            return len(s)

        def flush(self):
            return None

        def isatty(self):
            return False

    pipe = _FakePipe()
    old = (sys.stdout, sys.stderr, sys.stdin)
    sys.stdout = sys.stderr = pipe
    try:
        assert hc.setup() == "console"
        assert sys.stdout is pipe and sys.stderr is pipe
    finally:
        sys.stdout, sys.stderr, sys.stdin = old


def test_null_io_is_safe_sink():
    """无控制台时的兜底流：print / 顶部 reconfigure 都不能炸。"""
    sink = hc._NullIO()
    assert sink.write("中文\n") == 3
    assert sink.flush() is None
    assert sink.isatty() is False
    assert sink.readline() == ""
    sink.reconfigure(encoding="utf-8", errors="replace")
