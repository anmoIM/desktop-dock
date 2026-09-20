"""桌面收录 · 安装程序

运行后让用户选择安装目录，然后把程序文件释放到该目录，
并在桌面创建快捷方式。安装包内部已经带好了主程序与默认配置。
"""

import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

APP_NAME = "桌面收录"
APP_EXE = "桌面收录.exe"


def payload_dir():
    """安装包内释放出来的资源目录（打包后是临时解包目录）。"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def default_dir():
    return os.path.join(os.path.expanduser("~"), "DesktopDock")


def create_shortcut(exe_path):
    """用 PowerShell 建桌面快捷方式，免去额外依赖。"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    link = os.path.join(desktop, APP_NAME + ".lnk")
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.WorkingDirectory='%s';"
        "$s.Description='%s';$s.Save()"
        % (link, exe_path, os.path.dirname(exe_path), "桌面图标收录面板")
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script], capture_output=True
    )
    return os.path.exists(link)


class Installer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME + " 安装程序")
        self.root.geometry("500x210")
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap(default="")
        except tk.TclError:
            pass

        tk.Label(self.root, text="选择安装目录", font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor="w", padx=18, pady=(16, 2)
        )
        tk.Label(
            self.root,
            text="程序文件会释放到该目录，并在桌面创建快捷方式。",
            fg="#666",
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", padx=18)

        row = tk.Frame(self.root)
        row.pack(fill="x", padx=18, pady=(14, 0))
        self.path_var = tk.StringVar(value=default_dir())
        entry = tk.Entry(row, textvariable=self.path_var, font=("Microsoft YaHei UI", 10))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        tk.Button(row, text="浏览...", command=self.browse, width=8).pack(
            side="left", padx=(8, 0)
        )

        self.status = tk.Label(
            self.root,
            text="确认目录后点「开始安装」",
            fg="#2f7d32",
            font=("Microsoft YaHei UI", 9),
        )
        self.status.pack(anchor="w", padx=18, pady=(12, 0))

        buttons = tk.Frame(self.root)
        buttons.pack(pady=14)
        self.install_button = tk.Button(
            buttons, text="开始安装", width=16, height=2, command=self.install
        )
        self.install_button.pack(side="left", padx=6)
        tk.Button(buttons, text="退出", width=10, height=2, command=self.root.destroy).pack(
            side="left", padx=6
        )

    def browse(self):
        chosen = filedialog.askdirectory(
            title="选择安装目录", initialdir=self.path_var.get() or os.path.expanduser("~")
        )
        if chosen:
            self.path_var.set(os.path.normpath(chosen))

    def install(self):
        target = self.path_var.get().strip().strip('"')
        if not target:
            messagebox.showwarning(APP_NAME, "请先选择安装目录。")
            return
        exe_path = os.path.join(target, APP_EXE)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, "无法创建目录：%s" % exc)
            return

        if os.path.exists(exe_path):
            if not messagebox.askyesno(APP_NAME, "该目录已有同名文件，是否覆盖安装？"):
                return

        self.install_button.config(state="disabled")
        self.status.config(text="正在释放程序文件...")
        self.root.update()
        try:
            shutil.copy2(os.path.join(payload_dir(), APP_EXE), exe_path)

            # 带上默认配置（已有配置就不覆盖，避免抹掉用户设置）
            data_dir = os.path.join(target, "data")
            os.makedirs(data_dir, exist_ok=True)
            config = os.path.join(data_dir, "config.json")
            source_config = os.path.join(payload_dir(), "config.json")
            if os.path.exists(source_config) and not os.path.exists(config):
                shutil.copy2(source_config, config)

            self.status.config(text="正在创建桌面快捷方式...")
            self.root.update()
            shortcut_ok = create_shortcut(exe_path)
        except OSError as exc:
            self.install_button.config(state="normal")
            self.status.config(text="安装失败")
            messagebox.showerror(APP_NAME, "安装失败：%s" % exc)
            return

        self.status.config(text="安装完成：%s" % target)
        note = "已创建桌面快捷方式。" if shortcut_ok else "桌面快捷方式创建失败，可手动发送。"
        if messagebox.askyesno(
            APP_NAME,
            "安装完成！\n\n目录：%s\n%s\n\n若托盘里还有旧程序在运行，"
            "请先从托盘退出（否则新程序会因单实例限制直接退出）。\n\n是否立即启动？"
            % (target, note),
        ):
            try:
                os.startfile(exe_path)
            except OSError:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Installer().run()