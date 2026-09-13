#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hhu-autologin —— 河海大学校园网自动登录守护（纯标准库，无第三方依赖）

用法:
    hhu_login.py                # 跑一次：在线则直接退出，掉线则自动登录（供计划任务每分钟调用）
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
import json
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

[advanced]
; 是否写调试日志 logs/hhu_login_debug.log
debug = true
"""

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
    }
    if not CONFIG["username"] or not CONFIG["password"]:
        say("[!] config.ini 里账号或密码为空，请填写后重新运行")
        sys.exit(5)


# ---------------------------------------------------------------- HTTP

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OP = urllib.request.build_opener(NoRedirect)


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
            return 0
        say("[x] 服务器返回成功但网络仍未恢复")
        dlog("FAIL: server success but still offline")
        return 2
    if s == 200 and result == "fail":
        vcode = str(j.get("validCodeUrl") or "")
        if vcode or "验证码" in message:
            say("[x] 认证被拒：密码连续错误已触发验证码。")
            say("    请确认 config.ini 密码正确；若已在浏览器里连错三次，")
            say("    先用浏览器登录一次（输入验证码），再运行 --clear-flag")
        else:
            say(f"[x] 认证被拒：{message or resp[:200]}")
        dlog(f"FAIL: auth rejected: {resp[:300]}")
        LOG_DIR.mkdir(exist_ok=True)
        AUTH_FLAG.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {resp[:500]}", encoding="utf-8")
        return 4
    say(f"[x] 登录请求异常 HTTP {s}: {resp[:200]}")
    dlog(f"FAIL: login request error {s}")
    return 2


def do_logout() -> int:
    if not online():
        say("[*] 当前本就不在线，无需注销")
        return 0
    say("[*] 正在从门户获取当前会话凭证 ...")
    url = EPORTAL_HOST + "/"
    user_index = ""
    for _ in range(4):
        s, h, b = http(url, timeout=8)
        if s is None:
            break
        loc = h.get("Location", "") or ""
        m = re.search(r"userIndex=([0-9a-fA-F]+)", loc)
        if m:
            user_index = m.group(1)
            break
        if s in (301, 302, 303, 307, 308) and loc:
            url = urllib.parse.urljoin(url, loc)
            continue
        m = re.search(r"userIndex=([0-9a-fA-F]+)", b.decode("gbk", errors="replace"))
        if m:
            user_index = m.group(1)
        break
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


# ---------------------------------------------------------------- 入口

def main() -> int:
    global QUIET
    parser = argparse.ArgumentParser(description="河海大学校园网自动登录守护")
    parser.add_argument("--loop", nargs="?", const=0, type=int, metavar="分钟",
                        help="内置循环模式（不传分钟则读配置 interval_minutes）")
    parser.add_argument("--check", action="store_true", help="自检诊断")
    parser.add_argument("--logout", action="store_true", help="注销当前校园网会话")
    parser.add_argument("--clear-flag", action="store_true", help="清除断路器标志")
    parser.add_argument("--quiet", action="store_true", help="静默模式（计划任务可加）")
    args = parser.parse_args()

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
