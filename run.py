# -*- coding: utf-8 -*-
"""
run.py - PromptSprite 启动器（在项目根目录双击或 `python run.py` 运行）
创建日期：2026-08-12（阶段八：打包交付与测试）

说明：主程序为 app/main.py（使用包内相对导入），需以包方式启动，
本启动器提供 `python run.py` 的便捷入口（等效于 `python -m app.main`）。
"""
from app.main import main

if __name__ == "__main__":
    main()
