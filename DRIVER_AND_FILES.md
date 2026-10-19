# NX 已有随仓库提供的运行包

2026-10-19 新增 `nx/`：包含实际验证的厂家初始化程序、动态库、触发程序、MediaMTX、RTSP 离线补包、安装/探测/回滚工具。匹配的 TZTEK NX / R35.4.1 BSP 上无需再从 AGX 手工拷贝文件，详见 [完整 NX 操作手册](docs/ORIN_NX_ENTRON.md) 和 [运行包来源](nx/bundles/PROVENANCE.md)。

NX 的厂家库通过独立 `LD_LIBRARY_PATH` 加载；不会覆盖 `/usr/lib`。安装时自动校验 2-lane/VC 拓扑，并生成当前接线的配置。底层 BSP、内核、设备树仍需板卡厂商提供，不能从 AGX 直接移植。

以下为原 AGX 清单，其路径、4-lane 配置及全局库加载方式不代表 NX 新部署方式。

---

# 新机器驱动依赖与相关文件清单

## 1. 必须由域控制器 BSP 提供的底层部分

这一层不能靠复制验收脚本解决，也不建议只复制 `/boot/Image` 或单个 DTB。应保留域控制器厂家的完整刷机包、内核源码/补丁和设备树源码。

当前基准机环境：

| 项目 | 当前值 |
|---|---|
| SoC | Jetson AGX Orin / Tegra234 |
| Jetson Linux | R35.4.1 |
| 内核 | `5.10.120-tegra` |
| 板级 DTS 来源 | GEACX1 自定义 `tegra234-p3701-0005-p3737-0000.dts` |
| 视频驱动 | `tegra-camrtc-capture-vi` / NVCSI / V4L2 Media Controller |
| 当前相机节点 | 8 路 `imx185` 占位 sensor subdev，实际接 ISX031 GMSL |

BSP/设备树必须包含：

- Tegra VI、NVCSI、MIPI CSI lane/port/VC 配置。
- 8 路 sensor endpoint 和 `/dev/video0–7` 注册。
- 4 路 I2C mux 通道及 MAX9296 的板级连接。
- PoC/相机电源 GPIO，即当前 `/sys/class/tz_gpio/camera_{0_1,2_3,4_5,6_7}-power/value`。
- 外触发使用的 CPLD/串口，即当前 `/dev/ttyTHS1`。
- 与真实输出一致的 `active_w/active_h`、像素格式、lane 数、pixel clock、embedded metadata 设置。

当前机器的厂家初始化按 `1920×1536` 配置，启动链也已于 2026-08-27 调整为在启流前显式选择 V4L2 `1920×1536`。单路运行时对照已确认 1536 可消除 `err_data 64`，但六路热切换曾导致 VI timeout，因此必须以重启后的冷启动结果为准。制作新 BSP 时仍应使用 VI trace 确认真实行数，并统一厂家配置、设备树、V4L2 和后续编码链。

## 2. 厂家相机初始化运行库

当前 MAX96717F 特征（ID `0xC8`）路线使用：

```text
/home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac/
├── demo/camera_config_only
├── cfg/hb_j5dev.json
├── lib/sensorlib/libisx031std.so.1.2.0
├── lib/sensorlib/libmax9296.so.1.2.0
└── lib/sensorlib/libyuv_cam.so.1.2.0

/usr/lib/libgeaccam.so.1.2.0
```

整个 `camera_demo_geac` 目录当前约 21 MB，建议作为一个版本化制品整体保存；只复制上述最小文件时，必须保留 `.so` 的软链接链。

`camera_config_only` 当前会从系统 `/usr/lib`（`/lib` 为同一 usrmerge 路径）加载 `libgeaccam.so.1`，不是自动加载 demo 目录里的副本。新机器必须执行 `ldd camera_config_only` 验证实际加载版本，不能只看文件是否存在。

机器上另有多品牌包：

```text
/home/nvidia/cam_geac_6vendor/    # 约 27 MB
```

它用于 MAX9295A/多品牌路线，包含殷创 `extra_mode=11`、灵猴 `12`、艾利光 `13` 所需库。其 `libgeaccam.so.1.2.0` 与系统当前版本不同；切换时应通过专用 wrapper 明确设置：

```bash
LD_LIBRARY_PATH=/home/nvidia/cam_geac_6vendor/lib:/home/nvidia/cam_geac_6vendor/lib/sensorlib
```

不要把两套 `libgeaccam` 混装后依赖搜索顺序碰运气。

品牌/硬件选择原则：

| 加串器 ID | 特征型号 | 典型配置 |
|---|---|---|
| `0xC8` | MAX96717F | 当前路线 `extra_mode=2` |
| `0x91` | MAX9295A | 殷创 `11`、灵猴 `12`、艾利光 `13`，按厂家批次确认 |

当前六台虽被称为殷创，但实测都是 `0xC8`。新机器上线前应让供应商按相机料号/固件版本确认加串器，不能只按外壳品牌选择驱动。

## 3. 电源、配置和触发文件

需要随部署保存：

```text
/usr/local/bin/configure-camera/reset_power.sh
/usr/local/bin/configure-camera/reset_power_0_1.sh
/usr/local/bin/configure-camera/reset_power_2_3.sh
/usr/local/bin/configure-camera/reset_power_4_5.sh
/usr/local/bin/configure-camera/reset_power_6_7.sh
/home/nvidia/.local/bin/camera_config_generator.py
/home/nvidia/.local/bin/tztek-jetson-tool-internal-trigger-camera
/home/nvidia/start_8cam_uni2.sh
/home/nvidia/camera_stream_rtsp.sh
/etc/systemd/system/roc-camera-direct.service
```

注意：`reset_power*.sh` 里的 GPIO 名称、触发串口 `/dev/ttyTHS1`、bus 30–33 和解串器地址都是当前板级设计，换控制器必须修改。

## 4. Ubuntu/L4T 软件依赖

通用诊断和抓图依赖：

```bash
sudo apt-get install --no-install-recommends \
  v4l-utils i2c-tools ffmpeg psmisc python3 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly
```

当前 Ubuntu 包版本包括：

- `v4l-utils 1.18.0-2build1`
- `i2c-tools 4.1-2build2`
- `ffmpeg 4.2.7`
- GStreamer `1.16.x`

Jetson 硬件编解码插件必须来自与 BSP 匹配的 NVIDIA 包：

```text
nvidia-l4t-camera 35.4.1
nvidia-l4t-gstreamer 35.4.1
nvidia-l4t-multimedia 35.4.1
nvidia-l4t-multimedia-utils 35.4.1
```

并确认以下插件存在：

```bash
gst-inspect-1.0 v4l2src
gst-inspect-1.0 videoflip
gst-inspect-1.0 nvvidconv
gst-inspect-1.0 nvv4l2h264enc
gst-inspect-1.0 rtspclientsink
gst-inspect-1.0 jpegenc
```

不要把 R35 的 NVIDIA 多媒体库直接复制到 R36/JetPack 6；升级大版本需要重新适配内核、设备树和用户态 ABI。

## 5. 可选 RTSP/合成流组件

一键验收在视频节点已被占用时会优先使用现有快照或本机 RTSP。当前服务链还使用：

```text
/opt/roc/mediamtx
/opt/roc/mediamtx.yml
/etc/systemd/system/roc-mediamtx.service
/opt/roc/composite_stream.py
/opt/roc/stream_config.json
/etc/systemd/system/roc-composite.service
```

只有现场需要浏览器/VLC 远程看图时才必须部署 MediaMTX；单机空闲节点验收可由 `ffmpeg` 直接从 V4L2 抓 JPEG。

## 6. 新机器交付物建议

建议供应商/平台团队最终交付一个带版本号的目录：

```text
gmsl-camera-release-<board>-<bsp>-<date>/
├── flash/                 # 完整 BSP/刷机包，不是零散 DTB
├── kernel-source/         # 内核、DTS、补丁和构建说明
├── vendor-runtime/        # 相机初始化二进制、lib、sensorlib、配置模板
├── control/               # reset、trigger、generator、startup 脚本
├── systemd/               # 相机和可选 RTSP unit
├── acceptance/            # 本验收包和该机型 profile
├── SHA256SUMS
└── RELEASE_NOTES.md       # 相机料号、品牌、加串器、触发方式、输出尺寸/格式
```

量产前至少冻结以下信息：相机料号/固件、加串器型号和 ID、自由运行/外触发、输出行列/embedded lines、YUYV/UYVY、GMSL 速率、MIPI lane speed、物理口映射和每个二进制/库的 SHA-256。
