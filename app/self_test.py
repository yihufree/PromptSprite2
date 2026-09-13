# -*- coding: utf-8 -*-
"""
self_test.py - PromptSprite 统一自测入口
创建日期：2026-08-18（P2-6 新增）

用法：
  python -m app.self_test             # 运行全部模块自测（不打开 GUI）
  python -m app.self_test --smoke     # 追加 GUI 冒烟测试（打开窗口约 5 秒自动退出）
"""
import os
import subprocess
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# (模块, 描述) —— 按依赖顺序排列
_MODULES = [
    ("app.database", "数据库层自测"),
    ("app.backup", "备份模块自测"),
    ("app.incremental_backup", "增量备份模块自测"),  # 2026-08-29：纳入统一自测入口
    ("app.parser.json_io", "JSON 导入导出自测"),      # 2026-09-13（4-a）：v5 四层路径 + 字段定义随包
    ("app.parser.excel_io", "Excel 导入导出自测"),      # 2026-09-13：追加『项目类别』列往返 + 旧模板兼容
    ("app.parser.hierarchy_import", "层级导入器自测"),  # 2026-09-13（4-b）：源解析 + 层级结构化 + 载荷
    ("app.parser.fetcher", "联网抓取层自测"),        # 2026-09-14（5-a）：本地 HTTP 服务 + 注入假响应
    ("app.parser.md_parser", "MD 解析器自测"),
]


def main() -> None:
    smoke = "--smoke" in sys.argv
    failed = []
    for module, desc in _MODULES:
        print(f"\n=== [{desc}] python -m {module} ===")
        r = subprocess.run([sys.executable, "-m", module], cwd=_PROJECT_ROOT)
        if r.returncode != 0:
            failed.append(module)
    if smoke:
        print("\n=== [GUI 冒烟测试] python app/main.py --smoke ===")
        r = subprocess.run(
            [sys.executable, os.path.join(_PROJECT_ROOT, "app", "main.py"), "--smoke"],
            cwd=_PROJECT_ROOT)
        if r.returncode != 0:
            failed.append("app.main --smoke")
    if failed:
        print(f"\n✗ 以下自测失败：{failed}")
        sys.exit(1)
    print("\n=== 全部自测通过 ===")


if __name__ == "__main__":
    main()
