#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hhu_gui.py —— hhu-autologin 可视化控制台（tkinter，零第三方依赖）

用法:
    hhu_gui.py    # 双击 exe 或 python hhu_gui.py 打开控制台窗口

定位：后台守护由计划任务每分钟无窗口运行，本窗口是它的"驾驶舱"——
    账号配置：一键抓取账号与服务（也可手填），保存即生效
    守护设置：开机自启开关、检测间隔、立即检测、注销会话、断路器与日志
所有网络/子进程操作都在后台线程执行，界面不卡顿。
"""
from __future__ import annotations

import configparser
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import hhu_login as hl

TASK_NAME = "HHU-AutoLogin"
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


# ---------------------------------------------------------------- 界面

EXIT_CODE_MSG = {
    0: "检测完成：在线或已自动登录成功",
    1: "门户链路不可达（不在校园网？）",
    2: "登录失败：网络或服务器异常，将继续重试",
    3: "已跳过：断路器置位或当前 WiFi 不在允许列表",
    4: "认证被拒：账号或密码错误（已停止重试）",
    5: "配置未填写：请先在「账号配置」里保存账号密码",
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"河海校园网自动登录 · 控制台 v{hl.__version__}")
        self.geometry("560x540")
        self.resizable(False, False)
        self._build()
        self._refresh_status()
        self._refresh_autostart()

    # ---------- 布局

    def _build(self):
        pad = {"padx": 10, "pady": 6}

        top = ttk.LabelFrame(self, text="当前状态")
        top.pack(fill="x", **pad)
        self.lbl_status = ttk.Label(top, text="状态: 检测中...")
        self.lbl_status.pack(anchor="w", padx=10, pady=4)

        acct = ttk.LabelFrame(self, text="账号配置")
        acct.pack(fill="x", **pad)
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
        ttk.Label(acct, text="（服务按校区自动适配，无需选校区）").grid(row=2, column=2, sticky="w")

        self.lbl_real = ttk.Label(acct, text="", foreground="#0a7d32", wraplength=500, justify="left")
        self.lbl_real.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 4))

        self.btn_fetch = ttk.Button(acct, text="一键抓取账号和服务", command=self.on_fetch)
        self.btn_fetch.grid(row=4, column=1, sticky="w", pady=2)
        self.btn_save = ttk.Button(acct, text="保存配置", command=self.on_save)
        self.btn_save.grid(row=4, column=2, sticky="w", padx=6, pady=2)

        guard = ttk.LabelFrame(self, text="守护设置")
        guard.pack(fill="x", **pad)
        self.var_autostart = tk.BooleanVar(value=False)
        self.ckb_auto = ttk.Checkbutton(guard, text="开机自启（后台每分钟守护，掉线自动恢复）",
                                        variable=self.var_autostart, command=self.on_toggle_autostart)
        self.ckb_auto.pack(anchor="w", padx=10, pady=4)

        row = ttk.Frame(guard)
        row.pack(anchor="w", padx=10, pady=2)
        ttk.Label(row, text="检测间隔:").pack(side="left")
        self.spn_interval = ttk.Spinbox(row, from_=1, to=60, width=4)
        self.spn_interval.pack(side="left", padx=4)
        ttk.Label(row, text="分钟（保存配置后生效）").pack(side="left")

        ttk.Label(guard, text="WiFi 门卫（不在校园 WiFi 时守护自动跳过）:").pack(anchor="w", padx=10)
        self.cmb_campus = ttk.Combobox(guard, values=list(CAMPUS_CHOICES), width=28, state="readonly")
        self.cmb_campus.pack(anchor="w", padx=10, pady=2)

        row2 = ttk.Frame(guard)
        row2.pack(anchor="w", padx=10, pady=6)
        self.btn_check = ttk.Button(row2, text="立即检测一次", command=self.on_check)
        self.btn_check.pack(side="left", padx=(0, 6))
        self.btn_logout = ttk.Button(row2, text="注销当前会话", command=self.on_logout)
        self.btn_logout.pack(side="left", padx=(0, 6))
        self.btn_log = ttk.Button(row2, text="打开日志文件夹", command=self.on_open_log)
        self.btn_log.pack(side="left")

        self.lbl_flag = ttk.Label(self, text="", foreground="#b3261e")
        self.lbl_flag.pack(anchor="w", **pad)
        self.btn_clear = ttk.Button(self, text="清除断路器", command=self.on_clear_flag)
        self.btn_clear.pack(anchor="w", padx=10)

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

    def _bg(self, work, done):
        """work 在线程里跑，done 在主线程收结果（None 表示无需 UI 反馈）。"""
        def worker():
            try:
                res = work()
            except Exception as e:
                res = ("error", repr(e))
            self.after(0, lambda: done(res))
            self.after(0, lambda: self._set_busy(False))
        self._set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for b in (self.btn_fetch, self.btn_save, self.btn_check, self.btn_logout):
            b.config(state=state)

    # ---------- 状态刷新

    def _refresh_status(self):
        def work():
            return hl.online(), hl.current_wifi_ssid(), task_exists(), hl.AUTH_FLAG.exists()
        def done(res):
            if isinstance(res, tuple) and len(res) == 4:
                is_online, ssid, has_task, flagged = res
                color = "#0a7d32" if is_online else "#b3261e"
                state = "在线" if is_online else "离线（守护将自动登录）"
                guard = "运行中" if has_task else "未注册自启"
                text = f"状态: ● {state}    WiFi: {ssid or '无'}    后台守护: {guard}"
                self.lbl_status.config(text=text, foreground=color)
                if flagged:
                    self.lbl_flag.config(text="⚠ 断路器已置位：上次认证被服务器拒绝，登录已暂停。"
                                              "改好密码后点下方「清除断路器」。")
                    self.btn_clear.config(state="normal")
                else:
                    self.lbl_flag.config(text="")
                    self.btn_clear.config(state="disabled")
            self.after(15000, self._refresh_status)
        self._bg(work, done)

    def _refresh_autostart(self):
        def work():
            return task_exists()
        def done(res):
            if isinstance(res, bool):
                prev = self.var_autostart.get()
                self.var_autostart.set(res)
                if prev != res:  # 用户没动过的外部状态变化不回写
                    pass
        self._bg(work, done)

    # ---------- 动作

    def on_fetch(self):
        self.lbl_real.config(text="正在抓取（需已登录校园网）...", foreground="#555555")

        def work():
            if not hl.portal_reachable():
                return ("no_campus", None)
            info = hl.get_online_info(hl.fetch_user_index())
            return ("ok", info)
        def done(res):
            kind, info = res if isinstance(res, tuple) else ("error", None)
            if kind == "error":
                self.lbl_real.config(text="抓取失败，请确认在校园网内并已登录", foreground="#b3261e")
                return
            if kind == "no_campus":
                self.lbl_real.config(text="不在校园网（如手机热点）无法抓取，请手填账号和服务",
                                     foreground="#b3261e")
                return
            username = str(info.get("userId", "")).strip()
            real = str(info.get("realServiceName") or info.get("service") or "").strip()
            if not username:
                self.lbl_real.config(text="未抓到会话信息：请先在浏览器登录校园网再试", foreground="#b3261e")
                return
            kw = hl.detect_carrier_keyword(real)
            self.ent_user.delete(0, "end")
            self.ent_user.insert(0, username)
            if kw in SERVICE_CHOICES:
                self.cmb_service.set(kw)
            self.lbl_real.config(text=f"✓ 抓取成功: {username} · 当前服务「{real}」→ 已选关键词「{kw}」",
                                 foreground="#0a7d32")
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
        self._bg(work, done)

    def on_toggle_autostart(self):
        if self.var_autostart.get():
            try:
                interval = int(self.spn_interval.get())
            except ValueError:
                interval = 1
            self._bg(lambda: install_task(interval),
                     lambda res: messagebox.showinfo("开机自启", res[1] if isinstance(res, tuple) else str(res)))
        else:
            self._bg(lambda: (uninstall_task(), "已取消开机自启（守护不再后台运行）"),
                     lambda res: None)

    def on_check(self):
        def work():
            return hl.run_once()
        def done(res):
            if not isinstance(res, int):
                messagebox.showerror("内部错误", f"检测过程出现异常：{res!r}")
                self._refresh_status()
                return
            prefix = "√ " if res == 0 else "! "
            messagebox.showinfo("检测结果", prefix + EXIT_CODE_MSG.get(res, f"退出码 {res}"))
            self._refresh_status()
        self._bg(work, done)

    def on_logout(self):
        if not messagebox.askyesno("注销确认", "确定注销当前校园网会话吗？\n（守护会在一分钟内自动重新登录）"):
            return
        self._bg(lambda: hl.do_logout(), lambda res: self._refresh_status())

    def on_open_log(self):
        hl.LOG_DIR.mkdir(exist_ok=True)
        os.startfile(hl.LOG_DIR)  # noqa: S606 - Windows 资源管理器打开日志目录

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
