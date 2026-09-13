# -*- mode: python ; coding: utf-8 -*-
"""
PromptSprite.spec - PyInstaller 打包配置（阶段八：打包交付与测试）

用法（在项目根目录执行）：
    pyinstaller --noconfirm --clean build/PromptSprite.spec

说明：
  - onefile + windowed（无控制台窗口）
  - 内置手册打包进 resources/（config.builtin_manual_path 打包态读取 sys._MEIPASS/resources/）
  - 内置完整数据库打包进 resources/（2026-08-18 第023条：由 build.py 提前从开发态
    data/prompts.db 生成 app/resources/builtin_prompts.db；打包态首次运行自动复制到 exe 旁 data/）
  - 数据目录 data/ 在 exe 同目录自动生成（优先使用内置完整库）
"""
import os

APP_ROOT = os.path.dirname(SPECPATH)  # SPECPATH 为 build/ 目录，项目根为其上级

a = Analysis(
    [os.path.join(APP_ROOT, "app", "main.py")],
    pathex=[APP_ROOT],
    binaries=[],
    datas=[
        (os.path.join(APP_ROOT, "app", "resources", "builtin_manual.md"), "resources"),
        (os.path.join(APP_ROOT, "app", "resources", "builtin_prompts.db"), "resources"),  # 2026-08-18（第023条）：内嵌最新完整数据库
        (os.path.join(APP_ROOT, "Icons", "PSicon.png"), "Icons"),  # 托盘/窗口图标源
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "scipy", "IPython", "jedi"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PromptSprite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                       # windowed：不弹控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(APP_ROOT, "build", "app.ico"),
)
