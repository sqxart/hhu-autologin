#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包「新手便携版」压缩包：exe + 使用前必读！！！.txt。

用法（在仓库根目录）:
    python tools/make_portable_zip.py                 # 读 dist/，产物写到仓库根
    python tools/make_portable_zip.py --dist dist --out .

产物结构（新手解压后一眼能看到说明书）:
    hhu-autologin_v1.4.0_portable.zip        ← 外层用 ASCII 名
      ├── 使用前必读！！！.txt
      └── 河海校园网自动登录/                 ← 内部条目保持中文
            ├── 使用前必读！！！.txt   （同一份，防止只拷文件夹时丢了说明书）
            ├── hhu_gui.exe
            ├── hhu_login.exe
            └── hhu_campus_report.exe

注意：zip 文件名必须 ASCII——GitHub 会把 Release 附件名里的非 ASCII
字符直接删掉（实测中文名上传后变成 "_v1.4.0_.zip"）；压缩包内部条目
不受影响，保持中文。
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER_NAME = "河海校园网自动登录"
GUIDE_NAME = "使用前必读！！！.txt"
APP_FILES = ["hhu_gui.exe", "hhu_login.exe", "hhu_campus_report.exe"]
REQUIRED = ("hhu_gui.exe", "hhu_login.exe")   # 采集工具缺失只警告，不阻断发布


def app_version() -> str:
    """从 hhu_login.py 读版本号（正则解析，不 import，避免副作用）。"""
    m = re.search(r'__version__\s*=\s*"([^"]+)"',
                  (ROOT / "hhu_login.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


def make_zip(dist: Path, out_dir: Path) -> Path:
    guide = ROOT / GUIDE_NAME
    if not guide.exists():
        sys.exit(f"[x] 找不到说明书：{guide}")

    missing = [f for f in REQUIRED if not (dist / f).exists()]
    if missing:
        sys.exit(f"[x] {dist} 里缺少必需文件：{', '.join(missing)}\n"
                 f"    先打包 exe：pyinstaller --onefile --clean --name hhu_login hhu_login.py")

    files = [f for f in APP_FILES if (dist / f).exists()]
    skipped = [f for f in APP_FILES if f not in files]
    if skipped:
        print(f"[!] 未找到 {', '.join(skipped)}，将不打进压缩包")

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"hhu-autologin_v{app_version()}_portable.zip"   # ASCII 名，见模块 docstring
    guide_bytes = guide.read_bytes()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(GUIDE_NAME, guide_bytes)                      # zip 根目录：一打开就看到
        z.writestr(f"{FOLDER_NAME}/{GUIDE_NAME}", guide_bytes)    # 文件夹内再放一份
        for name in files:
            z.write(dist / name, f"{FOLDER_NAME}/{name}")

    print(f"[√] 已生成：{zip_path}")
    print(f"    {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            print(f"      {info.file_size / 1024 / 1024:6.1f} MB  {info.filename}")
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser(description="打包新手便携版 zip")
    ap.add_argument("--dist", default="dist", help="PyInstaller 产物目录（默认 dist）")
    ap.add_argument("--out", default=".", help="zip 输出目录（默认仓库根）")
    args = ap.parse_args()
    make_zip(Path(args.dist).resolve(), Path(args.out).resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
