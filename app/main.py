# -*- coding: utf-8 -*-
"""
main.py - PromptSprite 程序入口（启动流程编排）
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

启动流程：
  1. 启动静默备份（主库存在时；失败降级为状态栏黄点）
  2. 连接数据库：首次自动建表 + 写入 7 个预置根目录
  3. 打开主窗口
  4. 首次启动：内置手册自动导入（进度条模态窗口）
  5. 注册全局热键 Ctrl+Shift+P（失败提示，不影响主功能）

用法：
  python app/main.py          # 直接运行主文件（推荐，已解决包路径问题）
  python -m app.main          # 以包方式运行
  python -m app.main --smoke  # 冒烟测试：5 秒后自动退出（用于自动化验证）
"""
import argparse
import os
import shutil
import subprocess
import sys
from tkinter import messagebox

# 将项目根目录加入 sys.path：
# 直接运行 python app/main.py 时没有包上下文，需让 app 包可被绝对导入，
# 从而解决"运行主文件无法启动程序"的路径问题（其他模块的相对导入不受影响）。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app import config  # 纯标准库依赖，用于判断打包态与定位 requirements.txt


def _ensure_dependencies() -> None:
    """直接运行且缺少第三方库时，自动安装 requirements.txt 依赖（打包态跳过）。

    说明：应用依赖 customtkinter/Pillow/pyperclip/openpyxl/pystray/keyboard；
    用 IDE 或系统 Python 直接运行主文件时可能未安装，此处自动补齐，保证一键运行。
    """
    if config.is_frozen():  # 打包态第三方库已内嵌，无需安装
        return
    try:
        import customtkinter  # noqa: F401
        import PIL            # noqa: F401
        import pyperclip      # noqa: F401
        import openpyxl       # noqa: F401
        import pystray        # noqa: F401
        import keyboard       # noqa: F401
    except ModuleNotFoundError:
        req = os.path.join(_PROJECT_ROOT, "requirements.txt")
        print("[启动引导] 检测到缺少第三方库，正在自动安装依赖（首次运行可能需要数十秒）...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req])
            print("[启动引导] 依赖安装完成，正在启动 PromptSprite ...")
        except Exception as exc:
            print(f"[启动引导] 依赖安装失败：{exc}")
            print(f"请手动执行：{sys.executable} -m pip install -r \"{req}\"")
            raise SystemExit(1)


_ensure_dependencies()

# 使用绝对导入，保证"直接运行"与"包方式运行"均可正常加载各模块
from app import hotkey
from app.backup import backup_db
from app.database import Database
from app.parser import md_parser
from app.ui.main_window import MainWindow
from app.ui.progress_dialog import ProgressDialog


def _run_builtin_import(app: MainWindow, db: Database) -> None:
    """首次启动：解析内置手册并导入（进度条窗口，导入完成后刷新树）"""
    try:
        manual = md_parser.parse_file(config.builtin_manual_path())
    except Exception as exc:
        # 打包态资源缺失等异常必须可见，避免窗口程序静默失败（如 2026-08-12 修复的路径 Bug）
        messagebox.showerror(
            "内置手册导入失败",
            f"无法读取内置手册：\n{exc}\n\n请重新安装/打包后重试。",
            parent=app)
        return
    total = manual.count_entries()
    dlg = ProgressDialog(app, total=total)
    try:
        result = md_parser.import_manual(db, manual, "视觉风格分类",
                                         progress_cb=dlg.update_progress,
                                         link_domains=["视觉风格分类"])  # 内置手册归入视觉风格分类根目录
        db.set_meta(config.META_BUILTIN_IMPORTED, config.BUILTIN_MANUAL_VERSION)
        print(f"[导入完成] 条目 {result['entries']} 条，分类 新建{result['categories']}/复用{result.get('categories_shared', 0)} 个")
    finally:
        dlg.finish()
    app.refresh_domains()
    app.toast("✅ 内置手册导入完成")


def _ensure_initial_db() -> None:
    """打包态首次运行：把内嵌的完整最新数据库复制到 exe 同目录 data/（2026-08-18 第023条新增）。

    说明：EXE 内嵌了打包时的完整开发库（resources/builtin_prompts.db）。当 exe 同目录
    尚无 data/prompts.db（首次运行或拷贝到新位置）时，直接复制该库，使用户开箱即用
    最新数据（含全部根目录/分类/条目）。已有 data/prompts.db 时不覆盖（保护用户数据）。
    开发态直接使用主库，本方法不做任何操作。
    """
    if not config.is_frozen():
        return
    db_file = os.path.join(config.data_dir(), config.DB_FILE_NAME)
    if os.path.isfile(db_file):
        return  # 已有数据，不覆盖
    src = config.builtin_db_path()
    if not os.path.isfile(src):
        print("[启动] 未找到内置完整数据库资源，将走内置手册导入流程")
        return
    try:
        os.makedirs(config.data_dir(), exist_ok=True)
        shutil.copy2(src, db_file)
        print("[启动] 已从内置资源复制最新完整数据库")
    except Exception as exc:
        print(f"[警告] 内置数据库复制失败：{exc}（将自动重建内置手册库）")


def main() -> None:
    parser = argparse.ArgumentParser(description="PromptSprite 提示精灵")
    parser.add_argument("--smoke", action="store_true",
                        help="冒烟测试：启动数秒后自动退出")
    args = parser.parse_args()

    # 0. 打包态首次运行：复制内嵌完整数据库（2026-08-18 第023条新增）
    _ensure_initial_db()

    # 1. 启动静默备份
    backup = backup_db()
    backup_warning = backup["error"] if not backup["ok"] else ""

    # 2. 数据库初始化（首次自动建表 + 预置根目录）
    db = Database()
    db.seed_preset_domains()

    # 3. 主窗口
    app = MainWindow(db, startup_warning=backup_warning)

    # 4. 内置手册导入：首次启动或版本升级时自动重建（在事件循环内执行，保证进度条正常刷新）
    stored_version = db.get_meta(config.META_BUILTIN_IMPORTED)
    if stored_version != config.BUILTIN_MANUAL_VERSION:
        # 2026-08-18（P0-2 修复）：不再 reset_content() 清库——导入已改为"非破坏性合并"
        # （分类按名复用、条目按名 upsert），版本升级不再清空任何用户数据。
        app.after(300, lambda: _run_builtin_import(app, db))

    # 4.1 老版本数据迁移向导（2026-08-29，M5）：
    # 存在未归属根目录（旧库升级后尚未分配项目类别）且用户未取消过 → 自动弹出
    if (not args.smoke and db.list_unassigned_domains()
            and db.get_meta(config.META_MIGRATE_WIZARD_DISMISSED) != "1"):
        app.after(900, app._open_migrate_wizard)

    # 5. 全局热键（失败降级，不影响主功能）
    if not args.smoke:
        ok = hotkey.register_global_hotkey(
            lambda: app.after(0, app.show_and_focus_search))
        if not ok:
            print("[警告] 全局热键注册失败：请以管理员身份运行，或将该程序加入杀毒软件白名单")

    # 6. 系统托盘（阶段七；失败降级，不影响主功能）
    tray_icon = None
    if not args.smoke:
        try:
            tray_icon = hotkey.start_tray(
                lambda: app.after(0, app.show_and_focus_search),
                lambda: app.after(0, app.destroy))
        except Exception as exc:
            print(f"[警告] 系统托盘启动失败：{exc}")
            tray_icon = None

    if args.smoke:
        app.after(5000, app.quit)  # 结束事件循环（quit 优于 destroy，避免后台定时器报错）

    app.mainloop()
    try:
        app.destroy()  # mainloop 结束后统一销毁窗口
    except Exception:
        pass
    if tray_icon:
        hotkey.stop_tray(tray_icon)
    hotkey.unregister_all()
    db.close()


if __name__ == "__main__":
    main()
