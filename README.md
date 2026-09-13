# hhu-autologin

![平台](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Release](https://img.shields.io/github/v/release/sqxart/hhu-autologin)

河海大学（Hohai University）校园网自动登录守护。掉线自动重登、全程无浏览器、纯 Python 标准库零依赖。

> 适用对象：使用学校 ePortal 网页认证的河海同学（江宁 / 常州 / 西康路三校区无线网均适用）。
> 如果你曾被这些问题折磨：*每 1~2 天被踢下线*、*每次都要打开浏览器点登录*、*chromedriver 版本永远对不上*——这个项目就是为你写的。

## 🚀 三步上手（不需要任何电脑知识）

1. 打开 [Releases 下载页](../../releases)，下载 **`hhu_login.exe`**
   ⚠️ 不要点 "Source code"——那只是源代码，不是软件
2. 在电脑上新建一个文件夹（比如 `D:\校园网工具`），把 exe 放进去，**双击运行**
   → 它会自动生成 `config.ini` 并打开记事本：填上学号、密码，保存并关闭
3. 双击 **`install.bat`** —— 完成！以后开机自动守护，掉线自动恢复

> - 首次运行如果 Windows 弹蓝色警告：点 **「更多信息」→「仍要运行」**（没签名的小软件都这样）
> - 文件夹请放在 **D 盘等固定位置**，不要放桌面或下载文件夹（计划任务会记住这个位置，移动后需重新运行 install.bat）
> - 想确认它在工作：双击 `hhu_login.exe --check`，或看 `logs\hhu_login_debug.log` 最后一行的时间

## 特性

- **掉线自动重登**：每分钟检测一次，在线时 0.2 秒静默退出，掉线后 3 秒内自动恢复
- **零依赖**：纯 Python 标准库，不装 selenium、不装 chromedriver、不开浏览器
- **服务自动适配**：自动解析登录页的服务下拉框（校园网 / 移动 / 电信 / 联通），按关键词匹配，三校区通用
- **系统通知（可选）**：登录成功/失败弹 Windows 原生通知（Win10/Win11），带冷却防刷屏，`config.ini` 一键关闭
- **断路保护**：密码错误被服务器拒绝后立即停止重试（不撞接口、不触发验证码锁定），改完密码一键解除
- **验证码状态识别**：触发验证码时明确告诉你原因和恢复方法，而不是无限失败
- **SSID 门卫**（可选）：只在校园 WiFi 下工作，连手机热点时不瞎折腾
- **开机自启**：install.bat 一键注册 Windows 计划任务

## 快速开始

### 方式一：下载打包好的 exe（推荐，无需装 Python）

1. 到 [Releases](../../releases) 下载 `hhu_login.exe`，放到任意固定目录（建议如 `D:\hhu-autologin\`）
2. 同目录创建 `config.ini`（可直接复制 `config.example.ini`），填写学号和密码
3. 双击 `hhu_login.exe --check` 看自检结果
4. 双击 `install.bat`，完成。以后开机自动守护，不用管

> **SmartScreen / 杀毒软件提示**：未签名的 exe 首次运行会被 Windows SmartScreen 拦截，点「更多信息 → 仍要运行」即可；个别杀软对 PyInstaller 打包的程序有误报，可添加信任或改用方式二（源码运行）。

### 方式二：Python 运行（自备 Python 3.8+）

```bat
git clone https://github.com/sqxart/hhu-autologin.git
cd hhu-autologin
copy config.example.ini config.ini
notepad config.ini          # 填学号、密码
python hhu_login.py --check # 自检
install.bat                 # 注册每分钟计划任务（无窗口运行需 pythonw 或 exe）
```

### 方式三：macOS / Linux（进阶）

核心逻辑跨平台，无需 install.bat，用 cron 替代计划任务：

```bash
python3 hhu_login.py --check          # 先自检
crontab -e                            # 加入一行（每分钟检测）：
* * * * * /usr/bin/python3 /path/to/hhu_login.py --quiet
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `hhu_login.py` | 跑一次：在线秒退，掉线自动登录（计划任务就是调它） |
| `hhu_login.py --loop [分钟]` | 常驻循环模式（默认间隔读配置） |
| `hhu_login.py --check` | 自检：配置 / SSID / 在线状态 / 门户链路 / 服务列表 |
| `hhu_login.py --logout` | 注销当前会话（切换账号时用） |
| `hhu_login.py --clear-flag` | 密码改对后清除断路器标志 |
| `uninstall.bat` | 卸载计划任务 |

## 它是怎么工作的（原理）

把学校网络想象成一个大院子，门口有个"门卫"（上网控制器）：

```
 你 ──访问任意网址──> 门卫：「没登记，不许过」
 你 ──被转送到登录页──> ePortal（网址后面带一串加密的"通行证参数"）
 你 ──账号密码+通行证──> 认证接口 InterFace.do?method=login
 认证接口 ──拿通行证找门卫放行──> 上网 ✅
```

本脚本做的就是第三步的自动化，有几个**实测踩坑后才知道**的关键点：

1. **通行证参数不能省**：直接打开 `eportal.hhu.edu.cn` 主页是拿不到有效通行证的（会弹"设备未注册"）。必须先访问 `1.2.3.4` 这类会被门卫劫持的地址，跟着 302 跳转拿到带参数的登录页
2. **服务名必须动态解析**：下拉框显示名和实际提交值不一致（例：电信显示"中国电信(CTCC NET)"，实际提交"中国电信(常州)"），所以每次从登录页现场解析 `selectService(...)` 映射
3. **所有参数要双重 URL 编码**：页面 JS 对 userId/password/service/queryString 都做了两次 `encodeURIComponent`，少一层都会失败
4. **密码不加密**：`passwordEncrypt=false`，页面里的 RSA 加密代码是注释掉的，不要画蛇添足

## 常见问题对照表

| 现象 | 原因 | 解决 |
|---|---|---|
| 页面弹"设备未注册,请联系管理员" | 直接打开门户主页，缺通行证参数 | 从 `1.2.3.4` 或任意 HTTP 网址进入；本脚本自动处理 |
| `result:fail 用户名或密码错误` | 密码错 / 密码改了没同步 | 改 `config.ini` 后运行 `--clear-flag` |
| 提示"触发验证码" | 密码连续错了 3 次 | 用浏览器登录一次（输验证码），再 `--clear-flag` |
| `www.msftconnecttest.com` 解析失败 | 该域名在河海网络永久被过滤，正常现象 | 检测脚本不要用它（本项目已避开） |
| 开着 Clash 时怎么都登不上 | 代理把校园网内网请求送去了外网节点 | **先登录校园网再开 Clash**；掉线时先关 Clash 等一分钟 |
| 会一直弹通知吗 | 不会 | 成功通知只在真的执行登录后弹一次；失败通知有冷却时间（默认 30 分钟一条）；密码错误触发断路器后只弹一条"需要人工介入" |
| 睡眠唤醒后没网 | 计划任务唤醒后下一分钟才触发 | 等一分钟；或手动跑一次 |

## 断路器说明

为保护你的账号（密码连错 3 次会触发验证码）和学校认证服务器，认证被拒一次后脚本会**立即停止重试**并在 `logs/auth_failed.flag` 留下原因。这不是坏了——修正 `config.ini` 后运行：

```bat
hhu_login.py --clear-flag
```

最多一分钟后自动恢复。

## 服务选择（三校区）

`config.ini` 的 `service` 填**关键词**即可，脚本会自动匹配登录页下拉框（显示"中国电信(常州)"这类带校区后缀的选项也没问题）：

```ini
service = 校园网    ; 或 移动 / 电信 / 联通
```

> 运营商服务（需办理对应手机卡）暂未完整支持：页面在选择运营商后可能还需额外提交运营商账号密码，该流程尚待有运营商卡的同学实测。欢迎提 Issue 提供抓包信息。

## 已知限制

- 仅实测 Windows 11 + 常州校区无线网；macOS / Linux 下核心逻辑通用（`--loop` 模式），安装脚本未适配
- 接网线（无 WiFi 环境）请把 `config.ini` 里的 `wifi_ssid` 留空
- 你的密码明文保存在本地 `config.ini` 里（本项目 `.gitignore` 已排除它，请勿手动提交）

## 同类项目（河海生态，互相致敬）

河海已有不少同学造过这个轮子，各有所长，按需取用：

- [ThreeStones1029/AutoLoginCampusNetwork](https://github.com/ThreeStones1029/AutoLoginCampusNetwork) — Selenium 浏览器方案，Windows/Ubuntu 双平台教程详细
- [Xu-zn/campus-auto-login](https://github.com/Xu-zn/campus-auto-login) — Rust + Slint GUI，适合喜欢图形界面的同学
- [CodeFromInterest/auto-hhu-for-openWRT](https://github.com/CodeFromInterest/auto-hhu-for-openWRT) — 纯 Shell + curl 路由器方案，全宿舍共享一条认证
- [RedDragon0293/EportalNT](https://github.com/RedDragon0293/EportalNT) — 安卓端 EPortal 客户端，逆向了剩余时长/在线设备查询
- [RedDragon0293/EPortal](https://github.com/RedDragon0293/EPortal) — 同作者的 Java 桌面版，支持多账号
- [yiyiyixixi/hhu_campus_auth](https://github.com/yiyiyixixi/hhu_campus_auth) — 轻量 Shell 脚本

本项目的差异化：**无浏览器零依赖**（不折腾 chromedriver 版本）、**凭证全自动获取**（无需手动抓包 queryString）、**断路保护**（不撞接口不触发验证码）、故障自诊断（`--check`）。

## 免责声明

本项目仅供学习研究和个人便利使用。请遵守《河海大学网络安全管理办法》及所在网络环境的相关规定，勿用于批量请求、绕过计费等用途。使用本项目产生的任何后果由使用者自行承担。

## License

[MIT](LICENSE)
