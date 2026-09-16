#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双击启动可视化控制台 —— 不会弹出黑色控制台窗口。

为什么双击 hhu_gui.py 有黑窗：Windows 把 .py 关联到 python.exe（带控制台），
双击就会先开一个黑色命令行窗口再显示界面。
本文件扩展名是 .pyw，Windows 默认用 pythonw（无控制台版解释器）运行它——
双击直接出界面，全程无黑窗。

它只是个入口壳：所有逻辑都在旁边的 hhu_gui.py 里，两者别拆散。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import hhu_gui
except Exception as e:
    # pythonw 没有控制台，出错必须弹消息框，否则双击毫无反应、没法排查
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        None,
        "启动失败：{}\n\n"
        "请确认本文件和 hhu_gui.py 在同一个文件夹。\n"
        "仍不行就用终端运行  python hhu_gui.py  查看完整报错。".format(e),
        "河海校园网自动登录", 0x10)
    sys.exit(1)

sys.exit(hhu_gui.main())
