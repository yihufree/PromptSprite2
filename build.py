# -*- coding: utf-8 -*-
"""
build.py - PromptSprite 一键打包脚本（单 EXE）
用法：python build.py

流程（自动完成，无需手工分步）：
  1. 确保 pyinstaller 已安装（缺失自动安装）
  2. 确保 requirements.txt 运行依赖已安装（缺失自动安装）
  3. 由 Icons/PSicon.png 生成 build/app.ico
  4. 准备内置完整数据库：复制 data/prompts.db → app/resources/builtin_prompts.db（内嵌进 EXE）
  4.5 清理/校验内置库"运行态 meta"：clean_builtin_meta.py --apply + --check（2026-09-15 用户要求 C12；
      **同日升级为硬门禁**：任一步失败即终止打包）
  5. 执行 PyInstaller（onefile + windowed），把所有必要依赖与最新数据打入单 EXE
  6. 更新打包数据文件夹：复制 data/prompts.db → dist/data/prompts.db
  7. 校验产物 dist/PromptSprite.exe 并显示大小

说明：
  - 优先使用 .venv 的 Python 打包（依赖已齐备）；无 .venv 时使用当前解释器。
  - 打包产物为单文件（内置手册、图标、完整最新数据库一并内嵌），并自动按
    PromptSprite_<日期YYMMDD>_V<版本号>.EXE 命名（2026-09-07 第7条）。
    当前版本 V2.0.0 → 例如 PromptSprite_260914_V2.0.0.EXE（版本号自动取自 app/config.py）。
  - 每次打包均携带开发态最新数据，保证 EXE 开箱即用最新内容。
  - 2026-09-14（V2.0.0）：打包流程与 spec **无需任何改动**（未新增第三方依赖；
    联网抓取/短音频试听等均为标准库），仅版本号随之升为 2.0.0。
"""
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
SPEC = os.path.join(ROOT, "build", "PromptSprite.spec")
ICON_GEN = os.path.join(ROOT, "build", "generate_icon.py")
DIST_DIR = os.path.join(ROOT, "dist")
EXE_OUT = os.path.join(DIST_DIR, "PromptSprite.exe")
DEV_DB = os.path.join(ROOT, "data", "prompts.db")                  # 开发态主库（最新数据）
BUILTIN_DB = os.path.join(ROOT, "app", "resources", "builtin_prompts.db")  # 内嵌库（打包进 EXE）
DIST_DB = os.path.join(DIST_DIR, "data", "prompts.db")             # EXE 旁数据文件夹

# 优先使用 .venv 的解释器，其次使用当前解释器
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PY = VENV_PY if os.path.isfile(VENV_PY) else sys.executable


def _load_version() -> str:
    """从 app/config.py 读取 APP_VERSION（打包文件名需带版本号，2026-09-07 第7条）"""
    try:
        cfg_path = os.path.join(ROOT, "app", "config.py")
        with open(cfg_path, encoding="utf-8") as f:
            m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read())
        return m.group(1) if m else "0.0.0"
    except Exception:
        return "0.0.0"


def versioned_exe_path() -> str:
    """EXE 产物文件名格式：PromptSprite_<最后修改日期YYMMDD>_V<版本号>.EXE
    （2026-09-07 第7条；2026-09-22：示例版本号更新为当前 V2.3.0）"""
    tag = datetime.now().strftime("%y%m%d")
    return os.path.join(DIST_DIR, f"PromptSprite_{tag}_V{_load_version()}.exe")


def _run(args, cwd=None):
    """打印并执行命令"""
    print(">>", " ".join(args))
    subprocess.run(args, cwd=cwd, check=True)


def _prepare_builtin_db() -> None:
    """打包前：将开发态最新主库复制为内嵌库（build/PromptSprite.spec 的 datas 引用）。

    2026-08-18（第023条新增）：确保每次打包都携带开发中最后更新的数据。
    """
    if not os.path.isfile(DEV_DB):
        print(f"[数据] 未找到 {DEV_DB}，本次打包将不含完整数据库（仅内置手册 90 条）")
        return
    os.makedirs(os.path.join(ROOT, "app", "resources"), exist_ok=True)
    shutil.copy2(DEV_DB, BUILTIN_DB)
    print(f"[数据] 内置完整数据库已就绪（{os.path.getsize(BUILTIN_DB) / 1024 / 1024:.1f} MB，来自开发态最新库）")


def _update_dist_data() -> None:
    """打包后：将最新主库复制到 EXE 旁数据文件夹，保证 dist 开箱即用最新数据。

    2026-08-18（第023条新增）：只更新 prompts.db，保留已有 images/backup 等用户数据。
    """
    if not os.path.isfile(DEV_DB):
        print("[数据] 开发态主库不存在，跳过 dist/data 更新")
        return
    os.makedirs(os.path.join(ROOT, "dist", "data"), exist_ok=True)
    shutil.copy2(DEV_DB, DIST_DB)
    print(f"[数据] dist/data/prompts.db 已更新为最新数据（{os.path.getsize(DIST_DB) / 1024 / 1024:.1f} MB）")


def _clean_builtin_meta() -> bool:
    """打包前：**清理并校验"出厂内置库"的运行态 meta 键**（2026-09-15 用户要求 C12）。

    背景：内置库由"开发态主库"复制而来，而开发机运行程序会把运行态数据写进它的 meta 表
    （上次同步时间、窗口大小/视图模式/标签页停留位置，甚至**计算机名** `settings_computer_code`），
    新用户首次安装会直接继承这些值。此处自动调用项目根的 `clean_builtin_meta.py`：
      ① `--apply`：删除白名单（schema_version / builtin_manual_version / tag_dict）之外的键，
         写前自动备份到 `data/backup/`；
      ② `--check`：复核（退出码 0 = 已无运行态键）。

    **硬门禁**（2026-09-15 用户确认，原实现为"非致命"已改为阻断）：脚本缺失、调用异常、
    任一步退出码非 0 ⇒ 返回 `False`，由 `main()` 打印错误并 **终止打包**，
    避免"运行态 / 开发机个人信息"随 EXE 出厂。
    """
    script = os.path.join(ROOT, "clean_builtin_meta.py")
    if not os.path.isfile(script):
        print("[数据] ✗ 未找到 clean_builtin_meta.py，无法完成出厂库运行态清理校验")
        return False
    for args, desc in ((["--apply"], "清理"), (["--check"], "复核")):
        try:
            r = subprocess.run([PY, script, *args], cwd=ROOT)
        except Exception as exc:
            print("[数据] ✗ 内置库运行态%s调用失败：%s" % (desc, exc))
            return False
        if r.returncode != 0:
            print("[数据] ✗ 内置库运行态%s未通过（退出码 %d）" % (desc, r.returncode))
            return False
        print("[数据] ✓ 内置库运行态%s通过" % desc)
    return True


def main() -> None:
    print(f"使用解释器：{PY}")

    # 1. 安装 pyinstaller（如缺失）
    try:
        import PyInstaller  # noqa: F401
        print("[1/7] pyinstaller 已安装")
    except ImportError:
        print("[1/7] 安装 pyinstaller ...")
        _run([PY, "-m", "pip", "install", "pyinstaller"])

    # 2. 安装/校验运行依赖
    print("[2/7] 安装/校验运行依赖（requirements.txt）...")
    _run([PY, "-m", "pip", "install", "-r", REQUIREMENTS])

    # 3. 生成应用图标
    print("[3/7] 生成应用图标 build/app.ico ...")
    _run([PY, ICON_GEN])

    # 4. 准备内置完整数据库（2026-08-18 第023条新增）
    print("[4/7] 准备内置完整数据库（打包进 EXE）...")
    _prepare_builtin_db()

    # 4.5 清理/校验内置库运行态 meta（2026-09-15 用户要求 C12；同日升级为**硬门禁**）
    print("[4.5/7] 清理/校验内置库运行态 meta（clean_builtin_meta.py）...")
    if not _clean_builtin_meta():
        print("✗ 出厂内置库运行态 meta 清理/校验未通过 → 已终止打包（请按上方提示手工排查后重试）")
        sys.exit(1)

    # 5. 执行 PyInstaller 打包（在项目根目录运行，产物落在 dist/）
    print("[5/7] 执行 PyInstaller 打包（onefile + windowed，含最新数据）...")
    _run([PY, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC], cwd=ROOT)

    # 6. 更新 EXE 旁数据文件夹（2026-08-18 第023条新增）
    print("[6/7] 更新 EXE 旁数据文件夹 dist/data ...")
    _update_dist_data()

    # 6.5 按新命名规则归档 EXE（2026-09-07 第7条）：
    # PromptSprite_<最后修改日期YYMMDD>_V<版本号>.EXE，如 PromptSprite_260907_V1.6.0.EXE
    target = versioned_exe_path()
    if not os.path.isfile(EXE_OUT):
        print("未找到产物，请查看上方错误信息。")
        sys.exit(1)
    if os.path.abspath(target) != os.path.abspath(EXE_OUT):
        if os.path.isfile(target):   # 同日同版本已存在 → 先移除再改名，避免被 PyInstaller 占用
            os.remove(target)
        os.rename(EXE_OUT, target)
    else:
        target = EXE_OUT

    # 7. 校验产物
    if os.path.isfile(target):
        size_mb = os.path.getsize(target) / 1024 / 1024
        print(f"=== 打包完成：{target}（{size_mb:.1f} MB）===")
        print("已内置最新完整数据库；首次运行自动在 exe 同目录生成/复用 data/。")
        print("全局热键需管理员权限或杀毒软件白名单（失败不影响其他功能）。")
    else:
        print("未找到产物，请查看上方错误信息。")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"打包失败（退出码 {exc.returncode}），请查看上方错误信息。")
        sys.exit(exc.returncode)
