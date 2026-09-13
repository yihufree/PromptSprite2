# -*- coding: utf-8 -*-
"""
generate_icon.py - 由源图标 Icons/PSicon.png 生成 build/app.ico（阶段八：打包）
用法：python build/generate_icon.py
"""
import os

from PIL import Image

# 源图标（用户提供）与输出 ico 的默认路径
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(os.path.dirname(HERE), "Icons", "PSicon.png")
DEFAULT_OUT = os.path.join(HERE, "app.ico")


def generate(src_png: str = DEFAULT_SRC, out_ico: str = DEFAULT_OUT) -> str:
    """将 PNG 图标转换为多尺寸 ICO（PyInstaller 所需格式）"""
    if not os.path.isfile(src_png):
        raise FileNotFoundError(f"未找到源图标：{src_png}")
    img = Image.open(src_png).convert("RGBA")
    os.makedirs(os.path.dirname(out_ico), exist_ok=True)
    img.save(out_ico, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                    (128, 128), (256, 256)])
    return out_ico


if __name__ == "__main__":
    print("生成图标：", generate())
