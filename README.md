# GMSL 相机安装一键验收包

这套工具用于结构人员完成相机和线束安装后，在域控制器开机状态下检查：

- 预期相机数量和物理口是否一致。
- MAX9296 链路是否锁定、加串器 ID 是否符合批次配置。
- `/dev/videoN` 的分辨率和 YUYV/UYVY 格式。
- 每路是否真的持续出帧，而不只是存在设备节点。
- 最近是否有 VI `corr_err`、超时或错误恢复。
- 厂家初始化 JSON 与 V4L2/设备树尺寸是否一致。
- 厂家二进制、动态库、触发程序和启动脚本是否缺失或版本漂移。

输出包括中文 HTML、Markdown、JSON、每路实拍图、Media 拓扑和内核相机日志。

## 当前基准机状态

2026-08-27 已在当前 GEACX1 / Jetson AGX Orin 上完成实际验证：预期 6 台、I2C 检测到 6 台、成功获取 6 路画面，验收程序没有停止或重启现有采集服务。

| 逻辑口 | I2C / Link | 视频节点 | 加串器 ID | 当前基准 |
|---|---|---|---|---|
| P0 | bus 30 / A | `/dev/video0` | `0xC8` | 有相机、链路锁定、可出图 |
| P1 | bus 30 / B | `/dev/video1` | `0xC8` | 有相机、链路锁定、可出图 |
| P2 | bus 31 / A | `/dev/video2` | `0xC8` | 有相机、链路锁定、可出图 |
| P3 | bus 31 / B | `/dev/video3` | `0xC8` | 有相机、链路锁定、可出图 |
| P4 | bus 32 / A | `/dev/video4` | `0xC8` | 有相机、链路锁定、可出图 |
| P5 | bus 32 / B | `/dev/video5` | `0xC8` | 有相机、链路锁定、可出图 |
| P6 | bus 33 / A | `/dev/video6` | 无应答 | 当前配置为空口 |
| P7 | bus 33 / B | `/dev/video7` | 无应答 | 当前配置为空口 |

当前六路已经按 `YUYV 1920×1536` 冷启动，RTSP 为 H.264 `1920×1536@15fps`，路径为 `rtsp://<域控IP>:8554/cam0` 至 `cam5`。2026-08-27 重启后的完整验收结果为 `PASS`：六路全部出图，`corr_err=0`、`uncorr_err=0`、timeout=0、recover=0。

2026-08-27 的运行时对照试验结果：单独将 P0 切到 1536 后，该路 `corr_err` 归零；六路热切换到 1536 后出现 VI timeout，且必须重启才能恢复。因此启动脚本改为在冷启动、尚未打开采集流时统一设置 1536。冷启动已经验证通过，但仍禁止在六路采集中直接热切换分辨率；变更尺寸后应整机重启并重新验收。

当前基准机仍有以下待确认问题：

- 1536 冷启动已通过自动验收；量产冻结前仍建议完成六路长时间运行和温度循环测试。
- 相机结构安装方向倒置；当前 RTSP/H.264 分支已使用 `nvvidconv flip-method=2` 主动旋转 180°，验收 JPEG 快照也使用 `videoflip` 同步旋转。原始 `/dev/video*` 仍保持传感器方向，直接读取 V4L2 的其他程序需要自行旋转。
- 六台加串器实测均为 `0xC8`（MAX96717F 特征），与旧资料中殷创 MAX9295A 的 `0x91` 不一致。切换 `extra_mode=11` 前必须按相机料号和固件批次向厂家确认。
- `cam4/cam5` 有独立 RTSP，但当前 composite 合成流尚未包含这两路。

## 当前机器直接使用

```bash
cd /home/ccx/gmsl-camera-acceptance
sudo ./run.sh
```

`run.sh` 是开机后的手动验收入口，不是开机自启动项。如果 `roc-camera-direct.service` 未运行，它会启动该服务；这可能执行一次 PoC 电源复位和相机初始化。已经运行的服务不会被重启。

只观察当前状态、不启动服务：

```bash
sudo python3 camera_acceptance.py \
  --profile profiles/geacx1-r35-current-6cam.json
```

临时改变预期数量，例如验收 8 台：

```bash
sudo ./run.sh --expected 8
```

脚本结束时会先在终端直接给出结论，不需要打开 HTML 才知道结果。终端内容包括：

- 明确的 `OK`、`OK（有警告）` 或 `不OK`；
- 预期、检测到和成功出图的相机数量；
- 正常出图、失败、空口及非预期接入分别是哪些物理口和视频节点；
- P0～P7 每一路的加串器 ID、解串器 LOCK 值、V4L2 格式和分辨率；
- 合并后的关键问题，以及详细 HTML 报告路径。

示例（数值仅用于说明格式）：

```text
GMSL 相机验收结论：OK（全部预期相机均出图，但有警告）
自动状态：WARN  |  预期 6 路  |  检测 6 路  |  出图 6 路
正常出图：P0(/dev/video0), P1(/dev/video1), ..., P5(/dev/video5)
失败路：  无
空口：    P6(/dev/video6), P7(/dev/video7)
```

其中 `OK（有警告）` 表示预期相机均已识别、锁定并取得画面，可以继续人工看图，但配置或内核日志仍有风险需要处理；`不OK` 表示缺相机、未锁定、视频节点缺失、无画面或存在 VI 严重错误，不能直接交付。使用 `--no-capture` 时会明确显示“未完整验收”，不会把仅检查链路误写成画面验收通过。

完整报告仍会生成在 `results/YYYYMMDD-HHMMSS/`，需要查看实拍图和原始诊断时再打开其中的 `report.html`。脚本返回码为：

- `0`：PASS
- `1`：WARN，画面可用但存在配置/日志风险
- `2`：FAIL，缺相机、链路未锁定、无画面或存在 VI 严重错误

## 开机自启动关系与文件分布

### 先区分三件事

当前系统中，“相机驱动”“相机运行服务”和“一键验收”不是同一个程序：

1. **内核驱动自动加载**：Tegra VI/NVCSI、I2C、GPIO 和 GMSL 相关驱动由 BSP、内核和设备树在开机时加载，负责注册 `/dev/video*`。这一层不在 `gmsl-camera-acceptance` 目录，也不是 `roc-camera-direct.service`。
2. **相机运行服务自动启动**：systemd 在进入 `multi-user.target` 时自动启动 MediaMTX 和相机初始化服务，负责 PoC、厂家配置、外触发、采集、旋转和 RTSP。
3. **验收程序手动执行**：`/home/ccx/gmsl-camera-acceptance/run.sh` 只负责检查和生成报告，不会由系统自动定时运行。

当前启动关系如下：

```text
BSP / 内核 / 设备树
    └─ 开机加载 VI、NVCSI、I2C、GPIO、GMSL 驱动
       └─ 注册 /dev/video0 ... /dev/video7

systemd multi-user.target
    ├─ roc-mediamtx.service                    enabled，RTSP 服务器
    │  └─ /opt/roc/mediamtx /opt/roc/mediamtx.yml
    │
    └─ roc-camera-direct.service               enabled，Type=oneshot
       └─ /home/nvidia/start_8cam_uni2.sh
          ├─ 复位 PoC
          ├─ 运行厂家 camera_config_only
          ├─ 动态创建 camtrigger.service
          └─ 动态创建 camrtsp0 ... camrtsp5
             └─ /home/nvidia/camera_stream_rtsp.sh
                ├─ 1920×1536 YUYV → 旋转 180° → H.264 RTSP
                └─ 旋转 180° → 1 FPS JPEG 验收快照

人工执行 sudo ./run.sh
    └─ 只检查依赖、I2C/LOCK、V4L2、出图和 VI 日志并生成报告
       └─ 只有发现 roc-camera-direct.service 未运行时才尝试启动它
```

### 当前基准机应该看到的服务状态

| 服务 | 是否开机启用 | 正常运行状态 | 说明 |
|---|---|---|---|
| `roc-mediamtx.service` | `enabled` | `active (running)` | RTSP 服务器，静态 unit 文件 |
| `roc-camera-direct.service` | `enabled` | `active (exited)` | 一次性完成初始化后保留 active 状态 |
| `camtrigger.service` | 由主脚本创建 | `active` | 15 Hz 外触发，属于 transient unit |
| `camrtsp0.service`～`camrtsp5.service` | 由主脚本创建 | `active` | 六路采集、旋转、编码和推流，属于 transient unit |

`roc-camera-direct.service` 显示 `active (exited)` **不是崩溃**。它配置了 `Type=oneshot` 和 `RemainAfterExit=yes`，主脚本正常执行结束后，由动态创建的触发和 RTSP 子服务继续工作。

`camtrigger` 和 `camrtsp*` 没有固定的 `/etc/systemd/system/*.service` 文件。它们由 `start_8cam_uni2.sh` 通过 `systemd-run --collect` 创建，停止或重启后会消失，并在下一次相机初始化时重新生成。

每次开机后可用以下命令确认：

```bash
systemctl is-enabled \
  roc-mediamtx.service \
  roc-camera-direct.service

systemctl is-active \
  roc-mediamtx.service \
  roc-camera-direct.service \
  camtrigger.service \
  camrtsp0.service camrtsp1.service camrtsp2.service \
  camrtsp3.service camrtsp4.service camrtsp5.service

systemctl status roc-camera-direct.service --no-pager -l
journalctl -b -u roc-camera-direct.service --no-pager
```

预期前两个 unit 返回 `enabled`，运行状态全部返回 `active`。随后再执行 `sudo ./run.sh`，确认相机数量、画面、分辨率和 VI 日志。

### 文件不全在验收目录

当前实际文件分布如下：

| 层级/功能 | 当前绝对路径 | 是否在验收目录 |
|---|---|---|
| 一键验收入口 | `/home/ccx/gmsl-camera-acceptance/run.sh` | 是 |
| 验收逻辑 | `/home/ccx/gmsl-camera-acceptance/camera_acceptance.py` | 是 |
| 当前机型 profile | `/home/ccx/gmsl-camera-acceptance/profiles/geacx1-r35-current-6cam.json` | 是 |
| 文件版本指纹 | `/home/ccx/gmsl-camera-acceptance/manifests/geacx1-r35-golden.sha256` | 是 |
| 相机启动主脚本 | `/home/nvidia/start_8cam_uni2.sh` | 否 |
| 1536、180° 旋转和 RTSP 管线 | `/home/nvidia/camera_stream_rtsp.sh` | 否 |
| 厂家配置生成器 | `/home/nvidia/.local/bin/camera_config_generator.py` | 否 |
| 15 Hz 外触发程序 | `/home/nvidia/.local/bin/tztek-jetson-tool-internal-trigger-camera` | 否 |
| PoC 复位脚本 | `/usr/local/bin/configure-camera/reset_power.sh` 及同目录分组脚本 | 否 |
| 厂家初始化程序 | `/home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac/demo/camera_config_only` | 否 |
| 厂家相机配置 | `/home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac/cfg/hb_j5dev.json` | 否 |
| 厂家动态库 | 上述 `camera_demo_geac/lib/` 和 `/usr/lib/libgeaccam.so*` | 否 |
| 相机开机服务 | `/etc/systemd/system/roc-camera-direct.service` | 否 |
| MediaMTX 服务 | `/etc/systemd/system/roc-mediamtx.service` | 否 |
| MediaMTX 程序和配置 | `/opt/roc/mediamtx`、`/opt/roc/mediamtx.yml` | 否 |
| BSP、内核和设备树 | 系统镜像、内核模块及 `/boot` | 否 |

因此，**只复制 `/home/ccx/gmsl-camera-acceptance` 到新机器不能让相机工作**。该目录是验收工具和基准资料；新机器还必须部署正确的 BSP、厂家库、板级电源/触发脚本、运行管线、MediaMTX 和 systemd unit。完整迁移方法见后面的四层说明及 [驱动与文件清单](DRIVER_AND_FILES.md)。

### 不要在 sudo 命令里依赖 `~`

普通用户 `ccx` 登录时，`~/gmsl-camera-acceptance` 通常等于：

```text
/home/ccx/gmsl-camera-acceptance
```

但通过 `sudo -i`、root shell 或部署脚本执行时，`~` 可能变成 `/root`。文档和自动化脚本应始终使用绝对路径：

```bash
cd /home/ccx/gmsl-camera-acceptance
sudo ./run.sh
```

### 新机器安装 systemd 自启动

文件部署并完成板级参数适配后，执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable roc-mediamtx.service roc-camera-direct.service
sudo systemctl start roc-mediamtx.service roc-camera-direct.service
```

`enable` 决定以后开机是否自动启动，`start` 只负责本次立即启动。第一次执行 `start` 可能复位 PoC 并重新初始化相机，必须确认没有其他程序正在占用或配置同一套 GMSL 硬件。

## 换到新域控制器

1. 先安装域控制器厂家提供的完整 BSP、设备树和 GMSL 驱动，确认 `/dev/video*`、I2C mux、PoC GPIO 已被系统枚举。
2. 按 [驱动与文件清单](DRIVER_AND_FILES.md)部署厂家相机库、配置工具、触发程序及系统依赖。
3. 复制 `profiles/template-new-machine.json`，根据新机器实测结果填写物理口、I2C bus、MAX9296 地址、link、视频节点和预期相机数量。
4. 设置新配置：

```bash
export CAMERA_ACCEPTANCE_PROFILE=/path/to/new-machine.json
sudo -E ./run.sh
```

新平台必须重新建立以下映射，不能直接复制当前 bus 30–33：

```text
机壳物理口 → MAX9296/Link A-B → I2C bus/地址 → CSI/VC → /dev/videoN
```

建议在样机阶段逐路插拔一次，把映射固化到 profile；量产装配之后即可直接一键验收。

## 新机器必须准备的驱动和文件

“四层”不是四个都由同一个脚本安装，而是相机从硬件到应用依次依赖的四组内容：

```text
第 1 层：BSP/内核/设备树
    ↓ 决定系统有没有 I2C、CSI、PoC GPIO 和 /dev/videoN
第 2 层：相机厂家初始化二进制和动态库
    ↓ 负责配置 MAX9296、加串器和 ISX031 ISP
第 3 层：电源、触发、端口映射和启动服务
    ↓ 负责按正确顺序上电、初始化、发触发并启动采集
第 4 层：V4L2/FFmpeg/GStreamer/RTSP 工具
    ↓ 负责抓帧、编码、展示和生成验收报告
```

任何一层缺失，上面的应用脚本都不能补救。例如没有第 1 层时，复制再多相机库也不会出现 `/dev/videoN`；第 2 层版本错误时，设备节点存在但 GMSL 链路可能不锁定或不出图。

### 开始前先判断是哪种新机器

#### 情况 A：相同型号、相同硬件版本的 GEACX1

这是“复制一台基准机”的场景。推荐做法是：

1. 使用同一个已经验证过的完整 BSP/刷机镜像。
2. 将第 2、3 层运行文件按原绝对路径恢复。
3. 安装第 4 层软件包。
4. 复制本验收目录和当前 profile。
5. 结构人员断电安装相机，开机后执行 `sudo ./run.sh`。

这种情况下通常不需要重新开发驱动，但仍应核对域控 PCB 版本、相机料号和线束定义是否一致。

#### 情况 B：更换域控品牌、型号、PCB 版本或 JetPack 大版本

这是“重新适配”的场景，不能照抄当前 bus 30–33、MAX9296 地址、GPIO 名称和 `/dev/videoN` 顺序。需要域控厂家或 BSP 工程师先完成第 1 层移植，平台集成工程师再完成第 2、3 层适配，最后才能使用验收脚本。

特别是 R35/JetPack 5 换到 R36/JetPack 6 时，内核和 NVIDIA 用户态 ABI 都发生变化，不能直接复制旧的内核模块或 NVIDIA 多媒体库。

### 1. 域控制器完整 BSP

这一层不能由验收脚本替代，也不能只复制单个 DTB。需要域控制器厂家提供完整刷机包、内核/设备树源码及补丁。

当前基准环境是：

- Jetson AGX Orin / Tegra234
- Jetson Linux R35.4.1
- 内核 `5.10.120-tegra`
- GEACX1 自定义 `tegra234-p3701-0005-p3737-0000.dts`
- Tegra VI、NVCSI、V4L2 Media Controller 和 8 路 sensor endpoint

新 BSP 必须正确提供：

- VI/NVCSI、CSI lane、port 和 VC 映射。
- I2C mux、MAX9296 地址及 link A/B 拓扑。
- PoC 电源 GPIO；当前机器使用 `/sys/class/tz_gpio/camera_*-power/value`。
- 外触发接口；当前机器使用 `/dev/ttyTHS1`。
- 与真实相机输出一致的宽高、像素格式、pixel clock 和 embedded metadata 行数。

当前启动链已把目标统一为 `1920×1536`，但仍需重启冷启动验证。新 BSP 不能只照抄数值；应先用 VI trace 确认真实行数，再统一厂家配置、设备树、V4L2 和编码链。

#### 第 1 层具体怎么操作

负责人通常是域控制器厂家或负责 Jetson BSP 的工程师。

第一步，取得完整交付物，而不是只拿几个 `.ko` 或 `.dtb`：

```text
flash/                  完整刷机包和刷机说明
kernel-source/          内核源码、配置、补丁
device-tree/            DTS/DTSI 和 overlay 源码
board-release-notes/    域控型号、PCB 版本、JetPack/L4T 版本
```

同型号机器应使用厂家规定的刷机流程恢复同一版本镜像。不同型号机器则要让 BSP 工程师先在设备树中配置 I2C mux、MAX9296 所在总线、CSI port/VC、PoC GPIO 和 8 路 endpoint。

刷机完成后，在还没有运行厂家相机初始化程序之前，先执行以下检查：

```bash
cat /etc/nv_tegra_release
uname -a
tr -d '\0' </proc/device-tree/model; echo

i2cdetect -l
v4l2-ctl --list-devices
media-ctl -p

ls -l /sys/class/tz_gpio/ 2>/dev/null
ls -l /dev/ttyTHS1 2>/dev/null
```

第 1 层通过标准：

- L4T、内核版本和计划部署版本一致。
- `i2cdetect -l` 能看到板级 I2C 及解串器所用 mux 通道。
- `v4l2-ctl --list-devices` 能看到预期数量的 Tegra CSI 视频节点。
- `media-ctl -p` 中 sensor → NVCSI → VI 链路完整且为 `ENABLED`。
- PoC GPIO 和触发串口存在。
- 即使尚未接相机，预注册的 `/dev/videoN` 也可以存在；这时还不能说明相机能出图。

如果 `/dev/video*`、I2C mux 或 PoC GPIO 缺失，应停在这一层处理，不要继续复制相机库尝试碰运气。

### 2. 厂家相机初始化库

当前 MAX96717F 特征路线依赖：

```text
/home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac/
├── demo/camera_config_only
├── cfg/hb_j5dev.json
├── lib/sensorlib/libisx031std.so.1.2.0
├── lib/sensorlib/libmax9296.so.1.2.0
└── lib/sensorlib/libyuv_cam.so.1.2.0

/usr/lib/libgeaccam.so.1.2.0
```

当前整个 `camera_demo_geac` 约 21 MB，建议按版本整体保存。`camera_config_only` 实际从系统 `/usr/lib` 加载 `libgeaccam.so.1`，所以迁移后必须运行 `ldd camera_config_only` 检查真正加载的版本。

机器上还有约 27 MB 的多品牌运行包：

```text
/home/nvidia/cam_geac_6vendor/
```

它用于 MAX9295A/多品牌路线，包括殷创 `extra_mode=11`、灵猴 `12` 和艾利光 `13`。它的 `libgeaccam` 与当前系统版本不同，使用时应明确设置：

```bash
LD_LIBRARY_PATH=/home/nvidia/cam_geac_6vendor/lib:/home/nvidia/cam_geac_6vendor/lib/sensorlib
```

不要把两套动态库混装后依赖系统搜索顺序。

| 加串器 ID | 硬件特征 | 典型初始化配置 |
|---|---|---|
| `0xC8` | MAX96717F | 当前路线 `extra_mode=2` |
| `0x91` | MAX9295A | 殷创 `11`、灵猴 `12`、艾利光 `13`，按厂家批次确认 |

#### 第 2 层具体怎么操作

负责人通常是相机厂家和平台集成工程师。开始前必须拿到每个相机料号对应的：

- 加串器型号和 chip ID。
- 自由运行/外触发方式。
- 分辨率、帧率、YUYV/UYVY、MIPI Data Type。
- GMSL 速率、MIPI lane 数和 lane speed 建议值。
- 厂家初始化表、ISP 固件版本和适用的 `extra_mode/config_index`。

如果新机器使用和当前基准机完全相同的 BSP、相机批次和目录结构，可以在当前基准机制作运行文件包。下面命令只读取并打包文件，不包含地图、业务数据或密钥：

```bash
sudo tar --numeric-owner -C / -czpf /tmp/gmsl-camera-runtime-r35.4.1.tar.gz \
  home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac \
  home/nvidia/cam_geac_6vendor \
  usr/lib/libgeaccam.so usr/lib/libgeaccam.so.1 usr/lib/libgeaccam.so.1.2.0 \
  home/nvidia/.local/bin/camera_config_generator.py \
  home/nvidia/.local/bin/tztek-jetson-tool-internal-trigger-camera \
  usr/local/bin/configure-camera \
  home/nvidia/start_8cam_uni2.sh \
  home/nvidia/camera_stream_rtsp.sh \
  etc/systemd/system/roc-camera-direct.service \
  opt/roc/mediamtx \
  opt/roc/mediamtx.yml \
  etc/systemd/system/roc-mediamtx.service

cd /tmp
sha256sum gmsl-camera-runtime-r35.4.1.tar.gz \
  > gmsl-camera-runtime-r35.4.1.tar.gz.sha256
```

将压缩包和 `.sha256` 一起通过内网或 U 盘传到新机器，先校验：

```bash
sha256sum -c gmsl-camera-runtime-r35.4.1.tar.gz.sha256
tar -tzf gmsl-camera-runtime-r35.4.1.tar.gz | less
```

只有在“相同 BSP 的干净新机器”上，确认目标路径没有需要保留的旧相机文件后，才按原路径恢复：

```bash
sudo tar --numeric-owner -C / -xzpf gmsl-camera-runtime-r35.4.1.tar.gz
sudo ldconfig
```

如果新机器已经装过其他版本相机库，应先备份并逐文件比较，不能直接覆盖 `/usr/lib/libgeaccam*`。

恢复后检查二进制架构、动态库和文件版本：

```bash
file /home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac/demo/camera_config_only

ldd /home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac/demo/camera_config_only

cd /home/ccx/gmsl-camera-acceptance
sha256sum -c manifests/geacx1-r35-golden.sha256
```

`ldd` 输出中不能出现 `not found`，并且要确认 `libgeaccam.so.1` 指向计划使用的版本。SHA-256 不一致不一定表示文件损坏，也可能是厂家升级过版本；此时必须记录新版本并重新做单路、双路和多路验证，然后更新 manifest。

第 2 层通过标准：

- 所有 ELF 文件都是 `aarch64`，并与目标 L4T/ABI 兼容。
- `ldd` 没有缺库，也没有误加载另一套 `libgeaccam`。
- 当前相机批次与所选 `extra_mode/config_index` 有厂家依据。
- 厂家 JSON 中宽高、触发方式和设备树计划一致。

### 3. 电源、配置和触发文件

需要随新机器部署保存：

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

这些文件中的 GPIO 名称、触发串口、I2C bus 和解串器地址都是板级参数，换控制器后必须重新核对。

#### 第 3 层具体怎么操作

负责人通常是平台集成工程师。这一层的核心不是“把脚本放过去”，而是把脚本里的板级参数改成新机器的真实值。

需要逐项核对：

| 参数 | 当前基准位置 | 换机后怎么确认 |
|---|---|---|
| I2C bus | `start_8cam_uni2.sh` 的 `PBUS`、配置生成器 `DESERIALIZERS` | `i2cdetect -l`、原理图、单路插拔 |
| MAX9296 地址 | 配置生成器 `0x48/0x68` | 原理图、设备树、I2C 只读探测 |
| Link A/B 加串器别名 | 当前 `0x41/0x42` | 厂家初始化库和相机资料 |
| PoC GPIO | `reset_power*.sh` | `/sys/class/tz_gpio` 或新 BSP GPIO 接口 |
| 触发串口 | 当前 `/dev/ttyTHS1` | 原理图、设备树和示波器验证 |
| 外触发频率 | 当前服务实际 15 Hz | 相机规格、算法时间同步要求 |
| video 映射 | `videoN <- Pn` | 单路插拔、I2C lock、实际画面交叉确认 |

先复制新机器 profile：

```bash
cd /home/ccx/gmsl-camera-acceptance
cp profiles/template-new-machine.json profiles/<新域控型号>.json
```

至少修改 profile 中这些字段：

```text
expected_camera_count
expected_video
startup_systemd_unit
vendor_config
deserializers[].bus/address
ports[].bus/link/serializer_address/video_node
ports[].expected
ports[].expected_serializer_ids
runtime_paths
ldd_targets
```

然后核对或修改启动链中的硬编码路径和数组：

```bash
rg -n 'PBUS|PSER|DESERIALIZERS|ttyTHS|reset_power|camera_demo_geac|video' \
  /home/nvidia/start_8cam_uni2.sh \
  /home/nvidia/.local/bin/camera_config_generator.py \
  /usr/local/bin/configure-camera \
  /etc/systemd/system/roc-camera-direct.service
```

安装/刷新 systemd 服务：

```bash
sudo chmod +x \
  /home/nvidia/start_8cam_uni2.sh \
  /home/nvidia/camera_stream_rtsp.sh \
  /home/nvidia/.local/bin/camera_config_generator.py \
  /home/nvidia/.local/bin/tztek-jetson-tool-internal-trigger-camera \
  /usr/local/bin/configure-camera/reset_power*.sh

sudo systemctl daemon-reload
sudo systemctl enable roc-mediamtx.service roc-camera-direct.service
systemctl cat roc-camera-direct.service
```

第一次启动前：

1. 整机断电。
2. 结构人员安装并锁紧相机与同轴线，检查退针和压接。
3. 确认没有其他程序占用 `/dev/video*` 或重复配置同一颗 MAX9296。
4. 再开机启动相机服务。

首次启动和日志检查：

```bash
sudo systemctl start roc-camera-direct.service
systemctl status roc-camera-direct.service --no-pager -l
journalctl -b -u roc-camera-direct.service --no-pager

systemctl is-active camtrigger.service 2>/dev/null || true
ps -eo pid,stat,args | rg 'camera_config|gst-launch|trigger|video'
```

当前服务首次发现和配置最多接近 5 分钟。如果新机器初始化时间可能更长，需要同时调整 systemd 的 `TimeoutStartSec` 和验收 profile 的 `startup_timeout_s`，不能只改其中一个。

第 3 层通过标准：

- 电源复位脚本控制的是正确 PoC 分组。
- 每个已安装物理口都能读到预期加串器 ID。
- MAX9296 锁定寄存器稳定，不在锁定/掉锁之间跳变。
- 外触发进程持续运行，频率符合相机和算法要求。
- 只有一个初始化责任方，没有两个服务同时复位/配置相机。
- 每个物理口到 `/dev/videoN` 的映射已经写入新 profile。

### 4. 系统软件和 NVIDIA 多媒体组件

Ubuntu 通用依赖：

```bash
sudo apt-get install --no-install-recommends \
  v4l-utils i2c-tools ffmpeg psmisc python3 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly
```

还必须安装与 BSP 完全一致的 NVIDIA 软件包：

```text
nvidia-l4t-camera
nvidia-l4t-gstreamer
nvidia-l4t-multimedia
nvidia-l4t-multimedia-utils
```

当前版本均为 R35.4.1。不要把 R35 的 NVIDIA 多媒体库直接复制到 R36/JetPack 6。

验收程序会自动检查以下插件：

```text
v4l2src
videoflip
videoconvert
jpegenc
nvvidconv
nvv4l2h264enc
rtspclientsink
```

#### 第 4 层具体怎么操作

负责人通常是系统镜像维护或平台部署工程师。

先检查现有软件包，不要一上来就升级整机：

```bash
dpkg-query -W -f='${Package}\t${Version}\n' \
  v4l-utils i2c-tools ffmpeg psmisc python3 \
  gstreamer1.0-tools \
  nvidia-l4t-core nvidia-l4t-kernel nvidia-l4t-camera \
  nvidia-l4t-gstreamer nvidia-l4t-multimedia \
  nvidia-l4t-multimedia-utils
```

缺少 Ubuntu 通用工具时，再使用前面的 `apt-get install` 命令。NVIDIA `nvidia-l4t-*` 包如果缺失或版本混杂，优先重新使用正确 BSP 刷机/修复软件源，不要从另一个 JetPack 版本手工复制 `.so`。

逐个确认 GStreamer 元件：

```bash
for plugin in v4l2src videoflip videoconvert jpegenc nvvidconv nvv4l2h264enc rtspclientsink; do
  gst-inspect-1.0 "$plugin" >/dev/null \
    && echo "OK      $plugin" \
    || echo "MISSING $plugin"
done
```

再确认基础命令和权限：

```bash
command -v v4l2-ctl media-ctl i2cdetect i2ctransfer ffmpeg python3
id
ls -l /dev/video* /dev/i2c* 2>/dev/null
```

第 4 层通过标准：

- 通用命令全部存在。
- NVIDIA GStreamer 插件全部可被 `gst-inspect-1.0` 加载。
- `nvidia-l4t-*` 包版本与 `/etc/nv_tegra_release` 属于同一个 BSP 版本。
- 执行验收的用户有视频、I2C 和内核日志读取权限；推荐安装验收时直接使用 `sudo`。

### 5. 可选 RTSP 组件

如果现场需要通过 VLC/网络查看画面，或者视频节点已被后台进程占用，还应部署：

```text
/opt/roc/mediamtx
/opt/roc/mediamtx.yml
/etc/systemd/system/roc-mediamtx.service
```

没有 RTSP 时，只要视频节点空闲，验收程序仍可直接用 `ffmpeg` 从 V4L2 抓取 JPEG。

更完整的文件解释和建议交付目录见 [驱动与文件清单](DRIVER_AND_FILES.md)。

## 从装机到验收的完整操作顺序

下面是一台新机器第一次上线时的推荐顺序。

### 步骤 1：准备镜像和运行文件

1. 按第 1 层刷入正确 BSP。
2. 执行第 1 层检查，确认 I2C、Media、PoC GPIO、触发接口存在。
3. 安装第 2 层厂家运行库，并完成 `ldd` 和 SHA-256 检查。
4. 修改第 3 层板级参数和新机器 profile。
5. 安装第 4 层系统工具和 GStreamer/NVIDIA 软件包。

### 步骤 2：结构安装

1. 域控制器完全断电。
2. 按定义的物理口安装相机。
3. 锁紧连接器，检查端子退针、线束压接和弯折半径。
4. 记录相机序列号、料号、物理安装方向和所接端口。
5. 禁止带 PoC 热插拔。

### 步骤 3：首次开机初始化

```bash
sudo systemctl start roc-camera-direct.service
systemctl status roc-camera-direct.service --no-pager -l
```

如果 profile 使用的服务名不同，替换上面的 unit。等待初始化完成，不要在过程中重复运行配置程序或手动复位相机。

### 步骤 4：运行一键验收

```bash
cd /home/ccx/gmsl-camera-acceptance
export CAMERA_ACCEPTANCE_PROFILE=$PWD/profiles/<新域控型号>.json
sudo -E ./run.sh
```

脚本会检查四层依赖、链路、视频节点和内核日志，并把实拍图放入报告目录。

### 步骤 5：打开报告并人工看图

```bash
ls -dt results/* | head -n 1
```

打开最新目录下的 `report.html`。自动结果为 `PASS/WARN/FAIL`，但仍必须人工确认：

- 前后左右视角是否接到正确端口。
- 画面是否倒置或镜像。
- 结构件、线束、保险杠是否遮挡视野。
- 是否有偏色、青条、撕裂、重影或局部黑屏。
- 相机时间同步是否满足算法要求。

### 步骤 6：冻结基准

验收通过后保存：

- 最终 profile。
- 厂家 JSON。
- 验收 HTML/JSON 和实拍图。
- BSP、厂家库、启动脚本的 SHA-256。
- 相机料号、序列号、端口映射和固件版本。

以后同型号机器就可以按这个基准量产验收。

## 根据现象判断卡在哪一层

| 现象 | 优先检查层级 | 常见原因 |
|---|---|---|
| 没有 `/dev/videoN` | 第 1 层 | BSP/DTB 不对、VI/NVCSI endpoint 未注册 |
| 没有 I2C mux 或 PoC GPIO | 第 1 层 | 板级设备树或 GPIO 驱动缺失 |
| `camera_config_only` 启动即报缺库 | 第 2 层 | 动态库没复制、ABI 不兼容、`LD_LIBRARY_PATH` 错 |
| 加串器无应答、MAX9296 `0x12/0x22` | 第 3 层或硬件 | PoC 未上电、线束/端口错误、复位时序或地址配置错误 |
| 加串器 ID 与 profile 不一致 | 第 2/3 层 | 相机批次变更、选错初始化库、物理口映射错误 |
| 链路锁定但完全无帧 | 第 1–3 层交界 | CSI/VC、触发、宽高/Data Type、ISP 初始化错误 |
| 单路正常，双路青条/撕裂 | 第 1/2 层 | MIPI lane speed、带宽、VC 或 GMSL 速率不合适 |
| `err_data 64` 每帧出现 | 第 1/2 层 | 真实输出行数大于设备树/驱动声明，或 embedded lines 配置错误 |
| V4L2 可抓图但 RTSP 不工作 | 第 4 层 | GStreamer/NVIDIA 插件、MediaMTX 或编码链缺失 |
| 报告没有图但节点被占用 | 第 4 层 | 后台没有生成快照，且本机 RTSP 不可用 |

## 安全边界

- 验收程序本身不会停服务、改寄存器配置或复位电源；I2C 操作仅读取状态寄存器。
- `run.sh` 显式带 `--start`，仅在相机服务未运行时启动服务，服务本身可能复位 PoC。
- 不要带 PoC 热插拔 GMSL 同轴线。结构人员应整机断电后再插拔、紧固和检查退针。
- 视频节点被现有采集进程占用时，工具优先复制新鲜快照，其次从本机 RTSP 抓帧，不抢占 V4L2 节点。

## 文件说明

- `run.sh`：一键入口。
- `camera_acceptance.py`：验收逻辑和报告生成器。
- `profiles/geacx1-r35-current-6cam.json`：当前六路机器配置。
- `profiles/template-new-machine.json`：新平台模板。
- `manifests/geacx1-r35-golden.sha256`：当前基准机关键文件指纹。
- `DRIVER_AND_FILES.md`：BSP、厂家库、软件包和迁移清单。
- `results/YYYYMMDD-HHMMSS/report.html`：每次验收生成的图文报告。
