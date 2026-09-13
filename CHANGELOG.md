# 更新日志

## v1.1.0 — 未发布

- 系统通知（可配置）：登录成功/失败弹 Windows Toast，带失败冷却防刷屏
- 心跳日志：在线时每分钟一行，日志最后一行时间即守护存活时间
- SSID 跳过、服务未匹配等情况写日志
- logout 改用 userIndex 方式（logoutByUserIdAndPass 实测被拒）

## v1.0 — 2026-09-13

首发版本。

- 掉线自动重登：每分钟检测，在线秒退，掉线直连 ePortal 接口登录（无浏览器）
- 登录页服务动态解析（支持校园网/移动/电信/联通，显示名与提交值不一致自动纠正）
- 参数双重 URL 编码，与页面 `doauthen()` 逐字节对齐
- 断路器：认证被拒即停止重试，防止触发验证码锁定
- SSID 门卫：仅在校内 WiFi 工作（可选）
- `--check` 自检 / `--logout` 注销 / `--loop` 循环 / `--clear-flag`
- GitHub Actions 自动打包 Windows exe
