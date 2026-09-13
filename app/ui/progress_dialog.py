# -*- coding: utf-8 -*-
"""
progress_dialog.py - 导入进度条模态窗口
创建日期：2026-08-12（阶段一：项目初始化与数据地基）

用途：内置手册 / 大文件导入时强制弹出，实时刷新进度，防止界面"未响应"。
"""
import customtkinter as ctk


class ProgressDialog(ctk.CTkToplevel):
    """模态进度条窗口（出现时主界面不可操作，禁止手动关闭）"""

    def __init__(self, master, title="正在导入数据", total=0,
                 message="⏳ 正在解析数据，请稍候..."):
        super().__init__(master)
        self.total = max(int(total), 1)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # 禁止手动关闭

        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=message, font=("Microsoft YaHei", 13)).grid(
            row=0, column=0, padx=28, pady=(18, 6))
        self.progress = ctk.CTkProgressBar(self, width=380)
        self.progress.grid(row=1, column=0, padx=28, pady=6)
        self.progress.set(0)
        self.counter = ctk.CTkLabel(self, text="")
        self.counter.grid(row=2, column=0, padx=28, pady=(0, 18))

        # 居中于主窗口
        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_y() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.attributes("-topmost", True)
        self.lift()

    def update_progress(self, done: int, total: int, name: str) -> None:
        """更新进度；此方法在导入循环中被反复调用"""
        self.progress.set(done / max(total, 1))
        self.counter.configure(text=f"正在导入第 {done} / {total} 条：{name}")
        self.update_idletasks()  # 强制刷新 UI，避免系统显示"未响应"

    def finish(self) -> None:
        """导入完成，关闭窗口"""
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
