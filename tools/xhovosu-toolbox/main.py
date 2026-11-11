#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XhovoSU 一键 ROOT 工具箱

前端用 pywebview 载入 WebView2，UI 是 HTML/CSS，能动用 backdrop-filter
和 SVG 滤镜做液态玻璃、流体、抖动缩放；后端是纯 Python，负责 adb/fastboot。

打包后是单个 exe，不需要用户装任何运行库（WebView2 在 Win10/11 自带）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import zipfile

APP_TITLE = "XhovoSU 定制工具箱"
APP_VERSION = "1.0.0"

KMI_TABLE = [
    ("android17-6.18", "6.18"),
    ("android16-6.12", "6.12"),
    ("android15-6.6", "6.6"),
    ("android14-6.1", "6.1"),
    ("android14-6.5.15", "5.15"),
    ("android13-5.15", "5.15"),
    ("android13-5.10", "5.10"),
    ("android12-5.10", "5.10"),
]

PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"


def base_dir():
    """打包后资源在 _MEIPASS，开发时在脚本目录。"""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def exe_dir():
    """exe 所在目录，用来放下载的 platform-tools。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def run(cmd, timeout=60, cwd=None):
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
        return 127, "命令不存在: " + str(cmd[0])
    except subprocess.TimeoutExpired:
        return 124, "命令超时"
    except Exception as e:
        return 1, str(e)


def find_tool(name):
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in (exe_dir(), base_dir()):
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    # platform-tools 子目录
    for d in (exe_dir(), base_dir()):
        p = os.path.join(d, "platform-tools", exe)
        if os.path.isfile(p):
            return p
    return shutil.which(name) or ""


class Api:
    """暴露给前端的方法。每个方法返回 dict，前端按 {ok, msg, ...} 处理。"""

    def __init__(self):
        self.adb = find_tool("adb")
        self.fastboot = find_tool("fastboot")
        self.serial = ""
        self.info = {}
        self.image_path = ""
        self.logs = []
        self.lock = threading.Lock()

    # ---------------------------------------------------------- 日志
    def _log(self, msg):
        line = "[{}] {}".format(time.strftime("%H:%M:%S"), msg)
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > 800:
                self.logs = self.logs[-600:]
        print(line)

    def pull_logs(self, since=0):
        with self.lock:
            return {"ok": True, "lines": self.logs[since:], "total": len(self.logs)}

    # ---------------------------------------------------------- 1 环境
    def check_env(self):
        self._log("=" * 52)
        self._log("环境自检")
        self.adb = find_tool("adb")
        self.fastboot = find_tool("fastboot")

        adb_ver = ""
        if self.adb:
            _, out = run([self.adb, "version"])
            adb_ver = out.splitlines()[0].strip() if out.strip() else "ok"
            self._log("  adb: " + adb_ver)
        else:
            self._log("  [缺失] adb")

        fb_ver = ""
        if self.fastboot:
            _, out = run([self.fastboot, "--version"])
            fb_ver = out.splitlines()[0].strip() if out.strip() else "ok"
            self._log("  fastboot: " + fb_ver)
        else:
            self._log("  [缺失] fastboot")

        return {
            "ok": bool(self.adb and self.fastboot),
            "adb": adb_ver,
            "fastboot": fb_ver,
            "adb_path": self.adb,
            "fastboot_path": self.fastboot,
        }

    def download_tools(self):
        self._log("下载 platform-tools…")
        target = os.path.join(exe_dir(), "platform-tools.zip")
        try:
            urllib.request.urlretrieve(PLATFORM_TOOLS_URL, target)
        except Exception as e:
            self._log("[失败] " + str(e))
            return {"ok": False, "msg": str(e)}

        self._log("  解压…")
        try:
            with zipfile.ZipFile(target) as z:
                z.extractall(exe_dir())
            os.remove(target)
        except Exception as e:
            self._log("[失败] 解压出错: " + str(e))
            return {"ok": False, "msg": str(e)}

        self.adb = find_tool("adb")
        self.fastboot = find_tool("fastboot")
        self._log("  完成 adb={}".format(bool(self.adb)))
        return {"ok": bool(self.adb and self.fastboot), "adb_path": self.adb}

    # ---------------------------------------------------------- 2 设备
    def scan_device(self):
        self._log("=" * 52)
        self._log("设备识别")
        if not self.adb:
            self._log("[失败] 没有 adb")
            return {"ok": False, "msg": "没有 adb"}

        _, out = run([self.adb, "devices"])
        self._log("  adb devices → " + " ".join(out.split()))
        serials = [l.split("\t")[0] for l in out.splitlines()[1:] if "\t" in l and "device" in l]
        if not serials:
            self._log("[失败] 没有已授权设备")
            return {"ok": False, "msg": "没检测到设备，请连 USB 并允许调试"}

        self.serial = serials[0]
        self._log("  设备: " + self.serial)

        def prop(k):
            _, v = run([self.adb, "-s", self.serial, "shell", "getprop", k], timeout=8)
            return v.strip()

        model = prop("ro.product.model")
        brand = prop("ro.product.brand")
        android = prop("ro.build.version.release")
        sdk = prop("ro.build.version.sdk")
        slot = prop("ro.boot.slot_suffix").strip("_")
        platform = prop("ro.board.platform")

        _, kr = run([self.adb, "-s", self.serial, "shell", "uname", "-r"])
        kernel = kr.strip()
        kmi = self._match_kmi(kernel)

        _, su_out = run([self.adb, "-s", self.serial, "shell", "su", "-c", "id"], timeout=8)
        rooted = "uid=0" in su_out

        _, pm_out = run([self.adb, "-s", self.serial, "shell", "pm", "list", "packages",
                         "com.HxovoSU.yydsHx"])
        installed = "com.HxovoSU.yydsHx" in pm_out

        self.info = {
            "serial": self.serial,
            "brand": brand,
            "model": model,
            "android": android,
            "sdk": sdk,
            "slot": slot,
            "is_ab": bool(slot),
            "platform": platform,
            "kernel": kernel,
            "kmi": kmi,
            "rooted": rooted,
            "installed": installed,
        }

        self._log("  机型      {} {}".format(brand, model))
        self._log("  Android   {} (SDK {})".format(android, sdk))
        self._log("  芯片      {}".format(platform))
        self._log("  内核      {}".format(kernel))
        self._log("  KMI       {}".format(kmi or "未匹配"))
        self._log("  分区      {}".format("A/B " + slot if slot else "仅 A"))
        self._log("  ROOT      {}".format("已 ROOT" if rooted else "未 ROOT"))
        self._log("  面具      {}".format("已安装" if installed else "未安装"))

        return {"ok": bool(kmi), "info": self.info, "msg": "" if kmi else "内核不在支持列表"}

    @staticmethod
    def _match_kmi(kernel_release):
        for name, key in KMI_TABLE:
            if re.search(r"(^|[.\-])" + re.escape(key) + r"([.\-]|$)", kernel_release):
                return name
        return ""

    # ---------------------------------------------------------- 3 镜像
    def set_image(self, path):
        if not path or not os.path.isfile(path):
            return {"ok": False, "msg": "文件不存在"}
        self.image_path = path
        size = os.path.getsize(path)
        self._log("已选择镜像: {}  {:.2f} MB".format(os.path.basename(path), size / 1024 / 1024))
        return {"ok": True, "name": os.path.basename(path), "size": size}

    def dump_image(self, save_to):
        if not self.info.get("rooted"):
            self._log("[失败] 设备还没 ROOT，无法从分区导出")
            return {"ok": False, "msg": "设备还没 ROOT，请用官方固件里的 init_boot.img"}
        if not save_to:
            return {"ok": False, "msg": "没有指定保存路径"}

        slot = "" if not self.info.get("is_ab") else self.info.get("slot", "")
        name = ("init_boot" if self.info.get("is_ab") else "boot") + slot
        self._log("从 /dev/block/by-name/{} 导出…".format(name))

        _, out = run([self.adb, "-s", self.serial, "shell", "su", "-c",
                      "dd if=/dev/block/by-name/{} of=/sdcard/{}.img".format(name, name)],
                     timeout=180)
        self._log("  " + " ".join(out.split())[:200])

        _, out = run([self.adb, "-s", self.serial, "pull", "/sdcard/{}.img".format(name), save_to],
                     timeout=300)
        self._log("  " + " ".join(out.split())[:200])
        run([self.adb, "-s", self.serial, "shell", "rm", "-f", "/sdcard/{}.img".format(name)],
            timeout=20)

        if os.path.isfile(save_to):
            self.image_path = save_to
            size = os.path.getsize(save_to)
            self._log("导出完成 {:.2f} MB".format(size / 1024 / 1024))
            return {"ok": True, "name": os.path.basename(save_to), "size": size}
        return {"ok": False, "msg": "导出失败"}

    # ---------------------------------------------------------- 4 修补
    def patch(self):
        self._log("=" * 52)
        self._log("修补 init_boot")
        if not self.image_path:
            return {"ok": False, "msg": "先选择 init_boot.img"}

        magiskboot = find_tool("magiskboot")
        if not magiskboot:
            self._log("[缺少] magiskboot.exe")
            self._log("  把它放到程序目录后重试；")
            self._log("  或用 Magisk App 的「安装 → 选择并修补一个文件」，")
            self._log("  再把生成的 magisk_patched-*.img 用第 4 步刷入。")
            return {"ok": False, "msg": "缺少 magiskboot", "needMagiskboot": True}

        work = os.path.join(exe_dir(), "_xh_patch")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        shutil.copy2(self.image_path, os.path.join(work, "init_boot.img"))

        self._log("  解包…")
        rc, out = run([magiskboot, "unpack", "init_boot.img"], cwd=work, timeout=180)
        for line in out.splitlines()[:12]:
            self._log("    " + line)
        if rc != 0:
            return {"ok": False, "msg": "解包失败"}

        kernel = os.path.join(work, "kernel")
        if not os.path.isfile(kernel):
            return {"ok": False, "msg": "解包后没有 kernel 段"}

        ksize = os.path.getsize(kernel) / 1024 / 1024
        self._log("  kernel 段 {:.2f} MB".format(ksize))
        self._log("")
        self._log("内核替换需要对应 KMI（{}）的内核二进制。".format(self.info.get("kmi") or "未知"))
        self._log("把那颗内核放到 {} 后继续。".format(kernel))

        return {"ok": True, "kernelPath": kernel, "kernelSize": ksize, "workDir": work}

    # ---------------------------------------------------------- 5 刷入
    def reboot_bootloader(self):
        self._log("重启到 bootloader…")
        run([self.adb, "-s", self.serial, "reboot", "bootloader"], timeout=20)
        for i in range(20):
            time.sleep(1)
            _, out = run([self.fastboot, "devices"], timeout=10)
            if out.strip():
                self._log("  fastboot 已连接")
                return {"ok": True}
        return {"ok": False, "msg": "没检测到 fastboot 设备"}

    def flash(self):
        self._log("=" * 52)
        self._log("刷入 init_boot")
        if not self.image_path:
            return {"ok": False, "msg": "没有镜像"}
        if not self.fastboot:
            return {"ok": False, "msg": "没有 fastboot"}

        _, out = run([self.fastboot, "devices"], timeout=10)
        if not out.strip():
            return {"ok": False, "msg": "fastboot 没检测到设备"}

        target = "init_boot" if self.info.get("is_ab") else "boot"
        self._log("  目标分区 " + target)
        rc, out = run([self.fastboot, "flash", target, self.image_path], timeout=600)
        for line in out.splitlines():
            self._log("    " + line)
        if rc != 0 or "FAILED" in out.upper():
            return {"ok": False, "msg": "刷入失败"}

        self._log("  刷入成功，重启…")
        run([self.fastboot, "reboot"], timeout=30)
        return {"ok": True}

    # ---------------------------------------------------------- 6 面具
    def install_app(self, apk_path):
        self._log("=" * 52)
        self._log("安装面具")
        if not apk_path or not os.path.isfile(apk_path):
            return {"ok": False, "msg": "APK 不存在"}

        for _ in range(25):
            _, out = run([self.adb, "devices"], timeout=10)
            if "device" in out:
                break
            time.sleep(2)
        else:
            return {"ok": False, "msg": "设备没上线"}

        self._log("  推送并安装…")
        rc, out = run([self.adb, "install", "-r", "-g", apk_path], timeout=300)
        for line in out.splitlines()[-6:]:
            self._log("    " + line)
        if "Success" in out:
            self._log("  安装成功")
            return {"ok": True}
        return {"ok": False, "msg": "安装未成功"}

    # ---------------------------------------------------------- 文件对话框
    def pick_file(self, kind):
        import webview
        if kind == "image":
            types = ("镜像文件 (*.img)", "所有文件 (*.*)")
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=types)
        elif kind == "apk":
            types = ("APK (*.apk)", "所有文件 (*.*)")
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=types)
        else:
            result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG, save_filename="init_boot.img")
        if not result:
            return {"ok": False}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return {"ok": True, "path": path}


def main():
    import webview

    api = Api()
    ui = os.path.join(base_dir(), "ui", "index.html")

    window = webview.create_window(
        APP_TITLE,
        ui,
        js_api=api,
        width=1060,
        height=760,
        min_size=(960, 700),
        background_color="#0b0d12",
        frameless=False,
        easy_drag=False,
        text_select=False,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(os.path.join(exe_dir(), "toolbox-error.log"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
