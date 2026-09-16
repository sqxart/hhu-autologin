#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hhu-autologin —— 河海大学校园网自动登录守护（纯标准库，无第三方依赖）

用法:
    hhu_login.py                # 跑一次：在线则直接退出，掉线则自动登录（供计划任务每分钟调用）
    hhu_login.py --setup        # 配置向导：自动抓取账号与服务，只需输入密码
    hhu_login.py --loop [分钟]  # 内置循环模式（默认间隔读 config.ini，Ctrl+C 退出）
    hhu_login.py --check        # 自检诊断：配置 / SSID / 在线状态 / 门户链路 / 服务列表 / 断路器
    hhu_login.py --logout       # 注销当前校园网会话
    hhu_login.py --clear-flag   # 清除断路器标志（修正密码后使用）

退出码:
    0 正常（已在线 / 登录成功 / 干净跳过）
    1 门户链路不可达
    2 登录后仍未上线 / 网络错误
    3 跳过（断路器置位 / SSID 不匹配）
    4 认证被服务器拒绝（账号密码错误 / 触发验证码 / 服务不存在）
    5 配置问题（config.ini 缺失或账号密码未填）
"""
from __future__ import annotations

import argparse
import configparser
import getpass
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- 基础路径

def _base_dir() -> Path:
    # PyInstaller 打包后 __file__ 指向临时解包目录，配置和日志必须跟随 exe
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

BASE = _base_dir()
CONFIG_FILE = BASE / "config.ini"
LOG_DIR = BASE / "logs"
DEBUG_LOG = LOG_DIR / "hhu_login_debug.log"
AUTH_FLAG = LOG_DIR / "auth_failed.flag"
NOTIFY_STATE = LOG_DIR / "notify_state.json"

CONFIG_TEMPLATE = """[account]
; 学号
username =
; 校园网密码（信息门户密码）。注意：明文保存在本文件，请勿外传本机此文件
password =
; 服务关键词：校园网 / 移动 / 电信 / 联通（自动匹配登录页选项，支持三校区）
service = 校园网

[guard]
; --loop 模式的检测间隔（分钟）。计划任务模式请在 install.bat 中调整
interval_minutes = 1
; 仅当连接到指定 WiFi 时才工作（逗号分隔，支持多个校区 SSID）。
; 留空 = 不检查（接网线的同学请留空）。
; 说明：检测不到 WiFi 名称（如网线直连）时会放行，不会误伤有线用户。
wifi_ssid = Hohai University
; 仅控制台（hhu_gui）读取：登录成功上线后自动退出控制台（适合只想开机登录一次的人）
auto_exit_after_login = false
; 仅控制台（hhu_gui）读取：控制台运行 N 分钟后自动退出（0=不启用）
auto_exit_minutes = 0

[advanced]
; 是否写调试日志 logs/hhu_login_debug.log
debug = true
"""

__version__ = "1.4.2"

EPORTAL_HOST = "http://eportal.hhu.edu.cn"
SEEDS = [
    "http://1.2.3.4/",
    "http://10.96.0.155/eportal/redirectortosuccess.jsp",
    EPORTAL_HOST + "/eportal/redirectortosuccess.jsp",
    EPORTAL_HOST + "/",
]
INTERFACE = EPORTAL_HOST + "/eportal/InterFace.do?method="

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

INDEX_URL_RE = re.compile(r"https?://[^\s'<>]*index\.jsp\?[^\s'<>]+")
SERVICE_RE = re.compile(r"selectService\('([^']*)'\s*,\s*'([^']*)'\s*,\s*'(\d+)'\)")
NAT_RE = re.compile(r'name="net_access_type"[^>]*value="([^"]*)"')

# 计划任务重定向输出时避免中文乱码
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- 输出

QUIET = False
_t0 = time.time()


def say(msg: str) -> None:
    if not QUIET:
        try:
            print(msg, flush=True)
        except Exception:
            pass


def dlog(msg: str) -> None:
    if not CONFIG.get("debug", True):
        return
    try:
        LOG_DIR.mkdir(exist_ok=True)
        if DEBUG_LOG.exists() and DEBUG_LOG.stat().st_size > 256 * 1024:
            DEBUG_LOG.write_bytes(DEBUG_LOG.read_bytes()[-32 * 1024:])
        with DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 配置

CONFIG: dict = {}
config = configparser.ConfigParser()


def load_config() -> None:
    global CONFIG
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        say("[!] 已生成配置文件 config.ini，请填写学号和密码后重新运行")
        say("    " + str(CONFIG_FILE))
        sys.exit(5)
    config.read(CONFIG_FILE, encoding="utf-8")
    CONFIG = {
        "username": config.get("account", "username", fallback="").strip(),
        "password": config.get("account", "password", fallback="").strip(),
        "service": config.get("account", "service", fallback="校园网").strip(),
        "interval": config.getint("guard", "interval_minutes", fallback=1),
        "wifi_ssid": config.get("guard", "wifi_ssid", fallback="").strip(),
        "debug": config.getboolean("advanced", "debug", fallback=True),
        "notify_enabled": config.getboolean("notify", "enabled", fallback=True),
        "notify_on_success": config.getboolean("notify", "on_success", fallback=True),
        "notify_on_failure": config.getboolean("notify", "on_failure", fallback=True),
        "notify_cooldown": config.getint("notify", "failure_cooldown_minutes", fallback=30),
    }
    if not CONFIG["username"] or not CONFIG["password"]:
        say("[!] config.ini 里账号或密码为空，请填写后重新运行")
        sys.exit(5)


# ---------------------------------------------------------------- HTTP

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 本工具的一切请求强制直连（ProxyHandler({}) = 禁用系统代理）。
# 实测（2026-09）：Clash 系统代理开启时，urllib 默认跟随系统代理，
# 内网门户请求被送进代理后全部 ConnectionRefused 10061，GUI 误报
# "不在校园网"、守护整条登录链路瘫痪；掉线探测也可能被代理"代答"
# 造成假在线。校园网认证守护必须反映本机真实网络状态，绝不走代理。
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def http(url: str, timeout: int = 6, data=None):
    req = urllib.request.Request(
        url, headers=UA, data=data.encode() if isinstance(data, str) else data
    )
    try:
        with OP.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, dict(e.headers), e.read()
        except Exception:
            return e.code, {}, b""
    except Exception as e:
        return None, {}, repr(e).encode()


def online() -> bool:
    s, _, _ = http("http://connect.rom.miui.com/generate_204", timeout=4)
    return s == 204


def ece(s) -> str:
    """等价于页面 JS 的 encodeURIComponent 执行两次（doauthen 对所有参数如此）。"""
    safe = "-_.!~*'()"
    return urllib.parse.quote(urllib.parse.quote(str(s), safe=safe), safe=safe)


# ---------------------------------------------------------------- 系统通知

_POWERSHELL_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t.GetElementsByTagName('text').Item(0).AppendChild($t.CreateTextNode($env:TOAST_TITLE)) | Out-Null
$t.GetElementsByTagName('text').Item(1).AppendChild($t.CreateTextNode($env:TOAST_MSG)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def notify_toast(title: str, message: str) -> None:
    """Win10 1607+ / Win11 原生 Toast。任何失败都静默——通知只是锦上添花。"""
    if sys.platform != "win32":
        return
    try:
        env = {**os.environ, "TOAST_TITLE": title[:64], "TOAST_MSG": message[:180]}
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_TOAST],
            capture_output=True, timeout=15, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        dlog(f"toast fail (ignored): {e!r}")


def failure_notify_allowed() -> bool:
    """失败通知冷却：cooldown 分钟内只弹一次（脚本每分钟重跑，状态必须落盘）。"""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        state = {}
        if NOTIFY_STATE.exists():
            state = json.loads(NOTIFY_STATE.read_text(encoding="utf-8"))
        last = float(state.get("last_fail_notify", 0))
        now = time.time()
        if now - last < CONFIG["notify_cooldown"] * 60:
            return False
        state["last_fail_notify"] = now
        NOTIFY_STATE.write_text(json.dumps(state), encoding="utf-8")
        return True
    except Exception:
        return True  # 状态文件坏了也别把通知功能整个废掉


def clear_failure_cooldown() -> None:
    try:
        if NOTIFY_STATE.exists():
            state = json.loads(NOTIFY_STATE.read_text(encoding="utf-8"))
            state.pop("last_fail_notify", None)
            NOTIFY_STATE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def notify_failure(message: str) -> None:
    if not (CONFIG["notify_enabled"] and CONFIG["notify_on_failure"]):
        return
    if failure_notify_allowed():
        notify_toast("校园网自动登录失败", message)


def notify_success() -> None:
    if CONFIG["notify_enabled"] and CONFIG["notify_on_success"]:
        notify_toast("校园网已自动登录", "检测到掉线并已自动恢复，网络正常")
    clear_failure_cooldown()  # 成功后清除失败冷却，下次失败可立即提醒


# ---------------------------------------------------------------- SSID 门卫

def current_wifi_ssid() -> str | None:
    """返回当前 WiFi 名称；获取不到（网线/非Windows）返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        # CREATE_NO_WINDOW：pythonw 无控制台父进程拉起 netsh 时，防止闪现黑窗口
        raw = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.decode("gbk", errors="replace")
    except Exception:
        return None
    m = re.search(r"^\s*SSID\s*:\s*(.+)$", raw, re.M)
    return m.group(1).strip() if m else None


def ssid_gate() -> bool:
    """返回 False 表示当前网络不该尝试登录（安静跳过）。"""
    allow = CONFIG["wifi_ssid"]
    if not allow:
        return True
    ssid = current_wifi_ssid()
    if ssid is None:
        return True  # 网线等场景，放行
    allowed = [x.strip() for x in allow.split(",") if x.strip()]
    ok = any(a.lower() in ssid.lower() or ssid.lower() in a.lower() for a in allowed)
    if not ok:
        say(f"[*] 当前 WiFi「{ssid}」不在允许列表，跳过")
    return ok


# ---------------------------------------------------------------- 登录链路

def find_login_page():
    """走门卫重定向链，返回 (带 queryString 的登录页 URL, 登录页 HTML 文本)。"""
    for seed in SEEDS:
        url = seed
        for _ in range(6):
            s, h, b = http(url, timeout=8)
            if s is None:
                dlog(f"seed unreachable: {seed} -> {b[:80]}")
                break
            loc = h.get("Location", "") or ""
            if s in (301, 302, 303, 307, 308) and loc:
                url = urllib.parse.urljoin(url, loc)
                continue
            if "index.jsp?" in url:
                text = b.decode("gbk", errors="replace")
                if not text:
                    _, _, b2 = http(url, timeout=8)
                    text = b2.decode("gbk", errors="replace")
                return url, text
            m = INDEX_URL_RE.search(b.decode("gbk", errors="replace"))
            if m:
                url = m.group(0)
                s2, _, b2 = http(url, timeout=8)
                if s2 == 200:
                    return url, b2.decode("gbk", errors="replace")
                break
            break
    return None, ""


def parse_services(html: str):
    """从登录页解析服务映射 [(内部值, 显示名, 序号)]。"""
    return [(v, d, i) for v, d, i in SERVICE_RE.findall(html)]


def fetch_services(query_string: str = ""):
    """登录页的服务列表由页面 JS 调 getServices 接口动态注入，原始 HTML 里没有。

    纯 HTTP 也能调通该接口：POST InterFace.do?method=getServices（body 为空），
    服务选项就藏在响应 serviceContent 字段的 selectService(...) HTML 里。
    接口对 queryString 很宽容：不带参数也返回全量服务列表。
    """
    url = INTERFACE + "getServices"
    if query_string:
        url += "&queryString=" + urllib.parse.quote(query_string, safe="")
    s, _, b = http(url, timeout=8, data="")
    if s != 200:
        dlog(f"getServices http {s}")
        return []
    try:
        j = json.loads(b.decode("utf-8", errors="replace"))
    except Exception:
        dlog("getServices: response not json")
        return []
    return parse_services(j.get("serviceContent", ""))


def pick_service(services, keyword: str):
    """按关键词匹配服务；返回内部值或 None。"""
    kw = keyword.strip().lower()
    for value, display, _idx in services:
        if kw and (kw in value.lower() or kw in display.lower()):
            return value
    return None


# ---------------------------------------------------------------- 动作

def do_login() -> int:
    url, html = find_login_page()
    if not url:
        say("[x] 门户链路不可达（不在校园网？门卫没劫持？）")
        dlog("FAIL: portal chain unreachable")
        return 1
    qs = url.split("?", 1)[1]

    services = parse_services(html)
    if not services:
        services = fetch_services(qs)  # 页面原始 HTML 无服务列表，走接口拉取
    service = pick_service(services, CONFIG["service"])
    if service is None:
        m = NAT_RE.search(html)
        if m:
            service = m.group(1)
            say(f"[!] 关键词「{CONFIG['service']}」未匹配到服务，已改用页面默认值: {service}")
        elif services:
            service = services[0][0]
            say(f"[!] 关键词「{CONFIG['service']}」未匹配到服务，已改用第一个选项: {service}")
        else:
            service = "校园外网服务(out-campus NET)"
            say(f"[!] 页面未解析到服务列表，使用内置默认: {service}")
    if services:
        say(f"[*] 服务列表: " + " / ".join(d for _v, d, _i in services))
    say(f"[*] 使用服务: {service}")

    body = (
        "userId=" + ece(CONFIG["username"])
        + "&password=" + ece(CONFIG["password"])
        + "&service=" + ece(service)
        + "&queryString=" + ece(qs)
        + "&operatorPwd=&operatorUserId=&validcode=&passwordEncrypt=" + ece("false")
    )
    s, _, b = http(INTERFACE + "login", timeout=10, data=body)
    resp = b.decode("utf-8", errors="replace")
    dlog(f"login http {s} resp: {resp[:400]}")

    try:
        j = json.loads(resp)
    except Exception:
        j = {}
    result = str(j.get("result", "")).lower()
    message = str(j.get("message", ""))

    if s == 200 and result == "success":
        time.sleep(3)
        if online():
            say("[√] 登录成功，网络已恢复")
            dlog("SUCCESS: verified online")
            notify_success()
            return 0
        say("[x] 服务器返回成功但网络仍未恢复")
        dlog("FAIL: server success but still offline")
        notify_failure("登录接口返回成功但网络未恢复，将继续每分钟重试")
        return 2
    if s == 200 and result == "fail":
        vcode = str(j.get("validCodeUrl") or "")
        if vcode or "验证码" in message:
            say("[x] 认证被拒：密码连续错误已触发验证码。")
            say("    请确认 config.ini 密码正确；若已在浏览器里连错三次，")
            say("    先用浏览器登录一次（输入验证码），再运行 --clear-flag")
            msg = "密码连续错误已触发验证码，已停止重试。请修正 config.ini 密码，先用浏览器登录一次，再运行 --clear-flag"
        else:
            say(f"[x] 认证被拒：{message or resp[:200]}")
            msg = f"账号或密码被服务器拒绝（{message or '未知原因'}），已停止重试。修正 config.ini 后运行 --clear-flag"
        dlog(f"FAIL: auth rejected: {resp[:300]}")
        LOG_DIR.mkdir(exist_ok=True)
        AUTH_FLAG.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {resp[:500]}", encoding="utf-8")
        # 断路器一次性通知：之后每分钟都会在标志处直接退出，不会重复弹
        if CONFIG["notify_enabled"]:
            notify_toast("校园网自动登录失败", msg)
        return 4
    say(f"[x] 登录请求异常 HTTP {s}: {resp[:200]}")
    dlog(f"FAIL: login request error {s}")
    notify_failure(f"登录请求异常（HTTP {s}），将持续每分钟重试")
    return 2


def fetch_user_index() -> str:
    """在线时访问门户主页，从重定向链解析当前会话凭证 userIndex（注销/查会话共用）。"""
    url = EPORTAL_HOST + "/"
    for _ in range(4):
        s, h, b = http(url, timeout=8)
        if s is None:
            break
        loc = h.get("Location", "") or ""
        m = re.search(r"userIndex=([0-9a-fA-F]+)", loc)
        if m:
            return m.group(1)
        if s in (301, 302, 303, 307, 308) and loc:
            url = urllib.parse.urljoin(url, loc)
            continue
        m = re.search(r"userIndex=([0-9a-fA-F]+)", b.decode("gbk", errors="replace"))
        if m:
            return m.group(1)
        break
    return ""


def get_online_info(user_index: str) -> dict:
    """查询在线会话信息：userId / realServiceName（当前服务）等。"""
    if not user_index:
        return {}
    s, _, b = http(INTERFACE + "getOnlineUserInfo", timeout=8, data="userIndex=" + user_index)
    try:
        j = json.loads(b.decode("utf-8", errors="replace"))
        return j if isinstance(j, dict) else {}
    except Exception:
        dlog(f"getOnlineUserInfo http {s}: bad json")
        return {}


def do_logout() -> int:
    if not online():
        say("[*] 当前本就不在线，无需注销")
        return 0
    say("[*] 正在从门户获取当前会话凭证 ...")
    user_index = fetch_user_index()
    if not user_index:
        say("[x] 未获取到会话凭证（userIndex），无法注销")
        dlog("logout FAIL: no userIndex")
        return 1
    dlog(f"logout: userIndex len={len(user_index)}")
    s, _, b = http(INTERFACE + "logout", timeout=10, data="userIndex=" + user_index)
    resp = b.decode("utf-8", errors="replace")
    dlog(f"logout http {s} resp: {resp[:200]}")
    say(f"[*] 服务器响应: {resp[:150]}")
    time.sleep(2)
    if online():
        say("[!] 仍在线——注销可能未生效")
        return 2
    say("[√] 已注销下线")
    return 0


def run_once() -> int:
    if not ssid_gate():
        dlog("skip: SSID 不在允许列表")
        return 0
    if online():
        dlog("online, skip")  # 心跳：日志里最近一行的时间即最近一次守护
        say("[*] 在线，无需登录")
        return 0
    if AUTH_FLAG.exists():
        say("[!] 断路器置位：上次认证被服务器拒绝，已停止重试。")
        say("    修正 config.ini 账号密码后运行 --clear-flag 解除")
        try:
            say("    上次拒绝原因: " + AUTH_FLAG.read_text(encoding="utf-8")[:120])
        except Exception:
            pass
        return 3
    return do_login()


def run_check() -> int:
    say("== hhu-autologin 自检 ==")
    say(f"[1] 配置文件: {CONFIG_FILE} {'√' if CONFIG_FILE.exists() else '×'}")
    say(f"    账号: {CONFIG['username'][:3]}***{CONFIG['username'][-2:]}  服务关键词: {CONFIG['service']}")
    ssid = current_wifi_ssid()
    say(f"[2] 当前 WiFi: {ssid or '(获取不到，网线或非Windows)'}"
        + (f"  允许列表: {CONFIG['wifi_ssid']}" if CONFIG["wifi_ssid"] else "  未启用 SSID 检查"))
    say(f"[3] 在线状态: {'在线' if online() else '离线'}")
    say(f"[4] 断路器: {'置位(认证被拒过) ' + str(AUTH_FLAG) if AUTH_FLAG.exists() else '未置位'}")
    if online():
        say("[5] 门户链路: 在线状态下登录页不对已认证设备开放（属正常），跳过服务解析")
        say("[√] 自检完成：一切正常，守护脚本会在掉线时自动登录")
        return 0
    url, html = find_login_page()
    if not url:
        say("[5] 门户链路: × 不可达（确认已连校园网 WiFi 且未登录）")
        return 1
    say(f"[5] 门户链路: √ {url[:90]}...")
    services = parse_services(html)
    if not services:
        services = fetch_services(urllib.parse.urlsplit(url).query)
        if services:
            say("    （服务列表来自 getServices 接口）")
    if services:
        say("[6] 服务列表:")
        pick = pick_service(services, CONFIG["service"])
        for v, d, i in services:
            mark = "  <-- 将使用" if (pick and v == pick) else ""
            say(f"    [{i}] {d}  (提交值: {v}){mark}")
    else:
        say("[6] 服务列表: 页面未解析到（可能已登录，看到的是成功页）")
    say("[7] 离线状态下，最后真实尝试登录一次（同时验证凭据）")
    if AUTH_FLAG.exists():
        return 3
    return do_login()


# ---------------------------------------------------------------- 配置向导

def detect_carrier_keyword(service_name: str) -> str:
    """从服务名提取跨校区通用的运营商关键词。

    服务提交值带校区后缀（如"中国联通(常州)"），换校区就变了；
    但运营商归属（移动/电信/联通）不变，config 里存关键词即可自动适配。
    """
    name = service_name or ""
    for kw in ("移动", "电信", "联通"):
        if kw in name:
            return kw
    return "校园网"


def save_account(username: str, password: str, service: str) -> None:
    """把账号写入 config.ini 的 [account] 段，保留其余配置。"""
    cp = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cp.read(CONFIG_FILE, encoding="utf-8")
    if not cp.has_section("account"):
        cp.add_section("account")
    cp.set("account", "username", username)
    cp.set("account", "password", password)
    if service:
        cp.set("account", "service", service)
    # 新装用户：补齐守护默认配置（已有文件则不动用户改过的值）
    for section, key, val in (
        ("guard", "interval_minutes", "1"),
        ("guard", "wifi_ssid", "Hohai University"),
        ("advanced", "debug", "true"),
    ):
        if not cp.has_section(section):
            cp.add_section(section)
        if not cp.get(section, key, fallback="").strip():
            cp.set(section, key, val)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        f.write("; 可手动编辑本文件；换密码后重新运行: hhu_login.py --setup\n")
        cp.write(f)


def ask_password() -> str:
    """密码输入（不回显），输两遍防手滑；放弃返回空串。

    stdin 不是终端（管道/重定向，如自动化测试）时 getpass 在 Windows 会
    挂起——此时退回普通 input 明读。
    """
    hidden = sys.stdin.isatty()
    for _ in range(3):
        try:
            if hidden:
                p1 = getpass.getpass("请输入校园网密码（输入不回显）: ").strip()
            else:
                p1 = input("请输入校园网密码: ").strip()
            if not p1:
                say("[!] 密码不能为空，请重试")
                continue
            if hidden:
                p2 = getpass.getpass("再输一遍确认: ").strip()
            else:
                p2 = input("再输一遍确认: ").strip()
        except (EOFError, KeyboardInterrupt):
            say("")
            return ""
        if p1 == p2:
            return p1
        say("[!] 两次输入不一致，请重试")
    return ""


def portal_reachable() -> bool:
    """认证门户是否可达（区分校园网内外：手机热点/外网下 eportal 不可达）。"""
    s, _, _ = http(EPORTAL_HOST + "/", timeout=5)
    return s is not None


def run_setup() -> int:
    """配置向导：在线时自动抓取账号与服务，用户只需输入密码。

    可重复运行：换服务商时用新服务登录一次再跑向导，账号密码不变则自动沿用。
    """
    say("== hhu-autologin 配置向导 ==")
    say("    首次配置：登录校园网后运行，账号和服务自动抓取，只需输入密码")
    say("    换服务商：用新服务登录一次再运行向导，密码沿用、无需重输")

    # 读取已有配置（换服务商场景：账号密码可沿用）
    old = {}
    if CONFIG_FILE.exists():
        cp = configparser.ConfigParser()
        cp.read(CONFIG_FILE, encoding="utf-8")
        if cp.has_section("account"):
            old = {
                "username": cp.get("account", "username", fallback="").strip(),
                "password": cp.get("account", "password", fallback="").strip(),
                "service": cp.get("account", "service", fallback="").strip(),
            }
    if old.get("username"):
        say(f"[*] 已有配置: 账号 {old['username'][:3]}***{old['username'][-2:]}  服务: {old['service'] or '(未填)'}")

    on_campus = portal_reachable()
    is_online = online()
    if not on_campus:
        say("[!] 当前不在校园网环境（认证门户不可达，如手机热点/外网），")
        say("    无法自动抓取账号与服务列表，将转为手动填写。")
        say("    配置写好并回到校园网后自动生效；WiFi 门卫默认只认「Hohai University」，")
        say("    接网线的同学请把 config.ini 里 wifi_ssid 留空。")
    elif is_online:
        say("[*] 校园网内且已在线，正在抓取当前会话的账号与服务 ...")
    else:
        say("[*] 在校园网内但未认证。建议先在浏览器完成一次手动登录再运行向导，")
        say("    即可自动抓取；现在也可以手动填写。")

    username = service_kw = ""
    if on_campus and is_online:
        info = get_online_info(fetch_user_index())
        username = str(info.get("userId", "")).strip()
        real = str(info.get("realServiceName") or info.get("service") or "").strip()
        if username:
            service_kw = detect_carrier_keyword(real)
            say(f"[√] 抓取到账号: {username}")
            say(f"[√] 抓取到服务: {real}")
            say(f"    -> 按关键词「{service_kw}」保存（不带校区字样，换校区通用）")
            try:
                if input("回车确认，或输入 n 手动填写: ").strip().lower() == "n":
                    username = service_kw = ""
            except (EOFError, KeyboardInterrupt):
                say("")
                return 130
        else:
            say("[!] 已在线但未抓到会话信息（认证会话可能异常），请手动填写")

    if not username:
        try:
            tip = f"（回车沿用 {old['username'][:3]}***{old['username'][-2:]}）" if old.get("username") else ""
            username = input(f"请输入学号{tip}: ").strip() or old.get("username", "")
            if not username:
                say("[x] 学号不能为空，已退出（未写入任何配置）")
                return 5
            services = fetch_services() if on_campus else []
            if services:
                say("[*] 检测到本校区服务列表，输入序号选择:")
                for _v, d, i in services:
                    say(f"    [{i}] {d}")
                choice = input("服务序号 [回车=0 校园网]: ").strip() or "0"
                try:
                    service_kw = detect_carrier_keyword(services[int(choice)][1])
                except (ValueError, IndexError):
                    service_kw = "校园网"
                    say("[!] 序号无效，已使用默认「校园网」（之后可改 config.ini 的 service）")
            if not service_kw:
                service_kw = input("服务关键词（校园网/移动/电信/联通，回车=校园网）: ").strip() \
                    or old.get("service", "") or "校园网"
        except (EOFError, KeyboardInterrupt):
            say("")
            return 130

    # 密码：账号未变且已有保存的密码 -> 可沿用（换服务商场景不用重输）
    password = ""
    if old.get("password") and old.get("username") == username:
        say("[*] 账号未变，检测到已保存的密码")
        try:
            renew = input("回车沿用已保存密码，或输入 n 重新输入: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            say("")
            return 130
        if renew != "n":
            password = old["password"]
            say("[√] 密码沿用现有配置，未重新输入")
    if not password:
        password = ask_password()
        if not password:
            say("[x] 未获得有效密码，已退出（未写入任何配置）")
            return 5

    save_account(username, password, service_kw)
    if AUTH_FLAG.exists():  # 配置已更新，旧的认证拒绝原因不再适用
        AUTH_FLAG.unlink(missing_ok=True)
        say("[√] 已清除断路器标志（旧配置的认证拒绝不再适用）")
    say("")
    say(f"[√] 配置已写入 {CONFIG_FILE}")
    say(f"    账号: {username}   服务: {service_kw}   密码: 已保存（不回显）")
    say("[*] 守护会每分钟自动检测，掉线即用以上配置重登")
    say("    可运行 --check 自检；以后换密码或换服务商，重新运行 --setup 即可")
    dlog(f"setup: saved account {username[:3]}*** service={service_kw}")
    return 0


# ---------------------------------------------------------------- 入口

def main() -> int:
    global QUIET
    parser = argparse.ArgumentParser(description="河海大学校园网自动登录守护")
    parser.add_argument("--version", action="version", version=f"hhu-autologin v{__version__}")
    parser.add_argument("--loop", nargs="?", const=0, type=int, metavar="分钟",
                        help="内置循环模式（不传分钟则读配置 interval_minutes）")
    parser.add_argument("--check", action="store_true", help="自检诊断")
    parser.add_argument("--setup", action="store_true",
                        help="配置向导：自动抓取账号与服务，只需输入密码（重复运行可换服务商，密码沿用）")
    parser.add_argument("--logout", action="store_true", help="注销当前校园网会话")
    parser.add_argument("--clear-flag", action="store_true", help="清除断路器标志")
    parser.add_argument("--quiet", action="store_true", help="静默模式（计划任务可加）")
    args = parser.parse_args()

    if args.setup:  # 向导无需既有配置，放在 load_config 之前（首次运行 config.ini 还不存在）
        return run_setup()
    load_config()
    QUIET = args.quiet

    if args.clear_flag:
        AUTH_FLAG.unlink(missing_ok=True)
        say("[√] 断路器已清除")
        return 0
    if args.logout:
        return do_logout()
    if args.check:
        return run_check()
    if args.loop is not None:
        minutes = args.loop if args.loop > 0 else CONFIG["interval"]
        say(f"[*] 循环模式：每 {minutes} 分钟检测一次，Ctrl+C 退出")
        while True:
            try:
                run_once()
            except Exception as e:
                dlog(f"loop error: {e!r}")
            time.sleep(minutes * 60)
    return run_once()


if __name__ == "__main__":
    sys.exit(main())
