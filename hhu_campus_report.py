#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""河海大学校园网服务配置采集脚本（供江宁 / 西康路校区同学运行）

用途：在校园网环境下运行本脚本，自动采集本校区的服务配置并生成 txt 报告，
    帮助 hhu-autologin 项目验证跨校区适配。

使用方法（二选一）：
    1. 已装 Python：在校园网 WiFi 下双击或运行  python hhu_campus_report.py
    2. 按提示选择校区，等待几秒，报告 txt 会生成在本脚本旁边

隐私说明：
    * 不会读取、询问或上报你的密码
    * 报告中的学号已脱敏（只保留前 3 位和后 2 位）
    * 报告生成后你可以先打开自查，再决定是否发送

纯 Python 标准库，无任何第三方依赖，兼容 Python 3.8+。
也可以直接运行打包好的 hhu_campus_report.exe（无需安装 Python）。
"""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MIN_PY = (3, 8)
if sys.version_info < MIN_PY:
    # 版本太旧时只能尽量把话说明白（老版本 Python 可能连语法都过不了）
    print("=" * 56)
    print("  你的 Python 版本太旧：%d.%d" % sys.version_info[:2])
    print("  本脚本需要 Python 3.8 或更高版本。")
    print("  两个办法（任选其一）：")
    print("    1) 去 python.org 下载新版 Python，安装时勾选 Add to PATH")
    print("    2) 改用 hhu_campus_report.exe（免安装 Python，推荐）")
    print("=" * 56)
    try:
        input("\n按回车键退出...")
    except Exception:
        pass
    sys.exit(1)

for _stream in (sys.stdout, sys.stderr):
    try:
        # 真实控制台走的是 Windows 的 Unicode 接口，别去改它的编码（改了中文会花屏）
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def here() -> str:
    """本脚本（或本 exe）所在目录。

    打包成 exe 后 __file__ 指向临时解包目录，用完就被删——报告必须落在 exe 旁边。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def env_line() -> str:
    """运行环境一行摘要：写进报告，出问题时作者一看就知道你的环境。"""
    kind = "打包版 exe" if getattr(sys, "frozen", False) else "Python"
    ver = "%d.%d.%d" % sys.version_info[:3]
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    return "%s %s（%d 位）· %s" % (kind, ver, bits, sys.platform)


def say(msg):
    """安全输出：pythonw/输出流不可用时静默，绝不让打印本身搞崩脚本。"""
    try:
        print(msg, flush=True)
    except Exception:
        pass

# ---------------------------------------------------------------- 基础 HTTP

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}
EPORTAL_HOST = "http://eportal.hhu.edu.cn"
SEEDS = [
    "http://1.2.3.4/",
    "http://10.96.0.155/eportal/redirectortosuccess.jsp",
    EPORTAL_HOST + "/eportal/redirectortosuccess.jsp",
    EPORTAL_HOST + "/",
]
INTERFACE = EPORTAL_HOST + "/eportal/InterFace.do?method="
INDEX_URL_RE = re.compile(r"https?://[^\s'<>]*index\.jsp\?[^\s'<>]+")
SERVICE_RE = re.compile(r"selectService\('([^']*)'\s*,\s*'([^']*)'\s*,\s*'(\d+)'\)")
NAT_RE = re.compile(r'name="net_access_type"[^>]*value="([^"]*)"')

KEYWORDS = ["校园网", "移动", "电信", "联通"]
SCRIPT_VERSION = "1.1"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 强制直连（禁用系统代理）：与 hhu_login 同理——校园网探测走系统代理
# 会被 Clash 等代理弄挂（实测全部 ConnectionRefused），采集必须直连。
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def http(url, timeout=8, data=None):
    req = urllib.request.Request(url, headers=UA, data=data.encode() if isinstance(data, str) else data)
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


def online():
    s, _, _ = http("http://connect.rom.miui.com/generate_204", timeout=4)
    return s == 204


def current_wifi_ssid():
    """返回当前 WiFi 名称；获取不到（网线/非 Windows）返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        raw = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.decode("gbk", errors="replace")
    except Exception:
        return None
    m = re.search(r"^\s*SSID\s*:\s*(.+)$", raw, re.M)
    return m.group(1).strip() if m else None


def find_login_page():
    """走门卫劫持链，返回 (带 queryString 的登录页 URL, 登录页 HTML)。"""
    for seed in SEEDS:
        url = seed
        for _ in range(6):
            s, h, b = http(url, timeout=8)
            if s is None:
                break
            loc = h.get("Location", "") or ""
            if s in (301, 302, 303, 307, 308) and loc:
                url = urllib.parse.urljoin(url, loc)
                continue
            if "index.jsp?" in url:
                return url, b.decode("gbk", errors="replace")
            m = INDEX_URL_RE.search(b.decode("gbk", errors="replace"))
            if m:
                url = m.group(0)
                s2, _, b2 = http(url, timeout=8)
                if s2 == 200:
                    return url, b2.decode("gbk", errors="replace")
                break
            break
    return None, ""


def parse_services(html):
    return [(v, d, i) for v, d, i in SERVICE_RE.findall(html)]


def fetch_services(query_string=""):
    """调 getServices 接口拉取服务列表（登录页 JS 同款）。"""
    url = INTERFACE + "getServices"
    if query_string:
        url += "&queryString=" + urllib.parse.quote(query_string, safe="")
    s, _, b = http(url, timeout=8, data="")
    if s != 200:
        return []
    try:
        import json
        j = json.loads(b.decode("utf-8", errors="replace"))
    except Exception:
        return []
    return parse_services(j.get("serviceContent", ""))


def pick_services(services, keyword):
    """返回所有命中关键词的 (提交值, 显示名)；用于歧义检测。"""
    kw = keyword.strip().lower()
    if not kw:
        return []
    return [(v, d) for v, d, _i in services if kw in v.lower() or kw in d.lower()]


def fetch_user_index():
    """已登录时从门户重定向链解析会话凭证（只用于查询，不写入报告）。"""
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


def get_online_info(user_index):
    if not user_index:
        return {}
    s, _, b = http(INTERFACE + "getOnlineUserInfo", timeout=8, data="userIndex=" + user_index)
    try:
        import json
        j = json.loads(b.decode("utf-8", errors="replace"))
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def mask_user(uid):
    uid = str(uid or "")
    return (uid[:3] + "***" + uid[-2:]) if len(uid) > 5 else "(未获取)"


# ---------------------------------------------------------------- 采集主流程

def main():
    say("=" * 56)
    say("  河海大学校园网 · 服务配置采集（hhu-autologin 项目用）")
    say("=" * 56)
    say("说明：本脚本只读取网络信息与服务列表，不涉及密码。")
    say("技术信息: " + env_line())
    say("")

    choice = ""
    try:
        choice = input("请选择你所在的校区 [1]江宁 [2]西康路 [3]其他/不确定（回车=1）: ").strip()
    except Exception:
        pass
    campus = {"1": "江宁", "2": "西康路"}.get(choice, "其他")
    say("")

    lines = []
    add = lines.append

    add("=" * 52)
    add("  河海大学校园网服务配置采集报告")
    add("  校区（运行者选择）: " + campus)
    add("  采集时间: " + time.strftime("%Y-%m-%d %H:%M:%S"))
    add("  脚本版本: " + SCRIPT_VERSION)
    add("  运行环境: " + env_line())
    add("=" * 52)
    add("")

    # [1] 网络环境
    ssid = current_wifi_ssid()
    is_online = online()
    s_portal, _, _ = http(EPORTAL_HOST + "/", timeout=6)
    portal_ok = s_portal in (200, 301, 302, 303)   # 已登录时主页会重定向到成功页，属正常
    portal_err = ""
    if s_portal is None:
        portal_err = "不可达（可能不在校园网环境）"
    elif s_portal >= 500:
        portal_err = "认证服务器暂时故障（HTTP %s）——这不是你的问题，请过几分钟重跑本脚本" % s_portal
    elif not portal_ok:
        portal_err = "异常响应（HTTP %s）" % s_portal
    add("[1] 网络环境")
    add("    WiFi SSID        : " + (ssid or "(获取不到，可能接网线或非 Windows)"))
    add("    在线状态         : " + ("已登录（可上网）" if is_online else "未登录/掉线"))
    if portal_ok:
        detail = "正常（已登录，门户重定向属正常）" if s_portal in (301, 302, 303) else "正常（HTTP 200）"
        add("    认证门户         : " + detail)
    else:
        add("    认证门户         : " + portal_err)
    add("")

    # [2] 登录页劫持链路（校区指纹）
    add("[2] 认证链路参数（校区指纹）")
    url, html = find_login_page()
    if url:
        qs = url.split("?", 1)[1] if "?" in url else ""
        for key in ("wlanacname", "nasip", "ssid", "nasid", "wlanuserip"):
            m = re.search(key + r"=([^&]*)", qs)
            if m and m.group(1):
                add("    %s = %s" % (key, m.group(1)))
        if not any(re.search(k + r"=([^&]*)", qs) and re.search(k + r"=([^&]*)", qs).group(1)
                   for k in ("wlanacname", "nasip")):
            add("    (未解析到链路参数)")
    else:
        add("    门户劫持链路不可达（未连校园网/已登录/或认证服务器故障）")
        if portal_err:
            add("    ※ " + portal_err)
    add("")

    # [3] 服务列表
    add("[3] 本校区服务列表（getServices 接口）")
    qs_for_api = url.split("?", 1)[1] if (url and "?" in url) else ""
    services = fetch_services(qs_for_api) or fetch_services()
    if services:
        for v, d, i in services:
            add("    [%s] 显示名: %-24s 提交值: %s" % (i, d, v))
    else:
        add("    ✗ 未获取到")
        if portal_err:
            add("    ※ " + portal_err)
        else:
            add("    ※ 请确认已连接校园网 WiFi 后重跑")
    add("")

    # [4] 关键词适配测试
    add("[4] 关键词适配测试（hhu-autologin 的四个关键词）")
    all_hit = True
    if services:
        for kw in KEYWORDS:
            hits = pick_services(services, kw)
            if len(hits) == 1:
                add("    %-4s -> 提交值: %-28s （唯一命中 ✓）" % (kw, hits[0][0]))
            elif len(hits) > 1:
                all_hit = False
                add("    %-4s -> 命中 %d 项（有歧义！）: %s" %
                    (kw, len(hits), " / ".join(v for v, _d in hits)))
            else:
                all_hit = False
                add("    %-4s -> 无命中（该校区列表里没有含此关键词的服务！）")
        add("    适配结论: " + ("四个关键词全部唯一命中，可直接使用本项目 ✓" if all_hit
                                else "存在未命中或歧义，请在报告中特别标注反馈"))
    else:
        add("    （无服务列表，跳过）")
    add("")

    # [5] 当前登录会话（脱敏）
    add("[5] 当前登录会话（若已登录；学号已脱敏，不含密码）")
    if is_online:
        info = get_online_info(fetch_user_index())
        uid = info.get("userId", "")
        if uid:
            add("    学号（脱敏）        : " + mask_user(uid))
            add("    当前服务显示名      : " + str(info.get("service", "")))
            add("    当前服务提交值      : " + str(info.get("realServiceName", "")))
            add("    用户组              : " + str(info.get("userGroup", "")))
        else:
            add("    在线但未获取到会话信息")
    else:
        add("    当前未登录，跳过。（建议：浏览器登录校园网后重跑一次本脚本，[5] 会更完整）")
    add("")

    # [6] WiFi 门卫三档模拟
    add("[6] WiFi 门卫三档模拟（针对当前 WiFi）")
    if ssid:
        low = ssid.lower()
        for label, val in (("1. 严格仅校园网（Hohai University）", "Hohai University"),
                           ("2. 仅校园网（Hohai）", "Hohai"),
                           ("3. 无论是否校园网都尝试登录", "")):
            ok = (not val) or (val.lower() in low) or (low in val.lower())
            add("    %s: %s" % (label, "放行" if ok else "拦截"))
    else:
        add("    （获取不到 SSID，跳过）")
    add("")

    # [7] 自动推断
    add("[7] 自动观察")
    blob = " ".join(v + " " + d for v, d, _i in services) if services else ""
    real = ""
    if is_online:
        real = str(get_online_info(fetch_user_index()).get("realServiceName", ""))
    text_all = blob + " " + real
    if "江宁" in text_all:
        add("    服务名中检测到「江宁」字样，与所选校区一致" if campus == "江宁"
            else "    注意：服务名中出现「江宁」字样，与所选校区（%s）不一致" % campus)
    elif "西康路" in text_all:
        add("    服务名中检测到「西康路」字样")
    elif "常州" in text_all or "金坛" in text_all:
        add("    服务名中出现「常州/金坛」字样——当前网络似乎仍在常州校区，请在目标校区重新采集")
    else:
        add("    服务名中未出现校区字样（记录原文供分析）")
    add("")
    add("报告结束。感谢帮助！可打开自查后再发送。")

    report = "\n".join(lines)
    fname = "服务配置报告_%s_%s.txt" % (campus, time.strftime("%Y%m%d_%H%M%S"))
    base = here()
    saved = ""
    for d in (base, os.path.join(os.path.expanduser("~"), "Desktop"),
              os.environ.get("TEMP", base)):
        if not d:
            continue
        try:
            full = os.path.join(d, fname)
            with open(full, "w", encoding="utf-8-sig") as f:
                f.write(report)
            saved = full
            break
        except Exception:
            continue
    if saved:
        say("✓ 报告已生成: " + saved)
        say("  （UTF-8 编码，记事本可直接打开；自查无误后发给项目作者即可）")
    else:
        say("✗ 报告写入失败（目录权限不足？）。以下为报告全文，请直接复制发送：")
        say("-" * 52)
        say(report)

    try:
        input("\n按回车键退出...")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        errname = "采集脚本异常详情_%s.txt" % time.strftime("%Y%m%d_%H%M%S")
        saved = ""
        try:
            base = here()
            with open(os.path.join(base, errname), "w", encoding="utf-8") as f:
                f.write(tb)
            saved = os.path.join(base, errname)
        except Exception:
            pass
        say("")
        say("=" * 52)
        if saved:
            say("脚本遇到错误已停止。")
            say("错误详情已保存到本脚本旁边的: " + saved)
            say("请把这个文件发给项目作者，谢谢！")
        else:
            say("脚本遇到错误已停止，且错误详情无法保存。")
            say("请截图以下全部内容发给项目作者：")
            say("-" * 52)
            say(tb)
        try:
            input("\n按回车键退出...")
        except Exception:
            pass
