# XhovoSU 一键 ROOT 工具箱

Windows 单文件 exe，用来把 XhovoSU Manager 装进 Android 手机。
界面按步骤走，每一步都能看到 adb / fastboot 的原始输出。

## 它能做什么

```
1 环境自检     检查 adb / fastboot，缺了可以直接下载 platform-tools
2 设备识别     读机型、Android 版本、A/B 槽位、内核版本，自动匹配 KMI
3 镜像准备     选择官方 init_boot.img；已 root 的设备可直接从分区导出
4 内核修补     解包 init_boot，检查 kernel 段
5 刷入验证     自动重启进 fastboot，刷 init_boot，再重启
6 安装面具     推送并安装 XhovoSU Manager APK
```

## 它不能做什么

这件事必须说清楚，否则会白折腾：

**工具不会自动生成打过补丁的内核。**

GKI 模式要求把设备原来的 `kernel` 段换成 KernelSU 编译好的内核，而内核必须和设备的 KMI 精确对应（6.1 / 6.6 / 5.15 …），还要和厂商的 defconfig 对齐。这个东西没法在 PC 上凭空生成，只能来自预先编译好的产物。

所以第 4 步的实际行为是：

```
用 magiskboot 解开 init_boot.img
确认 kernel 段存在并显示大小
告诉你需要把对应 KMI 的内核文件放到哪里
```

修补这一步要么用 Magisk App 的「安装 → 选择并修补一个文件」完成，要么用事先编好的内核替换。

## 环境要求

```
Windows 10 / 11
手机开启 USB 调试并已授权
adb / fastboot（工具可下载）
magiskboot.exe（修补环节需要，放同目录）
```

## KMI 对照

工具按内核版本自动匹配，目前覆盖：

```
android17-6.18    6.18
android16-6.12    6.12
android15-6.6     6.6
android14-6.1     6.1
android14-5.15    5.15
android13-5.15    5.15
android13-5.10    5.10
android12-5.10    5.10
```

不在列表里的内核会提示「未匹配」，那种情况需要自己确认对应关系。

## 安全提示

刷 `init_boot` 会改变启动链。刷之前务必确认：

```
镜像是自己设备的官方版本，不要跨机型使用
电量在 50% 以上
已经知道怎么进 fastboot 和 recovery
手边有官方固件包，出问题能刷回去
```

工具在刷入前会打印目标分区名和镜像路径，确认无误再继续。

## 本地运行

```bash
python xhovosu_toolbox.py
```

不需要装任何第三方包，tkinter 是标准库自带的。

## 打包

GitHub Actions 里跑 `Build XhovoSU Toolbox` 工作流，产物在 Artifacts 里。
