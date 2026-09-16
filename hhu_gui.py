#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hhu_gui.py —— hhu-autologin 可视化控制台（tkinter，零第三方依赖）

用法:
    hhu_gui.py    # 双击 exe 或 python hhu_gui.py 打开控制台窗口

定位：后台守护由计划任务每分钟无窗口运行，本窗口是它的"驾驶舱"——
    账号配置：一键抓取账号与服务（也可手填），保存即生效
    守护设置：开机自启开关、检测间隔、立即检测登录状态、退出账号、实时日志
    主题：默认（浅色）/ 暗黑（深色工业风，遵循 ak-ui 设计契约）
所有网络/子进程操作都在后台线程执行，界面不卡顿。
"""
from __future__ import annotations

import configparser
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import hhu_login as hl
import ak_icon

TASK_NAME = "HHU-AutoLogin"
REPO_URL = "https://github.com/sqxart/hhu-autologin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---------------------------------------------------------------- 系统托盘（纯 ctypes，零依赖）

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
    NIIF_INFO = 0x1
    WM_APP_TRAY = 0x8001                       # 托盘回调消息（WM_APP+1）
    WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
    GWLP_WNDPROC = -4
    TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
    MF_STRING = 0x0
    IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTCOLOR = 1, 0x10, 0x0
    SM_CXSMICON, SM_CYSMICON = 49, 50

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint), ("hWnd", wintypes.HWND), ("uID", ctypes.c_uint),
            ("uFlags", ctypes.c_uint), ("uCallbackMessage", ctypes.c_uint),
            ("hIcon", wintypes.HICON), ("szTip", ctypes.c_wchar * 128),
            ("dwState", ctypes.c_uint), ("dwStateMask", ctypes.c_uint),
            ("szInfo", ctypes.c_wchar * 256), ("uVersion", ctypes.c_uint),
            ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", ctypes.c_uint),
            ("guidItem", ctypes.c_ubyte * 16),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                                 wintypes.WPARAM, wintypes.LPARAM)
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.CallWindowProcW.restype = ctypes.c_ssize_t
    _user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                                        wintypes.WPARAM, wintypes.LPARAM]
    _shell32.Shell_NotifyIconW.argtypes = [ctypes.c_uint, ctypes.POINTER(NOTIFYICONDATAW)]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                        wintypes.HINSTANCE, wintypes.LPVOID]
    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", ctypes.c_void_p), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.DestroyIcon.restype = wintypes.BOOL
    _user32.DestroyIcon.argtypes = [wintypes.HICON]


def _make_tray_icon():
    """托盘图标：多尺寸 PNG 打包成 .ico 后 LoadImage（返回 HICON）。

    不要退回 CreateIcon + 24 位原始位图那条老路：实测那样画出来图标全黑
    （颜色数据在转换中丢失，且 24 位无 alpha 无法表达抗锯齿）。
    PNG 图标带真实 alpha，与窗口/任务栏共用同一份绘制数据。
    """
    data = ak_icon.ak_icon_ico()
    path = None
    for base in (hl.LOG_DIR, pathlib.Path(tempfile.gettempdir())):
        try:
            base.mkdir(parents=True, exist_ok=True)
            cand = base / "hhu_tray_icon.ico"
            if not cand.exists() or cand.read_bytes() != data:
                cand.write_bytes(data)
            path = cand
            break
        except Exception:
            continue
    if path is None:
        return None
    cx = _user32.GetSystemMetrics(SM_CXSMICON) or 16
    cy = _user32.GetSystemMetrics(SM_CYSMICON) or 16
    return _user32.LoadImageW(None, str(path), IMAGE_ICON, cx, cy,
                              LR_LOADFROMFILE | LR_DEFAULTCOLOR) or None


class TrayIcon:
    """零依赖系统托盘：独立隐藏窗口 + 专属消息泵线程，事件经队列送回主线程。

    不挂钩 Tk 的窗口过程，避免与 Tk 事件循环互相干扰。
    """

    def __init__(self, tooltip):
        import queue
        self._events = queue.Queue()
        self._tooltip = tooltip
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if not self._added:
            raise ctypes.WinError(ctypes.get_last_error())

    def _pump(self):
        """托盘专用线程：建隐藏窗口、挂图标、泵消息；菜单也在这条线程弹出。"""
        cls_name = "HHUAutoLoginTray"
        wndproc = WNDPROC(self._wndproc)
        self._cb_ref = wndproc                  # 回调保活
        wc = WNDCLASSW()
        wc.lpfnWndProc = wndproc
        wc.lpszClassName = cls_name
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        _user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = _user32.CreateWindowExW(0, cls_name, "hhu_tray", 0,
                                            0, 0, 0, 0, None, None, wc.hInstance, None)
        self._hicon = _make_tray_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd, nid.uID = self.hwnd, 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self._tooltip
        self._added = bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        self._ready.set()
        if not self._added:
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_APP_TRAY:
            if lp == WM_LBUTTONUP:
                self._events.put("left")
            elif lp == WM_RBUTTONUP:
                self._events.put(("menu", self._popup()))
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _popup(self):
        """在托盘线程弹出菜单，返回选中项 id（0=未选）。"""
        hmenu = _user32.CreatePopupMenu()
        for iid, text in self.menu_items:
            _user32.AppendMenuW(hmenu, MF_STRING, iid, text)
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        _user32.SetForegroundWindow(self.hwnd)  # 否则菜单点击外部不消失
        cmd = _user32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                     pt.x, pt.y, 0, self.hwnd, None)
        _user32.PostMessageW(self.hwnd, 0, 0, 0)
        _user32.DestroyMenu(hmenu)
        return int(cmd)

    def bubble(self, title, msg):
        """气泡通知（Shell_NotifyIcon 可跨线程调用）。"""
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd, nid.uID = self.hwnd, 1
        nid.uFlags = NIF_INFO
        nid.szInfo, nid.szInfoTitle, nid.dwInfoFlags = msg, title, NIIF_INFO
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def poll_event(self):
        """主线程非阻塞取一个托盘事件；无事件返回 None。"""
        try:
            return self._events.get_nowait()
        except Exception:
            return None

    def close(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd, nid.uID = self.hwnd, 1
        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        if getattr(self, "_hicon", None):
            _user32.DestroyIcon(self._hicon)
        _user32.PostThreadMessageW(self._thread.ident, 0x0012, 0, 0)  # WM_QUIT

SERVICE_CHOICES = ["校园网", "移动", "电信", "联通"]
# 校区 -> WiFi 门卫 SSID。服务提交值由服务器按校区自动下发，无需选校区；
# 这里只影响"不在校园 WiFi 时跳过守护"的检查。江宁/西康路确切 SSID 待同学反馈，
# 先用宽松值"Hohai"（子串匹配所有 Hohai 开头的 SSID）。
CAMPUS_CHOICES = {
    "1. 严格仅校园网登录（若失败选下一选项）": "Hohai University",
    "2. 仅校园网登录（若失败选下一选项）": "Hohai",
    "3. 无论连接的是否为校园网都尝试登录": "",
}
# 账号配置区的校区选择：金坛服务提交值已实测，其他校区需现场抓取
CAMPUS_AREA = {
    "金坛校区（服务配置已实测）": ("jintan", SERVICE_CHOICES),
    "江宁校区（服务需现场抓取）": ("jiangning", None),
    "西康路校区（服务需现场抓取）": ("xikanglu", None),
}

# 主题遵循 ak-ui 设计契约：中性面层为主，色彩只作信号（黄=行动/警告、
# 青=信息、绿=成功、红=危险），工业感、扁平、无渐变无发光。
THEMES: dict[str, dict] = {
    "默认": dict(
        bg="#f0f0f0", panel="#f5f5f5", fg="#1a1a1a", muted="#666666",
        border="#b8b8b8", entry_bg="#ffffff", entry_fg="#1a1a1a",
        btn_bg="#e1e1e1", btn_fg="#1a1a1a", btn_border="#ababab",
        btn_hover="#d0d0d0", btn_press="#c4c4c4",
        accent="#0067c0", ok="#0a7d32", err="#b3261e",
        log_bg="#ffffff", log_fg="#333333", link="#0067c0",
    ),
    "暗黑": dict(
        bg="#1b1b1d", panel="#232326", fg="#e6e6e6", muted="#8f8f94",
        border="#3a3a3f", entry_bg="#2a2a2e", entry_fg="#f0f0f0",
        btn_bg="#2e2e33", btn_fg="#e6e6e6", btn_border="#4a4a51",
        btn_hover="#3a3a40", btn_press="#45454c",
        accent="#ffcc00", ok="#4caf50", err="#ff5252",
        log_bg="#141416", log_fg="#a5c9a1", link="#ffcc00",
    ),
}


# ---------------------------------------------------------------- 计划任务

def _task_target() -> str:
    """计划任务要执行的无窗口命令行（守护逻辑在 hhu_login，不是 GUI）。"""
    if getattr(sys, "frozen", False):
        login_exe = hl.BASE / "hhu_login.exe"
        if login_exe.exists():
            return f'"{login_exe}" --quiet'
        base = hl.BASE / "hhu_login.py"
        pyw = shutil.which("pythonw")
        return f'"{pyw}" "{base}" --quiet' if pyw else ""
    pyw = shutil.which("pythonw")
    return f'"{pyw}" "{hl.BASE / "hhu_login.py"}" --quiet' if pyw else ""


def task_exists() -> bool:
    r = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME],
                       capture_output=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def install_task(interval: int):
    target = _task_target()
    if not target:
        return False, "未找到 pythonw / hhu_login.exe，无法注册无窗口计划任务"
    r = subprocess.run(
        ["schtasks", "/create", "/tn", TASK_NAME, "/tr", target,
         "/sc", "minute", "/mo", str(max(1, int(interval))), "/f"],
        capture_output=True, creationflags=NO_WINDOW)
    if r.returncode == 0:
        return True, f"计划任务已注册（每 {max(1, int(interval))} 分钟检测一次）"
    return False, (r.stderr.decode("gbk", errors="replace") or r.stdout.decode("gbk", errors="replace"))[:200]


def uninstall_task():
    r = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
                       capture_output=True, creationflags=NO_WINDOW)
    return r.returncode == 0


# ---------------------------------------------------------------- 配置读写

def read_config() -> dict:
    """读取现有配置（不要求账号已填，GUI 允许从零开始）。"""
    cp = configparser.ConfigParser()
    if hl.CONFIG_FILE.exists():
        cp.read(hl.CONFIG_FILE, encoding="utf-8")
    return {
        "username": cp.get("account", "username", fallback=""),
        "password": cp.get("account", "password", fallback=""),
        "service": cp.get("account", "service", fallback="校园网"),
        "interval": cp.getint("guard", "interval_minutes", fallback=1),
        "wifi_ssid": cp.get("guard", "wifi_ssid", fallback=""),
        "auto_exit_after_login": cp.getboolean("guard", "auto_exit_after_login", fallback=False),
        "auto_exit_minutes": cp.getint("guard", "auto_exit_minutes", fallback=0),
        "campus_area": cp.get("guard", "campus_area", fallback="jintan"),
        "notify_on_success": cp.getboolean("notify", "on_success", fallback=True),
        "notify_on_failure": cp.getboolean("notify", "on_failure", fallback=True),
    }


def sync_runtime_config(cfg: dict) -> None:
    """把配置同步进 hhu_login 的运行时全局 CONFIG。

    CLI 靠 load_config() 填充（空账号会 sys.exit，GUI 不能用）；
    run_once/ssid_gate/do_login 都依赖它，不填会 KeyError。
    """
    hl.CONFIG.update({
        "username": cfg["username"], "password": cfg["password"], "service": cfg["service"],
        "interval": cfg["interval"], "wifi_ssid": cfg["wifi_ssid"], "debug": True,
        "notify_enabled": cfg["notify_on_success"] or cfg["notify_on_failure"],
        "notify_on_success": cfg["notify_on_success"],
        "notify_on_failure": cfg["notify_on_failure"],
        "notify_cooldown": 30,
    })


def save_all(username: str, password: str, service: str, interval: int, wifi_ssid: str,
             auto_exit_after_login: bool = False, auto_exit_minutes: int = 0,
             campus_area: str = "jintan",
             notify_on_success: bool = True, notify_on_failure: bool = True) -> None:
    hl.save_account(username, password, service)
    cp = configparser.ConfigParser()
    if hl.CONFIG_FILE.exists():
        cp.read(hl.CONFIG_FILE, encoding="utf-8")
    if not cp.has_section("guard"):
        cp.add_section("guard")
    cp.set("guard", "interval_minutes", str(max(1, int(interval))))
    cp.set("guard", "wifi_ssid", wifi_ssid.strip())
    cp.set("guard", "auto_exit_after_login", "true" if auto_exit_after_login else "false")
    cp.set("guard", "auto_exit_minutes", str(max(0, int(auto_exit_minutes))))
    cp.set("guard", "campus_area", campus_area)
    if not cp.has_section("notify"):
        cp.add_section("notify")
    cp.set("notify", "enabled", "true" if (notify_on_success or notify_on_failure) else "false")
    cp.set("notify", "on_success", "true" if notify_on_success else "false")
    cp.set("notify", "on_failure", "true" if notify_on_failure else "false")
    with hl.CONFIG_FILE.open("w", encoding="utf-8") as f:
        cp.write(f)


EXIT_CODE_MSG = {
    0: "√ 检测完成：在线或已自动登录成功",
    1: "! 门户链路不可达（不在校园网？）",
    2: "! 登录失败：网络或服务器异常，将继续重试",
    3: "! 已跳过：断路器置位或当前 WiFi 不在允许列表",
    4: "! 认证被拒：账号或密码错误（已停止重试）",
    5: "! 配置未填写：请先在「账号配置」里保存账号密码",
}


# ---------------------------------------------------------------- 界面

class App(tk.Tk):
    MENU_OPEN, MENU_CHECK, MENU_DAEMON, MENU_EXIT = 1001, 1002, 1004, 1003

    def __init__(self, start_hidden: bool = False):
        super().__init__()
        self.title(f"河海校园网自动登录 · 控制台 v{hl.__version__}")
        # 高 DPI：按真实缩放比放大窗口/字体/间距；屏幕放不下时转紧凑布局
        dpi = self.winfo_fpixels("1i") or 96.0
        self._dpi_scale = max(1.0, dpi / 96.0)
        self.tk.call("tk", "scaling", dpi / 72.0)
        need_w, need_h = int(660 * self._dpi_scale), int(860 * self._dpi_scale)
        screen_h = self.winfo_screenheight()
        self._compact = need_h > screen_h - 60   # 小屏笔记本 / 超高缩放：压缩间距塞下全部功能
        self._pv = 3 if self._compact else 6     # 统一区块间距
        self.geometry(f"{need_w}x{min(need_h, screen_h - 60)}")
        self.resizable(False, False)
        self.th = THEMES["默认"]
        self._last_log_text = ""
        self._close_tip_shown = False
        self._exiting = False
        self._daemon_on = True
        self._build_style()
        self._build()
        try:  # 方舟风窗口/任务栏图标（内存 PNG，无资源文件；多尺寸供系统挑选）
            self._icon_refs = [tk.PhotoImage(data=ak_icon.ak_icon_png(s)) for s in (32, 48)]
            self.iconphoto(True, *self._icon_refs)
        except Exception:
            pass
        self.apply_theme("默认")
        self._refresh_status()
        self._refresh_autostart()
        self._tick_log()
        self.update()  # 强制完成顶层窗口映射，托盘挂钩需要真实的 HWND
        self._init_tray()
        self.after(120, self._drain_tray)
        self._start_daemon()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if start_hidden:
            self.after(50, self.withdraw)

    # ---------- 系统托盘与常驻守护

    def _init_tray(self):
        try:
            self.tray = TrayIcon(f"河海校园网自动登录 v{hl.__version__}")
            self._sync_tray_menu()
        except Exception as e:
            self.tray = None
            hl.dlog(f"gui: tray init fail: {e!r}")

    def _drain_tray(self):
        """把托盘线程的事件分发到主线程。"""
        if self._exiting:
            return
        if self.tray:
            while True:
                evt = self.tray.poll_event()
                if evt is None:
                    break
                if evt == "left":
                    self.show_window()
                elif isinstance(evt, tuple) and evt[0] == "menu":
                    self._on_tray_menu(evt[1])
        self.after(120, self._drain_tray)

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _sync_tray_menu(self):
        """按守护状态刷新托盘菜单（停止/恢复互斥显示）。"""
        if not self.tray:
            return
        daemon_item = ((self.MENU_DAEMON, "停止后台守护（不再自动登录）") if self._daemon_on
                       else (self.MENU_DAEMON, "恢复后台守护（重新自动登录）"))
        self.tray.menu_items = [
            (self.MENU_OPEN, "打开控制窗口"),
            (self.MENU_CHECK, "立即检测登录状态"),
            daemon_item,
            (self.MENU_EXIT, "退出控制台"),
        ]

    def _on_tray_menu(self, cmd):
        if cmd == self.MENU_OPEN:
            self.show_window()
        elif cmd == self.MENU_CHECK:
            self.on_check()
        elif cmd == self.MENU_DAEMON:
            if self._daemon_on:
                if messagebox.askyesno(
                        "停止后台守护",
                        "停止后将不再自动检测登录（计划任务一并卸载），\n"
                        "控制台保留，可随时在托盘菜单恢复。确定停止吗？"):
                    self._stop_daemon("托盘手动停止")
            else:
                self._resume_daemon()
        elif cmd == self.MENU_EXIT:
            state = "后台守护仍在运行" if self._daemon_on else "后台守护已停止"
            if messagebox.askyesno("退出控制台", f"{state}。\n退出只是关闭这个窗口，确定吗？"):
                self._real_exit()

    def on_close(self):
        """点窗口 × = 缩到托盘继续守护，而不是退出。"""
        self.withdraw()
        if not self._close_tip_shown and self.tray:
            self._close_tip_shown = True
            self.tray.bubble("仍在后台运行",
                             "控制台已缩到系统托盘，右键托盘图标可打开窗口或退出。")

    def _real_exit(self):
        self._exiting = True
        try:
            if self.tray:
                self.tray.close()
        except Exception:
            pass
        self.destroy()

    def _start_daemon(self):
        """控制台常驻期间内嵌守护循环（与计划任务共存无害：在线检测幂等）。"""
        cfg = read_config()
        self._daemon_interval = max(1, cfg["interval"])
        if cfg["auto_exit_minutes"] > 0:
            self.after(cfg["auto_exit_minutes"] * 60000,
                       lambda: self._stop_daemon(f"检测已运行 {cfg['auto_exit_minutes']} 分钟"))
        self.after(5000, self._daemon_tick)

    def _daemon_tick(self):
        if self._exiting:
            return
        if self._daemon_on:
            self._bg(lambda: hl.run_once(), self._daemon_done, busy=False)
        self.after(self._daemon_interval * 60000, self._daemon_tick)

    def _daemon_done(self, res):
        if res == 0 and not self._exiting and read_config()["auto_exit_after_login"]:
            self._stop_daemon("网络在线（登录成功）")

    def _stop_daemon(self, reason):
        """停止后台守护：卸载计划任务 + 暂停内嵌循环；控制台保留。"""
        self._daemon_on = False

        def work():
            return uninstall_task()
        def done(res):
            self._refresh_autostart()
            self._sync_tray_menu()
            if self.tray:
                try:
                    self.tray.bubble("后台守护已停止", f"{reason}。恢复方式：托盘菜单「恢复后台守护」。")
                except Exception:
                    pass
        self._bg(work, done, busy=False)

    def _resume_daemon(self):
        """恢复后台守护：重新注册计划任务 + 恢复内嵌循环。"""
        self._daemon_on = True
        try:
            interval = int(self.spn_interval.get())
        except ValueError:
            interval = 1
        self._bg(lambda: install_task(interval),
                 lambda res: (self._refresh_autostart(), self._sync_tray_menu()))

    # ---------- ttk 主题与配色

    def _build_style(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")  # clam 最可定制，两套主题统一在它上面覆盖

    def apply_theme(self, name: str):
        self.theme_name = name
        t = self.th = THEMES.get(name, THEMES["默认"])
        s = self.style
        s.configure(".", background=t["bg"], foreground=t["fg"], bordercolor=t["border"])
        s.configure("TFrame", background=t["bg"])
        s.configure("TLabel", background=t["bg"], foreground=t["fg"])
        s.configure("TMuted.TLabel", background=t["bg"], foreground=t["muted"])
        s.configure("TLabelframe", background=t["bg"], bordercolor=t["border"])
        s.configure("TLabelframe.Label", background=t["bg"], foreground=t["accent"])
        s.configure("TButton", background=t["btn_bg"], foreground=t["btn_fg"],
                    bordercolor=t["btn_border"], lightcolor=t["btn_bg"], darkcolor=t["btn_bg"],
                    padding=(8, 4))
        s.map("TButton",
              background=[("pressed", t["btn_press"]), ("active", t["btn_hover"])],
              bordercolor=[("active", t["accent"])])
        s.configure("Accent.TButton", background=t["accent"], foreground="#111111",
                    bordercolor=t["accent"], lightcolor=t["accent"], darkcolor=t["accent"])
        s.map("Accent.TButton",
              background=[("pressed", t["btn_bg"]), ("active", t["btn_hover"])],
              foreground=[("pressed", t["fg"]), ("active", "#111111")])
        s.configure("TEntry", fieldbackground=t["entry_bg"], foreground=t["entry_fg"],
                    insertcolor=t["entry_fg"], bordercolor=t["border"], lightcolor=t["border"],
                    darkcolor=t["border"])
        s.map("TEntry", bordercolor=[("focus", t["accent"])])
        s.configure("TCombobox", fieldbackground=t["entry_bg"], foreground=t["entry_fg"],
                    background=t["btn_bg"], arrowcolor=t["fg"], bordercolor=t["border"],
                    lightcolor=t["border"], darkcolor=t["border"])
        s.map("TCombobox", fieldbackground=[("readonly", t["entry_bg"])],
              foreground=[("readonly", t["entry_fg"])])
        s.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
        s.map("TCheckbutton",
              background=[("active", t["bg"])],
              indicatorcolor=[("selected", t["accent"]), ("!selected", t["entry_bg"])])
        s.configure("TSpinbox", fieldbackground=t["entry_bg"], foreground=t["entry_fg"],
                    arrowcolor=t["fg"], bordercolor=t["border"], lightcolor=t["border"],
                    darkcolor=t["border"], insertcolor=t["entry_fg"])
        self.configure(bg=t["bg"])
        self.txt_log.config(bg=t["log_bg"], fg=t["log_fg"],
                            insertbackground=t["log_fg"])
        self.lbl_link.config(fg=t["link"], bg=t["bg"])
        self._sync_show()
        self._sync_auto()
        self._sync_exit_online()
        self._sync_notify()
        self._refresh_status()  # 动态状态色按新主题立即重刷

    # ---------- 布局

    def _build(self):
        pad = {"padx": 10, "pady": self._pv}
        wrap = lambda w: int(w * self._dpi_scale)   # 折行宽度是像素单位，须随缩放放大

        top = ttk.LabelFrame(self, text="当前状态")
        top.pack(fill="x", padx=10, pady=(self._pv + 2, self._pv))
        row = ttk.Frame(top)
        row.pack(fill="x", padx=10, pady=4)
        self.lbl_status = ttk.Label(row, text="状态: 检测中...")
        self.lbl_status.pack(side="left")
        self.cmb_theme = ttk.Combobox(row, values=list(THEMES), width=8, state="readonly")
        self.cmb_theme.current(0)
        self.cmb_theme.pack(side="right", padx=(0, 6))
        ttk.Label(row, text="主题:").pack(side="right")
        self.cmb_theme.bind("<<ComboboxSelected>>",
                            lambda _e: self.apply_theme(self.cmb_theme.get()))
        self.lbl_flag = ttk.Label(top, text="", wraplength=wrap(600), justify="left")
        self.lbl_flag.pack(anchor="w", padx=10, pady=(0, 4))

        acct = ttk.LabelFrame(self, text="▍账号配置")
        acct.pack(fill="x", padx=10, pady=self._pv)
        ttk.Label(acct, text="学号:").grid(row=0, column=0, sticky="e", padx=8, pady=4)
        self.ent_user = ttk.Entry(acct, width=30)
        self.ent_user.grid(row=0, column=1, columnspan=2, sticky="w", pady=4)

        ttk.Label(acct, text="密码:").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.ent_pass = ttk.Entry(acct, width=30, show="•")
        self.ent_pass.grid(row=1, column=1, sticky="w", pady=4)
        # 自绘勾选标记（ttk clam 指示器画的是叉，有歧义；☐/☑ 跟主题色走）
        self.var_show = tk.BooleanVar(value=False)
        self.chk_show = tk.Label(acct, text="☐ 显示密码", cursor="hand2")
        self.chk_show.grid(row=1, column=2, sticky="w", padx=6)
        self.chk_show.bind("<Button-1>", lambda _e: self._toggle_show())

        ttk.Label(acct, text="校区:").grid(row=2, column=0, sticky="e", padx=8, pady=4)
        self.cmb_area = ttk.Combobox(acct, values=list(CAMPUS_AREA), width=26, state="readonly")
        self.cmb_area.current(0)
        self.cmb_area.grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
        self.cmb_area.bind("<<ComboboxSelected>>", lambda _e: self._on_campus_area())

        ttk.Label(acct, text="服务:").grid(row=3, column=0, sticky="e", padx=8, pady=4)
        self.cmb_service = ttk.Combobox(acct, values=SERVICE_CHOICES, width=12, state="readonly")
        self.cmb_service.current(0)
        self.cmb_service.grid(row=3, column=1, sticky="w", pady=4)
        self.lbl_service_hint = ttk.Label(acct, text="金坛校区服务已实测，可直接选择",
                                          style="TMuted.TLabel", wraplength=wrap(300), justify="left")
        self.lbl_service_hint.grid(row=3, column=2, sticky="w")

        self.lbl_real = ttk.Label(acct, text="", wraplength=wrap(600), justify="left")
        self.lbl_real.grid(row=4, column=1, columnspan=2, sticky="w", pady=(0, 4))

        btns = ttk.Frame(acct)
        btns.grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=2)
        self.btn_fetch = ttk.Button(btns, text="一键抓取账号和服务", style="Accent.TButton",
                                    command=self.on_fetch)
        self.btn_fetch.pack(side="left", padx=(0, 8))
        self.btn_save = ttk.Button(btns, text="保存配置", command=self.on_save)
        self.btn_save.pack(side="left")

        guard = ttk.LabelFrame(self, text="▍设置")
        guard.pack(fill="x", padx=10, pady=self._pv)
        self.var_autostart = tk.BooleanVar(value=False)
        self.var_exit_online = tk.BooleanVar(value=False)
        self.ckb_auto = tk.Label(guard, text="☐ 开机自启（启动后每隔一定时间检测一次登录状态，掉线自动重登）", cursor="hand2")
        self.ckb_auto.pack(anchor="w", padx=10, pady=4)
        self.ckb_auto.bind("<Button-1>", lambda _e: self._toggle_auto())

        row2 = ttk.Frame(guard)
        row2.pack(anchor="w", padx=10, pady=2)
        ttk.Label(row2, text="检测间隔:").pack(side="left")
        self.spn_interval = ttk.Spinbox(row2, from_=1, to=60, width=4)
        self.spn_interval.pack(side="left", padx=4)
        ttk.Label(row2, text="分钟（保存配置后生效）", style="TMuted.TLabel").pack(side="left")

        self.chk_exit_online = tk.Label(guard, text="☐ 登录成功就不再检测（不再自动登录，适合只想开机登录一次的人）",
                                        cursor="hand2")
        self.chk_exit_online.pack(anchor="w", padx=10, pady=2)
        self.chk_exit_online.bind(
            "<Button-1>",
            lambda _e: self._toggle_flag(self.var_exit_online, self._sync_exit_online))
        row_exit = ttk.Frame(guard)
        row_exit.pack(anchor="w", padx=10, pady=2)
        ttk.Label(row_exit, text="或：后台检测运行").pack(side="left")
        self.spn_exit_min = ttk.Spinbox(row_exit, from_=0, to=1440, width=5)
        self.spn_exit_min.pack(side="left", padx=4)
        ttk.Label(row_exit, text="分钟后自动停止（0=不启用）", style="TMuted.TLabel").pack(side="left")

        self.var_notify_ok = tk.BooleanVar(value=True)
        self.var_notify_fail = tk.BooleanVar(value=True)
        self.chk_notify_ok = tk.Label(guard, text="☐ 掉线后自动恢复时通知我（系统弹窗）",
                                      cursor="hand2")
        self.chk_notify_ok.pack(anchor="w", padx=10, pady=2)
        self.chk_notify_ok.bind(
            "<Button-1>",
            lambda _e: self._toggle_flag(self.var_notify_ok, self._sync_notify))
        self.chk_notify_fail = tk.Label(guard, text="☐ 自动登录失败时通知我（系统弹窗）",
                                        cursor="hand2")
        self.chk_notify_fail.pack(anchor="w", padx=10, pady=2)
        self.chk_notify_fail.bind(
            "<Button-1>",
            lambda _e: self._toggle_flag(self.var_notify_fail, self._sync_notify))

        ttk.Label(guard, text="WiFi 门卫（连接非校园网时是否尝试登录）:").pack(anchor="w", padx=10)
        self.cmb_campus = ttk.Combobox(guard, values=list(CAMPUS_CHOICES), width=28, state="readonly")
        self.cmb_campus.pack(anchor="w", padx=10, pady=2)

        row3 = ttk.Frame(guard)
        row3.pack(anchor="w", padx=10, pady=self._pv)
        self.btn_check = ttk.Button(row3, text="立即检测登录状态", style="Accent.TButton",
                                    command=self.on_check)
        self.btn_check.pack(side="left", padx=(0, 8))
        self.btn_logout = ttk.Button(row3, text="退出当前校园网账号", command=self.on_logout)
        self.btn_logout.pack(side="left", padx=(0, 8))
        self.btn_log = ttk.Button(row3, text="打开日志文件夹", command=self.on_open_log)
        self.btn_log.pack(side="left")

        logf = ttk.LabelFrame(self, text="▍实时日志（每次检测 / 掉线与登录记录）")
        logf.pack(fill="x", padx=10, pady=self._pv)
        self.txt_log = tk.Text(logf, height=(2 if self._compact else 8), width=68,
                               state="disabled", wrap="none",
                               font=("Consolas", max(9, round(9 * self._dpi_scale))),
                               relief="flat", highlightthickness=1)
        self.txt_log.pack(fill="x", padx=8, pady=self._pv)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, self._pv + 2))
        self.btn_clear = ttk.Button(bottom, text="重置断路保护", command=self.on_clear_flag)
        self.btn_clear.pack(side="left")
        self.btn_clear.config(state="disabled")
        self.lbl_link = tk.Label(
            bottom, text="GitHub: sqxart/hhu-autologin · 觉得好用来个 Star ⭐",
            cursor="hand2")
        self.lbl_link.pack(side="right")
        self.lbl_link.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))
        self.lbl_clear_hint = ttk.Label(
            self, text="（登录时密码错误触发断路保护，不再尝试登录）",
            style="TMuted.TLabel")
        self.lbl_clear_hint.pack(anchor="w", padx=12, pady=(0, self._pv + 2))

        self._fill_from_config()

    def _fill_from_config(self):
        cfg = read_config()
        sync_runtime_config(cfg)
        self.ent_user.insert(0, cfg["username"])
        self.ent_pass.insert(0, cfg["password"])
        if cfg["service"] in SERVICE_CHOICES:
            self.cmb_service.set(cfg["service"])
        self.spn_interval.delete(0, "end")
        self.spn_interval.insert(0, str(cfg["interval"]))
        self.var_exit_online.set(cfg["auto_exit_after_login"])
        self.var_notify_ok.set(cfg["notify_on_success"])
        self.var_notify_fail.set(cfg["notify_on_failure"])
        self.spn_exit_min.delete(0, "end")
        self.spn_exit_min.insert(0, str(cfg["auto_exit_minutes"]))
        for label, (value, _svcs) in CAMPUS_AREA.items():
            if value == cfg["campus_area"]:
                self.cmb_area.set(label)
                break
        self._sync_area_hint()
        ssid = cfg["wifi_ssid"]
        for label, value in CAMPUS_CHOICES.items():
            if (ssid and ssid.lower() in value.lower()) or value.lower() == ssid.lower():
                self.cmb_campus.set(label)
                break

    def _sync_show(self):
        on = self.var_show.get()
        self.chk_show.config(text=("☑" if on else "☐") + " 显示密码",
                             fg=self.th["accent"] if on else self.th["muted"],
                             bg=self.th["bg"])
        self.ent_pass.config(show="" if on else "•")

    def _toggle_show(self):
        self.var_show.set(not self.var_show.get())
        self._sync_show()

    def _sync_auto(self):
        on = self.var_autostart.get()
        self.ckb_auto.config(text=("☑" if on else "☐") + " 开机自启（启动后每隔一定时间检测一次登录状态，掉线自动重登）",
                             fg=self.th["accent"] if on else self.th["fg"],
                             bg=self.th["bg"])

    def _sync_exit_online(self):
        on = self.var_exit_online.get()
        self.chk_exit_online.config(
            text=("☑" if on else "☐") + " 登录成功就不再检测（不再自动登录，适合只想开机登录一次的人）",
            fg=self.th["accent"] if on else self.th["fg"], bg=self.th["bg"])

    def _sync_notify(self):
        ok = self.var_notify_ok.get()
        fail = self.var_notify_fail.get()
        self.chk_notify_ok.config(
            text=("☑" if ok else "☐") + " 掉线后自动恢复时通知我（系统弹窗）",
            fg=self.th["accent"] if ok else self.th["fg"], bg=self.th["bg"])
        self.chk_notify_fail.config(
            text=("☑" if fail else "☐") + " 自动登录失败时通知我（系统弹窗）",
            fg=self.th["accent"] if fail else self.th["fg"], bg=self.th["bg"])

    def _toggle_flag(self, var, sync_fn):
        var.set(not var.get())
        sync_fn()

    # ---------- 校区与服务联动

    def _sync_area_hint(self):
        """按所选校区更新服务选择：金坛用实测静态配置，其他校区现场抓取。"""
        area = self.cmb_area.get()
        if area.startswith("金坛"):
            self.cmb_service["values"] = SERVICE_CHOICES
            if self.cmb_service.get() not in SERVICE_CHOICES:
                self.cmb_service.set("校园网")
            self.lbl_service_hint.config(text="金坛校区服务已实测，可直接选择",
                                         foreground=self.th["muted"])
        else:
            self.lbl_service_hint.config(text="正在抓取本校区服务列表...",
                                         foreground=self.th["muted"])
            self._bg(lambda: hl.fetch_services(), self._on_services_fetched, busy=False)

    def _on_campus_area(self):
        self._sync_area_hint()

    def _on_services_fetched(self, services):
        if not isinstance(services, list) or not services:
            self.lbl_service_hint.config(
                text="✗ 抓取不到服务列表：请确认已连接对应校区校园网后重试",
                foreground=self.th["err"])
            return
        displays = [d for _v, d, _i in services]
        self.cmb_service["values"] = displays
        self.cmb_service.set(displays[0])
        self.lbl_service_hint.config(text=f"✓ 已获取本校区 {len(displays)} 项服务，请选择",
                                     foreground=self.th["ok"])

    def _fetch_and_pick_service(self, real):
        """一键抓取（非金坛校区）：拉服务列表并选中当前会话对应的服务。"""
        def done(services):
            if isinstance(services, list) and services:
                displays = [d for _v, d, _i in services]
                self.cmb_service["values"] = displays
                match = next((d for v, d, _i in services if real in (v, d)), displays[0])
                self.cmb_service.set(match)
                self.lbl_service_hint.config(text=f"✓ 已获取本校区 {len(displays)} 项服务",
                                             foreground=self.th["ok"])
            else:
                kw = hl.detect_carrier_keyword(real)
                if kw in SERVICE_CHOICES:
                    self.cmb_service.set(kw)
                self.lbl_service_hint.config(
                    text="抓取服务列表失败，已按关键词「" + kw + "」选择", foreground=self.th["err"])
        return done

    def _toggle_auto(self):
        self.var_autostart.set(not self.var_autostart.get())
        self._sync_auto()
        self.on_toggle_autostart()

    # ---------- 后台线程骨架

    def _bg(self, work, done, busy=True):
        """work 在线程里跑，done 在主线程收结果；busy=False 不锁按钮（用于轮询）。"""
        def worker():
            try:
                res = work()
            except Exception as e:
                res = ("error", repr(e))
            self.after(0, lambda: done(res))
            if busy:
                self.after(0, lambda: self._set_busy(False))
        if busy:
            self._set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for b in (self.btn_fetch, self.btn_save, self.btn_check, self.btn_logout):
            b.config(state=state)

    # ---------- 实时日志（轻量轮询，不锁按钮）

    def _tick_log(self):
        def work():
            try:
                with open(hl.DEBUG_LOG, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 8192))
                    return f.read().decode("utf-8", errors="replace").splitlines()[-40:]
            except Exception:
                return []
        def done(lines):
            if not isinstance(lines, list):
                return
            text = "\n".join(lines)
            if text != self._last_log_text:
                self._last_log_text = text
                self.txt_log.config(state="normal")
                self.txt_log.delete("1.0", "end")
                self.txt_log.insert("end", text or "（暂无日志，守护运行后这里会实时滚动）")
                self.txt_log.see("end")
                self.txt_log.config(state="disabled")
        self._bg(work, done, busy=False)
        self.after(2000, self._tick_log)

    # ---------- 状态刷新

    def _refresh_status(self):
        def work():
            return hl.online(), hl.current_wifi_ssid(), task_exists(), hl.AUTH_FLAG.exists()
        def done(res):
            if isinstance(res, tuple) and len(res) == 4 and self.winfo_exists():
                is_online, ssid, has_task, flagged = res
                color = self.th["ok"] if is_online else self.th["err"]
                state = "在线" if is_online else "离线（守护将自动登录）"
                guard = "运行中" if has_task else "未注册自启"
                text = f"状态: ● {state}    WiFi: {ssid or '无'}    后台守护: {guard}"
                self.lbl_status.config(text=text, foreground=color)
                if flagged:
                    self.lbl_flag.config(text="⚠ 断路保护已触发：登录时密码错误，已停止自动尝试登录。"
                                              "改好密码后点「保存配置」会自动重置，也可点下方「重置断路保护」。",
                                         foreground=self.th["err"])
                    self.btn_clear.config(state="normal")
                else:
                    self.lbl_flag.config(text="")
                    self.btn_clear.config(state="disabled")
            if self.winfo_exists():
                self.after(15000, self._refresh_status)
        self._bg(work, done, busy=False)

    def _refresh_autostart(self):
        """让勾选标记反映计划任务真实状态（启动时与自启操作后调用）。"""
        def work():
            return task_exists()
        def done(res):
            if isinstance(res, bool):
                self.var_autostart.set(res)
                self._sync_auto()
        self._bg(work, done, busy=False)

    # ---------- 动作

    def on_fetch(self):
        self.lbl_real.config(text="正在抓取（需已登录校园网）...", foreground=self.th["muted"])

        def work():
            if not hl.portal_reachable():
                return ("no_campus", None)
            info = hl.get_online_info(hl.fetch_user_index())
            return ("ok", info)
        def done(res):
            kind, info = res if isinstance(res, tuple) else ("error", None)
            if kind == "error":
                self.lbl_real.config(text="抓取失败，请确认在校园网内并已登录", foreground=self.th["err"])
                return
            if kind == "no_campus":
                self.lbl_real.config(text="不在校园网（如手机热点）无法抓取，请手填账号和服务",
                                     foreground=self.th["err"])
                return
            username = str(info.get("userId", "")).strip()
            real = str(info.get("realServiceName") or info.get("service") or "").strip()
            if not username:
                self.lbl_real.config(text="未抓到会话信息：请先在浏览器登录校园网再试",
                                     foreground=self.th["err"])
                return
            kw = hl.detect_carrier_keyword(real)
            self.ent_user.delete(0, "end")
            self.ent_user.insert(0, username)
            if self.cmb_area.get().startswith("金坛"):
                if kw in SERVICE_CHOICES:
                    self.cmb_service.set(kw)
            else:
                self._bg(lambda: hl.fetch_services(), self._fetch_and_pick_service(real), busy=False)
            self.lbl_real.config(
                text=f"✓ 抓取成功: {username} · 当前服务「{real}」→ 已选关键词「{kw}」",
                foreground=self.th["ok"])
        self._bg(work, done)

    def on_save(self):
        username = self.ent_user.get().strip()
        password = self.ent_pass.get().strip()
        service_sel = self.cmb_service.get().strip() or "校园网"
        service = service_sel if service_sel in SERVICE_CHOICES else (hl.detect_carrier_keyword(service_sel) or "校园网")
        try:
            interval = int(self.spn_interval.get())
        except ValueError:
            interval = 1
        if not username or not password:
            messagebox.showwarning("缺少信息", "学号和密码都需要填写")
            return
        campus = self.cmb_campus.get()
        ssid = CAMPUS_CHOICES.get(campus, "") if campus else ""
        try:
            exit_min = int(self.spn_exit_min.get())
        except ValueError:
            exit_min = 0

        def work():
            save_all(username, password, service, interval, ssid,
                     auto_exit_after_login=self.var_exit_online.get(),
                     auto_exit_minutes=exit_min,
                     campus_area=CAMPUS_AREA.get(self.cmb_area.get(), ("jintan",))[0],
                     notify_on_success=self.var_notify_ok.get(),
                     notify_on_failure=self.var_notify_fail.get())
            sync_runtime_config(read_config())
            if self.var_autostart.get():
                return install_task(interval)
            return (True, "")
        def done(res):
            if isinstance(res, tuple) and res and res[0] is False:
                messagebox.showerror("自启注册失败", res[1])
            hl.AUTH_FLAG.unlink(missing_ok=True)
            messagebox.showinfo("已保存",
                                f"配置已保存并生效\n学号: {username}\n服务: {service}\n检测间隔: {interval} 分钟")
            self._refresh_status()
            self._refresh_autostart()
        self._bg(work, done)

    def on_toggle_autostart(self):
        if self.var_autostart.get():
            try:
                interval = int(self.spn_interval.get())
            except ValueError:
                interval = 1
            self._bg(lambda: install_task(interval), lambda res: self._refresh_autostart())
        else:
            self._bg(lambda: (uninstall_task(), "已取消开机自启（守护不再后台运行）"),
                     lambda res: self._refresh_autostart())

    def on_check(self):
        def work():
            return hl.run_once()
        def done(res):
            if not isinstance(res, int):
                messagebox.showerror("内部错误", f"检测过程出现异常：{res!r}")
                self._refresh_status()
                return
            messagebox.showinfo("检测结果", EXIT_CODE_MSG.get(res, f"退出码 {res}"))
            self._refresh_status()
        self._bg(work, done)

    def on_logout(self):
        if not messagebox.askyesno("退出确认",
                                   "确定退出当前校园网账号吗？\n（会先断网，守护会在一分钟内自动重新登录）"):
            return
        self._bg(lambda: hl.do_logout(), lambda res: self._refresh_status())

    def on_open_log(self):
        hl.LOG_DIR.mkdir(exist_ok=True)
        try:
            subprocess.Popen(["explorer", str(hl.LOG_DIR)], creationflags=NO_WINDOW)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开日志文件夹：{e!r}\n路径: {hl.LOG_DIR}")

    def on_clear_flag(self):
        hl.AUTH_FLAG.unlink(missing_ok=True)
        self.lbl_flag.config(text="")
        self.btn_clear.config(state="disabled")
        messagebox.showinfo("已重置", "断路保护已重置，守护将恢复自动登录")


def _enable_dpi_awareness():
    """高 DPI 适配：必须在创建任何窗口之前声明，否则系统会位图拉伸整个界面（发虚）。

    依次尝试 Per-Monitor v2（Win10 1703+）→ 系统级感知（Win10 1607+）→ 老 API 兜底。
    """
    if sys.platform != "win32":
        return
    try:
        _user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


def main() -> int:
    import argparse
    _enable_dpi_awareness()   # 必须在 Tk() 创建之前
    parser = argparse.ArgumentParser(description="河海校园网自动登录 · 可视化控制台")
    parser.add_argument("--tray", action="store_true", help="启动时缩到系统托盘（不显示窗口）")
    args = parser.parse_args()
    app = App(start_hidden=args.tray)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
