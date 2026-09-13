# -*- coding: utf-8 -*-
"""
column_visibility_dialog.py - 「目录隐藏 / 目录显示」对话框
创建日期：2026-09-10（用户要求 2-（2）："项目列"按钮改为"目录隐藏/目录显示"）

用途：选择隐藏或显示主窗口左侧的四个分类列（项目类别、根目录、一级分类、二级分类）。
状态统一用一个整数表达：nav_hidden = 从"项目类别"起、连续隐藏的列数（0~4）。
  · 隐藏对话框：勾选集合 = 前缀 [0, nav_hidden)
      - 勾选靠后的列会自动勾选其前面的列（"选后必先选前"）；
      - 取消某列会自动取消其后面的列。
  · 显示对话框：勾选集合 = 后缀 [nav_hidden, 4)
      - 勾选靠前的列会自动勾选其后面的列（"选前自动选后"）；
      - 取消某列会自动取消其前面的列。

确认后回调 on_apply(nav_hidden)，由主窗口据此显示/隐藏对应列并更新按钮文案。

2026-09-10 补充（用户要求 一）：四个分类项右侧增加"全部隐藏 / 全部显示"两个快捷可选项
（互斥单选）——选中"全部隐藏"即 nav_hidden=4（左侧各项勾选状态自动同步），选中"全部显示"
即 nav_hidden=0（左侧同步）；部分隐藏时两项都不选中。快捷可选项同样需点"确定"后生效。
"""
import customtkinter as ctk

# 分类列名称（顺序 = 主窗口从左到右的列顺序）
NAV_COL_NAMES = ("项目类别", "根目录", "一级分类", "二级分类")


class ColumnVisibilityDialog(ctk.CTkToplevel):
    """目录隐藏 / 目录显示对话框（模态，确认后回调主窗口）。

    master   : 父窗口（主窗口）
    nav_hidden: 当前"连续隐藏的列数"（0~4）
    mode     : "hide" 打开隐藏对话框；"show" 打开显示对话框
    on_apply : 确认回调，参数为新的 nav_hidden
    """

    def __init__(self, master, nav_hidden: int, mode: str, on_apply) -> None:
        super().__init__(master)
        self._nav_hidden = max(0, min(len(NAV_COL_NAMES), int(nav_hidden)))
        self._mode = "show" if mode == "show" else "hide"
        self._on_apply = on_apply
        self._boxes = []
        # 2026-09-10（用户要求 一）：快捷可选项状态 —— 1=全部隐藏、0=全部显示、-1=两者都不选
        self._quick_var = ctk.IntVar(master=self, value=-1)

        self.title("🗂 目录隐藏" if self._mode == "hide" else "🗂 目录显示")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self._build()
        self._sync()
        self._center()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        pad = 20
        head = "目录隐藏" if self._mode == "hide" else "目录显示"
        ctk.CTkLabel(self, text=f"🗂 {head}", font=("Microsoft YaHei", 15, "bold")
                     ).grid(row=0, column=0, padx=pad, pady=(16, 2), sticky="w")
        if self._mode == "hide":
            hint = ("勾选要隐藏的分类列。\n"
                    "勾选靠后的列时会自动勾选其前面的列（例：勾选“一级分类”会同时勾选\n"
                    "“项目类别”和“根目录”）；取消某列会同时取消其后面的列。")
        else:
            hint = ("勾选要显示的分类列。\n"
                    "勾选靠前的列时会自动勾选其后面的列（例：勾选“根目录”会同时勾选\n"
                    "“一级分类”和“二级分类”）；取消某列会同时取消其前面的列。")
        ctk.CTkLabel(self, text=hint, justify="left", text_color="gray",
                     font=("Microsoft YaHei", 11)
                     ).grid(row=1, column=0, columnspan=2, padx=pad, pady=(0, 10), sticky="w")

        # 2026-09-10（用户要求 一）：左＝四个分类列复选项；右＝"全部隐藏 / 全部显示"两个快捷可选项
        opt_row = ctk.CTkFrame(self, fg_color="transparent")
        opt_row.grid(row=2, column=0, columnspan=2, padx=pad, pady=(0, 10), sticky="w")

        box_frame = ctk.CTkFrame(opt_row, fg_color="transparent")
        box_frame.pack(side="left", anchor="n")
        for idx, name in enumerate(NAV_COL_NAMES):
            box = ctk.CTkCheckBox(box_frame, text=name, font=("Microsoft YaHei", 13),
                                  command=lambda i=idx: self._on_toggle(i))
            box.pack(anchor="w", pady=4)
            self._boxes.append(box)

        # 2026-09-10（用户要求 一）：四个分类项右侧的"全部隐藏 / 全部显示"快捷可选项
        # （互斥单选；选中后左侧各项勾选状态自动同步；沿用不点"确定"不生效的既有约定）。
        quick_frame = ctk.CTkFrame(opt_row, fg_color="transparent")
        quick_frame.pack(side="left", anchor="n", padx=(28, 0))
        ctk.CTkLabel(quick_frame, text="快捷选项", font=("Microsoft YaHei", 11),
                     text_color="gray").pack(anchor="w", pady=(0, 2))
        self.quick_hide_radio = ctk.CTkRadioButton(
            quick_frame, text="全部隐藏", font=("Microsoft YaHei", 13),
            variable=self._quick_var, value=1, command=self._on_quick_hide)
        self.quick_hide_radio.pack(anchor="w", pady=6)
        self.quick_show_radio = ctk.CTkRadioButton(
            quick_frame, text="全部显示", font=("Microsoft YaHei", 13),
            variable=self._quick_var, value=0, command=self._on_quick_show)
        self.quick_show_radio.pack(anchor="w", pady=6)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, columnspan=2, sticky="e", padx=pad, pady=(4, 16))
        ctk.CTkButton(btn_row, text="确定", width=96, fg_color="#2E8B57",
                      command=self._apply).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="取消", width=96,
                      command=self.destroy).pack(side="left", padx=4)

    def _on_quick_hide(self) -> None:
        """选中"全部隐藏"：等同四个分类列全部隐藏（左侧各项勾选状态自动同步）。"""
        self._nav_hidden = len(NAV_COL_NAMES)
        self._sync()

    def _on_quick_show(self) -> None:
        """选中"全部显示"：等同四个分类列全部显示（左侧各项勾选状态自动同步）。"""
        self._nav_hidden = 0
        self._sync()

    def _on_toggle(self, idx: int) -> None:
        """勾选/取消某一列 → 按"连续前缀（隐藏）/连续后缀（显示）"规则重算 nav_hidden。"""
        if self._mode == "hide":
            if idx < self._nav_hidden:      # 原为勾选（已隐藏）→ 取消该列及其后面的列
                self._nav_hidden = idx
            else:                           # 原为未勾选 → 勾选该列及其前面的列
                self._nav_hidden = idx + 1
        else:
            if idx >= self._nav_hidden:     # 原为勾选（已显示）→ 取消该列及其前面的列
                self._nav_hidden = idx + 1
            else:                           # 原为未勾选 → 勾选该列及其后面的列
                self._nav_hidden = idx
        self._sync()

    def _sync(self) -> None:
        for i, box in enumerate(self._boxes):
            checked = (i < self._nav_hidden) if self._mode == "hide" else (i >= self._nav_hidden)
            box.select() if checked else box.deselect()
        # 快捷可选项跟随状态：全隐藏→"全部隐藏"；全显示→"全部显示"；部分隐藏→两者都不选
        if self._nav_hidden >= len(NAV_COL_NAMES):
            self._quick_var.set(1)
        elif self._nav_hidden == 0:
            self._quick_var.set(0)
        else:
            self._quick_var.set(-1)

    def _apply(self) -> None:
        try:
            self._on_apply(self._nav_hidden)
        finally:
            self.destroy()

    def _center(self) -> None:
        self.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() - self.winfo_width()) // 2
        y = self.master.winfo_y() + (self.master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
