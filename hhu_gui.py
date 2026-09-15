#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hhu_gui.py —— hhu-autologin 可视化控制台（tkinter，零第三方依赖）

用法:
    hhu_gui.py    # 双击 exe 或 python hhu_gui.py 打开控制台窗口

定位：后台守护由计划任务每分钟无窗口运行，本窗口是它的"驾驶舱"——
    账号配置：一键抓取账号与服务（也可手填），保存即生效
    守护设置：开机自启开关、检测间隔、立即检测登录状态、退出账号、实时日志
    主题：默认（浅色）/ 方舟行动（深色工业风，遵循 ak-ui 设计契约）
所有网络/子进程操作都在后台线程执行，界面不卡顿。
"""
from __future__ import annotations

import configparser
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import hhu_login as hl

TASK_NAME = "HHU-AutoLogin"
REPO_URL = "https://github.com/sqxart/hhu-autologin"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SERVICE_CHOICES = ["校园网", "移动", "电信", "联通"]
# 校区 -> WiFi 门卫 SSID。服务提交值由服务器按校区自动下发，无需选校区；
# 这里只影响"不在校园 WiFi 时跳过守护"的检查。江宁/西康路确切 SSID 待同学反馈，
# 先用宽松值"Hohai"（子串匹配所有 Hohai 开头的 SSID）。
CAMPUS_CHOICES = {
    "金坛校区（Hohai University）": "Hohai University",
    "江宁校区（宽松匹配）": "Hohai",
    "西康路校区（宽松匹配）": "Hohai",
    "不检查 WiFi（网线/通用）": "",
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
    "方舟行动": dict(
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
    }


def sync_runtime_config(cfg: dict) -> None:
    """把配置同步进 hhu_login 的运行时全局 CONFIG。

    CLI 靠 load_config() 填充（空账号会 sys.exit，GUI 不能用）；
    run_once/ssid_gate/do_login 都依赖它，不填会 KeyError。
    """
    hl.CONFIG.update({
        "username": cfg["username"], "password": cfg["password"], "service": cfg["service"],
        "interval": cfg["interval"], "wifi_ssid": cfg["wifi_ssid"], "debug": True,
        "notify_enabled": True, "notify_on_success": True, "notify_on_failure": True,
        "notify_cooldown": 30,
    })


def save_all(username: str, password: str, service: str, interval: int, wifi_ssid: str) -> None:
    hl.save_account(username, password, service)
    cp = configparser.ConfigParser()
    if hl.CONFIG_FILE.exists():
        cp.read(hl.CONFIG_FILE, encoding="utf-8")
    if not cp.has_section("guard"):
        cp.add_section("guard")
    cp.set("guard", "interval_minutes", str(max(1, int(interval))))
    cp.set("guard", "wifi_ssid", wifi_ssid.strip())
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
    def __init__(self):
        super().__init__()
        self.title(f"河海校园网自动登录 · 控制台 v{hl.__version__}")
        self.geometry("580x740")
        self.resizable(False, False)
        self.th = THEMES["默认"]
        self._last_log_text = ""
        self._build_style()
        self._build()
        self.apply_theme("默认")
        self._refresh_status()
        self._refresh_autostart()
        self._tick_log()

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
        self.lbl_link.config(fg=t["link"])
        self._refresh_status()  # 动态状态色按新主题立即重刷

    # ---------- 布局

    def _build(self):
        pad = {"padx": 10, "pady": 6}

        top = ttk.LabelFrame(self, text="当前状态")
        top.pack(fill="x", padx=10, pady=(8, 6))
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
        self.lbl_flag = ttk.Label(top, text="", wraplength=520, justify="left")
        self.lbl_flag.pack(anchor="w", padx=10, pady=(0, 4))

        acct = ttk.LabelFrame(self, text="▍账号配置")
        acct.pack(fill="x", padx=10, pady=6)
        ttk.Label(acct, text="学号:").grid(row=0, column=0, sticky="e", padx=8, pady=4)
        self.ent_user = ttk.Entry(acct, width=30)
        self.ent_user.grid(row=0, column=1, columnspan=2, sticky="w", pady=4)

        ttk.Label(acct, text="密码:").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.ent_pass = ttk.Entry(acct, width=30, show="•")
        self.ent_pass.grid(row=1, column=1, sticky="w", pady=4)
        self.var_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(acct, text="显示密码", variable=self.var_show,
                        command=lambda: self.ent_pass.config(show="" if self.var_show.get() else "•")
                        ).grid(row=1, column=2, sticky="w", padx=6)

        ttk.Label(acct, text="服务:").grid(row=2, column=0, sticky="e", padx=8, pady=4)
        self.cmb_service = ttk.Combobox(acct, values=SERVICE_CHOICES, width=12, state="readonly")
        self.cmb_service.current(0)
        self.cmb_service.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(acct, text="（服务按校区自动适配，无需选校区）", style="TMuted.TLabel"
                  ).grid(row=2, column=2, sticky="w")

        self.lbl_real = ttk.Label(acct, text="", wraplength=500, justify="left")
        self.lbl_real.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 4))

        btns = ttk.Frame(acct)
        btns.grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=2)
        self.btn_fetch = ttk.Button(btns, text="一键抓取账号和服务", style="Accent.TButton",
                                    command=self.on_fetch)
        self.btn_fetch.pack(side="left", padx=(0, 8))
        self.btn_save = ttk.Button(btns, text="保存配置", command=self.on_save)
        self.btn_save.pack(side="left")

        guard = ttk.LabelFrame(self, text="▍守护设置")
        guard.pack(fill="x", padx=10, pady=6)
        self.var_autostart = tk.BooleanVar(value=False)
        self.ckb_auto = ttk.Checkbutton(guard, text="开机自启（后台每分钟守护，掉线自动恢复）",
                                        variable=self.var_autostart, command=self.on_toggle_autostart)
        self.ckb_auto.pack(anchor="w", padx=10, pady=4)

        row2 = ttk.Frame(guard)
        row2.pack(anchor="w", padx=10, pady=2)
        ttk.Label(row2, text="检测间隔:").pack(side="left")
        self.spn_interval = ttk.Spinbox(row2, from_=1, to=60, width=4)
        self.spn_interval.pack(side="left", padx=4)
        ttk.Label(row2, text="分钟（保存配置后生效）", style="TMuted.TLabel").pack(side="left")

        ttk.Label(guard, text="WiFi 门卫（不在校园 WiFi 时守护自动跳过）:").pack(anchor="w", padx=10)
        self.cmb_campus = ttk.Combobox(guard, values=list(CAMPUS_CHOICES), width=28, state="readonly")
        self.cmb_campus.pack(anchor="w", padx=10, pady=2)

        row3 = ttk.Frame(guard)
        row3.pack(anchor="w", padx=10, pady=6)
        self.btn_check = ttk.Button(row3, text="立即检测登录状态", style="Accent.TButton",
                                    command=self.on_check)
        self.btn_check.pack(side="left", padx=(0, 8))
        self.btn_logout = ttk.Button(row3, text="退出当前校园网账号", command=self.on_logout)
        self.btn_logout.pack(side="left", padx=(0, 8))
        self.btn_log = ttk.Button(row3, text="打开日志文件夹", command=self.on_open_log)
        self.btn_log.pack(side="left")

        logf = ttk.LabelFrame(self, text="▍实时日志（每分钟心跳 / 掉线与登录记录）")
        logf.pack(fill="x", padx=10, pady=6)
        self.txt_log = tk.Text(logf, height=8, width=68, state="disabled", wrap="none",
                               font=("Consolas", 9), relief="flat",
                               highlightthickness=1)
        self.txt_log.pack(fill="x", padx=8, pady=6)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 8))
        self.btn_clear = ttk.Button(bottom, text="清除断路器", command=self.on_clear_flag)
        self.btn_clear.pack(side="left")
        self.btn_clear.config(state="disabled")
        self.lbl_link = tk.Label(
            bottom, text="GitHub: sqxart/hhu-autologin · 觉得好用来个 Star ⭐",
            cursor="hand2")
        self.lbl_link.pack(side="right")
        self.lbl_link.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))

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
        ssid = cfg["wifi_ssid"]
        for label, value in CAMPUS_CHOICES.items():
            if (ssid and ssid.lower() in value.lower()) or value.lower() == ssid.lower():
                self.cmb_campus.set(label)
                break

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
                    self.lbl_flag.config(text="⚠ 断路器已置位：上次认证被服务器拒绝，登录已暂停。"
                                              "改好密码后点下方「清除断路器」。",
                                         foreground=self.th["err"])
                    self.btn_clear.config(state="normal")
                else:
                    self.lbl_flag.config(text="")
                    self.btn_clear.config(state="disabled")
            if self.winfo_exists():
                self.after(15000, self._refresh_status)
        self._bg(work, done, busy=False)

    def _refresh_autostart(self):
        """让勾选框反映计划任务真实状态（启动时与自启操作后调用）。"""
        def work():
            return task_exists()
        def done(res):
            if isinstance(res, bool):
                self.var_autostart.set(res)
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
            if kw in SERVICE_CHOICES:
                self.cmb_service.set(kw)
            self.lbl_real.config(
                text=f"✓ 抓取成功: {username} · 当前服务「{real}」→ 已选关键词「{kw}」",
                foreground=self.th["ok"])
        self._bg(work, done)

    def on_save(self):
        username = self.ent_user.get().strip()
        password = self.ent_pass.get().strip()
        service = self.cmb_service.get().strip() or "校园网"
        try:
            interval = int(self.spn_interval.get())
        except ValueError:
            interval = 1
        if not username or not password:
            messagebox.showwarning("缺少信息", "学号和密码都需要填写")
            return
        campus = self.cmb_campus.get()
        ssid = CAMPUS_CHOICES.get(campus, "") if campus else ""

        def work():
            save_all(username, password, service, interval, ssid)
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
        messagebox.showinfo("已清除", "断路器已清除，守护将恢复自动登录")


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
