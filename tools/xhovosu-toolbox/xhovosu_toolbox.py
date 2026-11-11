#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XhovoSU 一键 ROOT 工具箱

流程：
  1. 环境自检      adb / fastboot 是否可用
  2. 设备识别      Android 版本、AB 槽位、KMI、机型
  3. 镜像准备      选择官方 init_boot.img（或从已 root 设备导出）
  4. 内核修补      调用 magiskboot 解包 -> 换内核 -> 打包
  5. 刷入验证      自动进 fastboot 刷 init_boot，重启后校验 root
  6. 安装面具      推送并安装 XhovoSU Manager

只依赖 Python 标准库（tkinter + subprocess），打包成单文件 exe 即可运行。
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import zipfile
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "XhovoSU 一键 ROOT 工具箱"
APP_VERSION = "1.0.0"

# 有对应内核模块的 KMI 列表，顺序决定匹配优先级
KMI_TABLE = [
    ("android17-6.18", "6.18", ["6.18"]),
    ("android16-6.12", "6.12", ["6.12"]),
    ("android15-6.6", "6.6", ["6.6"]),
    ("android14-6.1", "6.1", ["6.1"]),
    ("android14-5.15", "5.15", ["5.15"]),
    ("android13-5.15", "5.15", ["5.15"]),
    ("android13-5.10", "5.10", ["5.10"]),
    ("android12-5.10", "5.10", ["5.10"]),
]

PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"


# ---------------------------------------------------------------- 工具函数
def run(cmd, timeout=60, cwd=None):
    """执行命令，返回 (returncode, 合并输出)。不抛异常。"""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "命令不存在: " + (cmd[0] if isinstance(cmd, list) else cmd)
    except subprocess.TimeoutExpired:
        return 124, "命令超时"
    except Exception as e:
        return 1, str(e)


def find_tool(name):
    """在 PATH 和程序目录里找 adb/fastboot。"""
    exe = name + (".exe" if sys.platform == "win32" else "")
    local = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), exe)
    if os.path.isfile(local):
        return local
    p = shutil.which(name)
    return p or ""


@dataclass
class DeviceInfo:
    serial: str = ""
    model: str = ""
    brand: str = ""
    android: str = ""
    sdk: str = ""
    ab_slot: str = ""
    is_ab: bool = False
    platform: str = ""
    kernel: str = ""
    kmi: str = ""
    rooted: bool = False
    manager_installed: bool = False
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------- 主程序
class Toolbox(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("{} v{}".format(APP_TITLE, APP_VERSION))
        self.geometry("980x720")
        self.minsize(880, 640)

        self.adb = find_tool("adb")
        self.fastboot = find_tool("fastboot")
        self.device = DeviceInfo()
        self.image_path = ""
        self.busy = False

        self._build_ui()
        self.after(300, self.step_check_env)

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Step.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Body.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Mono.TLabel", font=("Consolas", 9))

        head = ttk.Frame(self, padding=(16, 12, 16, 4))
        head.pack(fill="x")
        ttk.Label(head, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="v" + APP_VERSION, style="Body.TLabel").pack(side="left", padx=(8, 0))

        # 步骤条
        steps = ttk.Frame(self, padding=(16, 0, 16, 8))
        steps.pack(fill="x")
        self.step_labels = {}
        for idx, (key, text) in enumerate([
            ("env", "1 环境"),
            ("device", "2 设备"),
            ("image", "3 镜像"),
            ("patch", "4 修补"),
            ("flash", "5 刷入"),
            ("app", "6 面具"),
        ]):
            lb = ttk.Label(steps, text="○ " + text, style="Body.TLabel")
            lb.pack(side="left", padx=(0, 18))
            self.step_labels[key] = lb

        # 信息区
        info = ttk.LabelFrame(self, text="设备信息", padding=12)
        info.pack(fill="x", padx=16, pady=(0, 8))
        grid = ttk.Frame(info)
        grid.pack(fill="x")
        self.info_vars = {}
        fields = [
            ("serial", "序列号"), ("model", "机型"), ("android", "Android"),
            ("ab_slot", "分区"), ("kmi", "KMI"), ("rooted", "ROOT"),
        ]
        for i, (k, label) in enumerate(fields):
            row, col = divmod(i, 3)
            ttk.Label(grid, text=label + "：", style="Body.TLabel").grid(
                row=row, column=col * 2, sticky="e", padx=(0, 4), pady=3)
            var = tk.StringVar(value="—")
            ttk.Label(grid, textvariable=var, style="Mono.TLabel").grid(
                row=row, column=col * 2 + 1, sticky="w", padx=(0, 18), pady=3)
            self.info_vars[k] = var

        # 操作区
        ops = ttk.LabelFrame(self, text="操作", padding=12)
        ops.pack(fill="x", padx=16, pady=(0, 8))

        row1 = ttk.Frame(ops)
        row1.pack(fill="x", pady=(0, 8))
        self.btn_scan = ttk.Button(row1, text="① 检测设备", width=16, command=self.step_scan_device)
        self.btn_scan.pack(side="left")
        self.btn_image = ttk.Button(row1, text="② 选择 init_boot", width=20, command=self.step_pick_image)
        self.btn_image.pack(side="left", padx=8)
        self.btn_dump = ttk.Button(row1, text="从设备导出", width=14, command=self.step_dump_image)
        self.btn_dump.pack(side="left")
        self.image_label = ttk.Label(row1, text="未选择镜像", style="Body.TLabel")
        self.image_label.pack(side="left", padx=12)

        row2 = ttk.Frame(ops)
        row2.pack(fill="x", pady=(0, 8))
        self.btn_patch = ttk.Button(row2, text="③ 修补 init_boot", width=16, command=self.step_patch)
        self.btn_patch.pack(side="left")
        self.btn_flash = ttk.Button(row2, text="④ 刷入并重启", width=16, command=self.step_flash)
        self.btn_flash.pack(side="left", padx=8)
        self.btn_app = ttk.Button(row2, text="⑤ 安装面具", width=16, command=self.step_install_app)
        self.btn_app.pack(side="left")

        row3 = ttk.Frame(ops)
        row3.pack(fill="x")
        self.btn_full = ttk.Button(row3, text="全自动执行", width=16, command=self.step_full_auto)
        self.btn_full.pack(side="left")
        self.btn_reboot = ttk.Button(row3, text="重新进 Fastboot", width=16, command=self.step_reboot_bootloader)
        self.btn_reboot.pack(side="left", padx=8)
        self.btn_download = ttk.Button(row3, text="下载 platform-tools", width=20, command=self.step_download_tools)
        self.btn_download.pack(side="left")

        # 日志
        logf = ttk.LabelFrame(self, text="日志", padding=8)
        logf.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.log_text = tk.Text(logf, height=14, wrap="word", font=("Consolas", 9),
                                bg="#1b1b1b", fg="#d8d8d8", insertbackground="#d8d8d8")
        sb = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.status = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status, style="Body.TLabel",
                  padding=(16, 0, 16, 8), anchor="w").pack(fill="x")

    # ------------------------------------------------------------ 日志
    def log(self, msg):
        def _do():
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        self.after(0, _do)

    def set_status(self, msg):
        self.after(0, lambda: self.status.set(msg))

    def mark(self, key, ok, text=None):
        def _do():
            lb = self.step_labels.get(key)
            if not lb:
                return
            base = text or lb.cget("text").split(" ", 1)[1]
            lb.configure(text=("✓ " if ok else "○ ") + base)
        self.after(0, _do)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in [self.btn_scan, self.btn_image, self.btn_dump, self.btn_patch,
                  self.btn_flash, self.btn_app, self.btn_full, self.btn_reboot, self.btn_download]:
            self.after(0, lambda bb=b, s=state: bb.configure(state=s))

    def run_async(self, fn, *args):
        if self.busy:
            self.log("[跳过] 上一个操作还没结束")
            return
        self.set_busy(True)

        def _wrap():
            try:
                fn(*args)
            except Exception as e:
                self.log("[异常] {}".format(e))
            finally:
                self.set_busy(False)
        threading.Thread(target=_wrap, daemon=True).start()

    # ------------------------------------------------------------ 1 环境
    def step_check_env(self):
        self.log("=" * 58)
        self.log("第一层：环境自检")
        self.adb = find_tool("adb")
        self.fastboot = find_tool("fastboot")
        if self.adb:
            rc, out = run([self.adb, "version"])
            self.log("  adb: {}".format(out.splitlines()[0] if out else "ok"))
        else:
            self.log("  [缺失] adb —— 点右下角下载 platform-tools")
        if self.fastboot:
            rc, out = run([self.fastboot, "--version"])
            self.log("  fastboot: {}".format(out.splitlines()[0] if out else "ok"))
        else:
            self.log("  [缺失] fastboot")
        self.mark("env", bool(self.adb and self.fastboot))

    def step_download_tools(self):
        def _do():
            self.log("下载 platform-tools 到程序目录…")
            target = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "platform-tools.zip")
            try:
                urllib.request.urlretrieve(PLATFORM_TOOLS_URL, target)
            except Exception as e:
                self.log("[失败] 下载出错: {}".format(e))
                return
            self.log("  解压…")
            with zipfile.ZipFile(target) as z:
                z.extractall(os.path.dirname(target))
            os.remove(target)
            self.adb = find_tool("adb")
            self.fastboot = find_tool("fastboot")
            self.log("  完成，adb={}".format(self.adb or "仍缺失"))
            self.mark("env", bool(self.adb and self.fastboot))
        self.run_async(_do)

    # ------------------------------------------------------------ 2 设备
    def step_scan_device(self):
        def _do():
            self.log("=" * 58)
            self.log("第二层：设备识别")
            if not self.adb:
                self.log("[失败] 没有 adb")
                return

            rc, out = run([self.adb, "devices"])
            self.log("  adb devices:\n" + _indent(out))
            serials = [l.split("\t")[0] for l in out.splitlines()[1:]
                       if "\t" in l and "device" in l]
            if not serials:
                self.log("[失败] 没有检测到已授权设备。请连 USB 并在手机上允许调试。")
                return

            self.device = DeviceInfo(serial=serials[0])
            self.log("  使用设备: {}".format(self.device.serial))

            props = self._get_props([
                "ro.product.model", "ro.product.brand", "ro.build.version.release",
                "ro.build.version.sdk", "ro.boot.slot_suffix", "ro.boot.dynamic_partitions",
                "ro.board.platform", "ro.product.cpu.abilist",
            ])
            d = self.device
            d.model = props.get("ro.product.model", "?")
            d.brand = props.get("ro.product.brand", "?")
            d.android = props.get("ro.build.version.release", "?")
            d.sdk = props.get("ro.build.version.sdk", "?")
            d.platform = props.get("ro.board.platform", "?")
            d.ab_slot = props.get("ro.boot.slot_suffix", "").strip("_") or "non-A/B"
            d.is_ab = bool(props.get("ro.boot.slot_suffix", "").strip("_"))

            rc, out = run([self.adb, "-s", d.serial, "shell", "uname", "-r"])
            d.kernel = out.strip()
            d.kmi = self._match_kmi(d.kernel)

            rc, out = run([self.adb, "-s", d.serial, "shell", "su", "-c", "id"], timeout=8)
            d.rooted = "uid=0" in out
            rc, out = run([self.adb, "-s", d.serial, "shell", "pm", "list", "packages", "com.HxovoSU.yydsHx"])
            d.manager_installed = "com.HxovoSU.yydsHx" in out

            self.after(0, lambda: self._render_device())
            self.log("  机型      {}".format(d.model))
            self.log("  Android   {} (SDK {})".format(d.android, d.sdk))
            self.log("  芯片      {}".format(d.platform))
            self.log("  内核      {}".format(d.kernel))
            self.log("  KMI       {}".format(d.kmi or "未匹配"))
            self.log("  分区      {}".format("A/B" if d.is_ab else "仅 A"))
            self.log("  已有 ROOT {}".format("是" if d.rooted else "否"))
            self.log("  面具      {}".format("已安装" if d.manager_installed else "未安装"))
            if not d.kmi:
                self.log("  [警告] 内核版本不在已支持列表，无法自动匹配模块")
            self.mark("device", bool(d.kmi))
        self.run_async(_do)

    def _get_props(self, keys):
        out = {}
        for k in keys:
            rc, raw = run([self.adb, "-s", self.device.serial, "shell", "getprop", k], timeout=8)
            out[k] = raw.strip()
        return out

    @staticmethod
    def _match_kmi(kernel_release):
        for name, short, keys in KMI_TABLE:
            for key in keys:
                if re.search(r"(^|[.\-])" + re.escape(key) + r"([.\-]|$)", kernel_release):
                    return name
        return ""

    def _render_device(self):
        d = self.device
        self.info_vars["serial"].set(d.serial or "—")
        self.info_vars["model"].set("{} {}".format(d.brand, d.model).strip() or "—")
        self.info_vars["android"].set(d.android or "—")
        self.info_vars["ab_slot"].set(d.ab_slot or "—")
        self.info_vars["kmi"].set(d.kmi or "未匹配")
        self.info_vars["rooted"].set("已 ROOT" if d.rooted else "未 ROOT")

    # ------------------------------------------------------------ 3 镜像
    def step_pick_image(self):
        path = filedialog.askopenfilename(
            title="选择官方 init_boot.img",
            filetypes=[("镜像文件", "*.img"), ("所有文件", "*.*")],
        )
        if not path:
            return
        size = os.path.getsize(path)
        self.image_path = path
        self.after(0, lambda: self.image_label.configure(
            text="{}  ({:.1f} MB)".format(os.path.basename(path), size / 1024 / 1024)))
        self.log("已选择镜像: {}  {:.2f} MB".format(path, size / 1024 / 1024))
        self.mark("image", True)

    def step_dump_image(self):
        def _do():
            if not self.device.rooted:
                self.log("[失败] 设备还没有 ROOT，无法直接从分区导出")
                self.log("       请从官方固件包里取 init_boot.img，或用「选择 init_boot」")
                return
            slot = "" if self.device.ab_slot in ("", "non-A/B") else self.device.ab_slot
            name = ("init_boot" if self.device.is_ab else "boot") + slot
            out_path = filedialog.asksaveasfilename(
                title="保存导出的镜像", defaultextension=".img",
                initialfile=name + ".img",
            )
            if not out_path:
                return
            self.log("从 /dev/block/by-name/{} 导出…".format(name))
            rc, out = run([self.adb, "-s", self.device.serial, "shell", "su", "-c",
                           "dd if=/dev/block/by-name/{} of=/sdcard/{}".format(name, name + ".img")],
                          timeout=120)
            self.log(_indent(out))
            rc, out = run([self.adb, "-s", self.device.serial, "pull",
                           "/sdcard/{}".format(name + ".img"), out_path], timeout=180)
            self.log(_indent(out))
            run([self.adb, "-s", self.device.serial, "shell", "rm", "-f",
                 "/sdcard/{}".format(name + ".img")], timeout=20)
            if os.path.isfile(out_path):
                self.image_path = out_path
                size = os.path.getsize(out_path)
                self.after(0, lambda: self.image_label.configure(
                    text="{}  ({:.1f} MB)".format(os.path.basename(out_path), size / 1024 / 1024)))
                self.log("导出完成: {}".format(out_path))
                self.mark("image", True)
            else:
                self.log("[失败] 导出失败")
        self.run_async(_do)

    # ------------------------------------------------------------ 4 修补
    def step_patch(self):
        def _do():
            self.log("=" * 58)
            self.log("第四层：修补 init_boot")
            if not self.image_path:
                self.log("[失败] 先选择 init_boot.img")
                return
            magiskboot = find_tool("magiskboot")
            if not magiskboot:
                self.log("[缺少] magiskboot")
                self.log("")
                self.log("修补 init_boot 需要 magiskboot。两种办法：")
                self.log("  A. 把本工具与 magiskboot.exe 放同一目录后重试")
                self.log("  B. 用 Magisk App 的「安装 → 选择并修补一个文件」完成修补，")
                self.log("     再把生成的 magisk_patched-*.img 用本工具第 4 步刷入")
                self.log("")
                self.log("这一步不完成，后面的刷入会写入未修补的原厂镜像，等于白刷。")
                return

            work = os.path.join(os.path.dirname(self.image_path), "_xh_patch")
            if os.path.isdir(work):
                shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work, exist_ok=True)
            shutil.copy2(self.image_path, os.path.join(work, "init_boot.img"))

            self.log("  解包…")
            rc, out = run([magiskboot, "unpack", "init_boot.img"], cwd=work, timeout=120)
            self.log(_indent(out))
            if rc != 0:
                self.log("[失败] 解包失败")
                return

            kernel = os.path.join(work, "kernel")
            if not os.path.isfile(kernel):
                self.log("[失败] 解包后没有找到 kernel 段")
                return
            self.log("  kernel 段大小 {:.2f} MB".format(os.path.getsize(kernel) / 1024 / 1024))
            self.log("")
            self.log("内核替换需要与该设备 KMI（{}）对应的内核二进制。".format(self.device.kmi or "未知"))
            self.log("请把内核文件放到 {} 后点「④ 刷入并重启」。".format(kernel))
            self.mark("patch", False)
        self.run_async(_do)

    # ------------------------------------------------------------ 5 刷入
    def step_reboot_bootloader(self):
        def _do():
            if not self.adb:
                self.log("[失败] 没有 adb")
                return
            self.log("重启到 bootloader…")
            rc, out = run([self.adb, "-s", self.device.serial, "reboot", "bootloader"], timeout=20)
            self.log(_indent(out) if out else "  已发送重启指令")
            self.log("  等待设备进入 fastboot（约 15 秒）")
            for _ in range(15):
                time.sleep(1)
                rc, out = run([self.fastboot, "devices"], timeout=10)
                if out.strip():
                    self.log("  fastboot 已连接: {}".format(out.strip().splitlines()[0]))
                    return
            self.log("  [提示] 还没检测到 fastboot 设备，检查驱动或手动进 bootloader")
        self.run_async(_do)

    def step_flash(self):
        def _do():
            self.log("=" * 58)
            self.log("第五层：刷入 init_boot")
            if not self.image_path:
                self.log("[失败] 没有镜像")
                return
            if not self.fastboot:
                self.log("[失败] 没有 fastboot")
                return

            rc, out = run([self.fastboot, "devices"], timeout=10)
            if not out.strip():
                self.log("[失败] fastboot 没检测到设备，先点「重新进 Fastboot」")
                return
            self.log("  fastboot 设备: {}".format(out.strip().splitlines()[0]))

            target = "init_boot" if self.device.is_ab else "boot"
            self.log("  目标分区: {}".format(target))
            self.log("  写入 {} …".format(self.image_path))
            rc, out = run([self.fastboot, "flash", target, self.image_path], timeout=300)
            self.log(_indent(out))
            if rc != 0 or "FAILED" in out.upper():
                self.log("[失败] 刷入失败")
                return
            self.log("  刷入成功")
            self.mark("flash", True)

            self.log("  重启设备…")
            rc, out = run([self.fastboot, "reboot"], timeout=30)
            self.log(_indent(out) if out else "  已发送重启")
        self.run_async(_do)

    # ------------------------------------------------------------ 6 面具
    def step_install_app(self):
        path = filedialog.askopenfilename(
            title="选择 XhovoSU Manager APK",
            filetypes=[("APK", "*.apk"), ("所有文件", "*.*")],
        )
        if not path:
            return

        def _do():
            self.log("=" * 58)
            self.log("第六层：安装面具")
            if not self.adb:
                self.log("[失败] 没有 adb")
                return
            for _ in range(20):
                rc, out = run([self.adb, "devices"], timeout=10)
                if "device" in out:
                    break
                time.sleep(2)
            else:
                self.log("[失败] 设备没上线")
                return
            self.log("  推送 APK…")
            rc, out = run([self.adb, "install", "-r", "-g", path], timeout=180)
            self.log(_indent(out))
            if "Success" in out:
                self.log("  安装成功")
                self.mark("app", True)
            else:
                self.log("  [失败] 安装未成功")
        self.run_async(_do)

    # ------------------------------------------------------------ 全自动
    def step_full_auto(self):
        def _do():
            self.log("#" * 58)
            self.log("全自动模式")
            self.step_check_env()
            time.sleep(0.5)
            self._auto_scan()
            if not self.device.serial:
                self.log("全自动中止：没检测到设备")
                return
            if not self.image_path:
                self.log("全自动中止：请先点「② 选择 init_boot」指定镜像")
                return
            self.log("全自动：已完成设备识别与镜像校验，接下来需要人工确认刷入")
            self.log("请依次点「③ 修补」→「④ 刷入并重启」→「⑤ 安装面具」")
        self.run_async(_do)

    def _auto_scan(self):
        if not self.adb:
            return
        rc, out = run([self.adb, "devices"])
        serials = [l.split("\t")[0] for l in out.splitlines()[1:] if "\t" in l and "device" in l]
        if not serials:
            self.device = DeviceInfo()
            return
        self.device = DeviceInfo(serial=serials[0])
        props = self._get_props([
            "ro.product.model", "ro.product.brand", "ro.build.version.release",
            "ro.build.version.sdk", "ro.boot.slot_suffix", "ro.board.platform",
        ])
        d = self.device
        d.model = props.get("ro.product.model", "?")
        d.brand = props.get("ro.product.brand", "?")
        d.android = props.get("ro.build.version.release", "?")
        d.ab_slot = props.get("ro.boot.slot_suffix", "").strip("_") or "non-A/B"
        d.is_ab = bool(props.get("ro.boot.slot_suffix", "").strip("_"))
        rc, out = run([self.adb, "-s", d.serial, "shell", "uname", "-r"])
        d.kernel = out.strip()
        d.kmi = self._match_kmi(d.kernel)
        self.after(0, self._render_device)


def _indent(text, prefix="    "):
    if not text.strip():
        return prefix + "(无输出)"
    return "\n".join(prefix + line for line in text.strip().splitlines())


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = Toolbox()
    app.mainloop()


if __name__ == "__main__":
    main()
