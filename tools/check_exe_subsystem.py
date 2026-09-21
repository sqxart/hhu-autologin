#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验打包出来的 exe 子系统——黑窗防回归闸门。

PyInstaller 不加 --windowed 打出来的是控制台子系统（IMAGE_SUBSYSTEM_WINDOWS_CUI），
Windows 每次启动都会给它分配一个控制台窗口。计划任务每 N 分钟调用一次守护，
用户就会看到黑框定时闪（v1.4.2 及以前便携版的实际问题，见 CHANGELOG）。
CI 用本脚本卡住这个坑，本地手动打包后也建议跑一遍：

    python tools/check_exe_subsystem.py dist/hhu_login.exe dist/hhu_gui.exe
    python tools/check_exe_subsystem.py dist/hhu_campus_report.exe --expect cui
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# PE 可选头里 Subsystem 字段的取值：2=GUI 子系统（无控制台），3=控制台子系统
SUBSYSTEMS = {2: "gui", 3: "cui"}
PE_SIGNATURE = b"PE\0\0"
OPTIONAL_HEADER_OFFSET = 4 + 20   # PE 签名 4 字节 + COFF 头 20 字节
SUBSYSTEM_OFFSET = 68             # 可选头内偏移，PE32 与 PE32+ 相同


def read_subsystem(path: Path) -> str:
    with path.open("rb") as f:
        head = f.read(0x400)
    if head[:2] != b"MZ":
        raise ValueError("不是 PE 文件（缺 MZ 头）")
    pe = struct.unpack_from("<I", head, 0x3C)[0]
    if head[pe:pe + 4] != PE_SIGNATURE:
        raise ValueError("PE 签名缺失")
    value = struct.unpack_from("<H", head, pe + OPTIONAL_HEADER_OFFSET + SUBSYSTEM_OFFSET)[0]
    if value not in SUBSYSTEMS:
        raise ValueError(f"未知子系统取值 {value}")
    return SUBSYSTEMS[value]


def main() -> int:
    ap = argparse.ArgumentParser(description="校验 exe 的 PE 子系统")
    ap.add_argument("files", nargs="+", help="要校验的 exe")
    ap.add_argument("--expect", choices=("gui", "cui"), default="gui",
                    help="期望的子系统（默认 gui，即 --windowed 的产物）")
    args = ap.parse_args()

    bad = 0
    for name in args.files:
        path = Path(name)
        if not path.exists():
            print(f"[x] 找不到 {path}")
            bad += 1
            continue
        try:
            got = read_subsystem(path)
        except Exception as e:
            print(f"[x] {path.name}: 读取失败 {e!r}")
            bad += 1
            continue
        if got == args.expect:
            print(f"[√] {path.name}: {got}（符合期望）")
            continue
        hint = "——少了 --windowed，计划任务会定时闪黑窗" if args.expect == "gui" else ""
        print(f"[x] {path.name}: {got}，期望 {args.expect}{hint}")
        bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
