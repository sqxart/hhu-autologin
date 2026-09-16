# -*- coding: utf-8 -*-
"""纯函数单元测试：服务解析 / 关键词匹配 / URL 编码 / 登录页链路正则。
测试数据来自真实抓包的河海登录页（常州校区）。"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hhu_login as hl

# 真实抓包片段（常州校区登录页，2026-09）
BOOTSTRAP_HTML = (
    "<script>top.self.location.href="
    "'http://10.96.0.155/eportal/index.jsp?wlanuserip=a33477056511b504e4b94617958e43b9"
    "&wlanacname=da09139abe7289065f8686f2a826bbc2&t=wireless-v2&url=35e6780db7fde27a9440bcc5335350b9'"
    "</script>"
)
LOGIN_PAGE_HTML = """
<div id="bch_service_0" onclick="selectService('校园外网服务(out-campus NET)','校园网(Campus NET)','0')">
  <div class="right" id="_service_0">校园网(Campus NET) </div></div>
<div id="bch_service_1" onclick="selectService('中国移动(CMCC NET)','中国移动(CMCC NET)','1')">
  <div class="right" id="_service_1">中国移动(CMCC NET) </div></div>
<div id="bch_service_2" onclick="selectService('中国电信(常州)','中国电信(CTCC NET)','2')">
  <div class="right" id="_service_2">中国电信(CTCC NET) </div></div>
<div id="bch_service_3" onclick="selectService('中国联通(常州)','中国联通(CUCC NET)','3')">
  <div class="right" id="_service_3">中国联通(CUCC NET) </div></div>
<input name="net_access_type" id="net_access_type" value="校园外网服务(out-campus NET)" type="hidden">
"""


def test_index_url_regex_extracts_portal_url():
    m = hl.INDEX_URL_RE.search(BOOTSTRAP_HTML)
    assert m, "应从引导页提取出带参数的 index.jsp 地址"
    assert "index.jsp?" in m.group(0)
    assert "wlanuserip=a33477056511b504e4b94617958e43b9" in m.group(0)


def test_parse_services_real_page():
    services = hl.parse_services(LOGIN_PAGE_HTML)
    assert len(services) == 4
    values = [v for v, _d, _i in services]
    # 关键陷阱：显示名与提交值不同
    assert "校园外网服务(out-campus NET)" in values
    assert "中国电信(常州)" in values
    assert "中国联通(常州)" in values
    displays = [d for _v, d, _i in services]
    assert "中国电信(CTCC NET)" in displays  # 显示名里反而是 CTCC


def test_pick_service_by_keyword():
    services = hl.parse_services(LOGIN_PAGE_HTML)
    assert hl.pick_service(services, "校园网") == "校园外网服务(out-campus NET)"
    assert hl.pick_service(services, "移动") == "中国移动(CMCC NET)"
    assert hl.pick_service(services, "电信") == "中国电信(常州)"  # 按提交值匹配
    assert hl.pick_service(services, "联通") == "中国联通(常州)"
    assert hl.pick_service(services, "不存在的服务") is None


def test_ece_double_encode_matches_page_js():
    # encodeURIComponent 的安全字符集：A-Za-z0-9 -_.!~*'()
    assert hl.ece("2535010118") == "2535010118"          # 纯数字不变
    assert hl.ece("!Wc220913") == "!Wc220913"            # ! 属安全字符
    assert hl.ece("校园网") == urllib_quote_twice("校园网")
    assert hl.ece("a b") == "a%2520b"                    # 空格两次编码
    assert hl.ece("a&b=c") == "a%2526b%253Dc"            # & 和 = 被编码


def urllib_quote_twice(s: str) -> str:
    import urllib.parse
    safe = "-_.!~*'()"
    return urllib.parse.quote(urllib.parse.quote(s, safe=safe), safe=safe)


# 真实抓包片段：InterFace.do?method=getServices 响应的 serviceContent 字段
# （服务列表由页面 JS 调该接口动态注入，登录页原始 HTML 里没有。2026-09 实测）
GETSERVICES_SERVICE_CONTENT = (
    "<div id='bch_service_0' onclick=\"selectService('校园外网服务(out-campus NET)','校园网(Campus NET)','0')\">"
    "<input name=\"net_access_type\" id=\"net_access_type\" value='校园外网服务(out-campus NET)' type=\"hidden\"/>"
    "<div id='bch_service_1' onclick=\"selectService('中国移动(CMCC NET)','中国移动(CMCC NET)','1')\">"
    "<div id='bch_service_2' onclick=\"selectService('中国电信(常州)','中国电信(CTCC NET)','2')\">"
    "<div id='bch_service_3' onclick=\"selectService('中国联通(常州)','中国联通(CUCC NET)','3')\">"
)


def test_fetch_services_parses_service_content_field():
    """fetch_services 的解析约定：接口响应 serviceContent 里的 selectService 项可被 SERVICE_RE 提取。"""
    services = hl.parse_services(GETSERVICES_SERVICE_CONTENT)
    assert len(services) == 4
    assert hl.pick_service(services, "联通") == "中国联通(常州)"
    assert hl.pick_service(services, "移动") == "中国移动(CMCC NET)"
    assert hl.pick_service(services, "电信") == "中国电信(常州)"
    assert hl.pick_service(services, "校园网") == "校园外网服务(out-campus NET)"


def test_detect_carrier_keyword():
    """从服务名提取跨校区通用的关键词（校区后缀会变，运营商不变）。"""
    assert hl.detect_carrier_keyword("中国移动(CMCC NET)") == "移动"
    assert hl.detect_carrier_keyword("中国电信(常州)") == "电信"
    assert hl.detect_carrier_keyword("中国联通(常州)") == "联通"
    assert hl.detect_carrier_keyword("校园外网服务(out-campus NET)") == "校园网"
    assert hl.detect_carrier_keyword("") == "校园网"


def test_save_account_preserves_other_sections(tmp_path, monkeypatch):
    """--setup 写配置时应保留 [guard] 等用户已改过的段落。"""
    import configparser
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[account]\nusername = old\npassword = old\nservice = 校园网\n"
        "\n[guard]\nwifi_ssid = MyWiFi\ninterval_minutes = 5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hl, "CONFIG_FILE", cfg)
    hl.save_account("2624030207", "pw", "移动")
    cp = configparser.ConfigParser()
    cp.read(cfg, encoding="utf-8")
    assert cp.get("account", "username") == "2624030207"
    assert cp.get("account", "password") == "pw"
    assert cp.get("account", "service") == "移动"
    assert cp.get("guard", "wifi_ssid") == "MyWiFi"      # 用户已改的值不被覆盖
    assert cp.get("guard", "interval_minutes") == "5"


# ---------------------------------------------------------------- 代理禁用（回归：v1.4.1）

def test_opener_bypasses_system_proxy():
    """HTTP 客户端必须禁用系统代理：Clash 等代理开着时走代理会全军覆没
    （实测 2026-09-16：内网门户请求被代理拒绝，GUI 误报"不在校园网"）。

    机制：build_opener 传入空 ProxyHandler({}) 会顶掉默认的"读系统代理"版，
    空实例本身不留在 handler 链里——所以断言语义是"链上不允许存在
    任何带代理配置的 ProxyHandler"，并加源码检查双保险。
    """
    import urllib.request
    bad = [h for h in hl.OP.handlers
           if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
    assert not bad, f"OP 混入了带代理配置的 handler: {bad!r}"
    src = Path(hl.__file__).read_text(encoding="utf-8")
    assert "urllib.request.ProxyHandler({})" in src, "OP 构造必须保留空 ProxyHandler 直连"
