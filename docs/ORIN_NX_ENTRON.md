# 新 NX 部署殷创 GMSL2 相机：从准备到出流

本文复现 2026-10-19 实机从“六路均不 OK”到“六路全部 PASS”的完整步骤。所有命令均在仓库根目录执行，除非明确标注为“操作电脑”。默认运行目录是 `/opt/gmsl-nx`，不依赖登录用户名。

## 0. 先确认适用范围

本流程已经验证的组合：

| 项目 | 要求 |
|---|---|
| 主控与载板 | 厂家 TZTEK Orin NX 8 口 GMSL 板型，四组 MAX9296 |
| 系统 | Ubuntu 20.04 arm64、Jetson Linux R35.4.1、内核 `5.10.120-tegra` |
| 视频节点 | 厂家 BSP 注册 `/dev/video0`～`video7`；名字可显示 `imx185`，不是据此判断实际传感器 |
| 解串器 | bus 30/31/32/33，地址 0x48/0x68/0x48/0x68；ID 0x94 |
| CSI | 每组 2 lanes；VI port-index 0/0/1/1/2/2/3/3，VC 0/1 交替 |
| 相机 | 用户确认殷创；本批 serializer ID 实测 0xc8；使用 `isx031std`、`extra_mode=2` |
| 输出 | 1920×1536 YUYV → H.264、15 Hz 外触发、六路 RTSP |

**殷创品牌本身不足以确定驱动。** 如果探测到 0x91，或者相机料号、固件、分辨率不同，不要直接将 extra_mode 改成 11 继续跑；需要对应的厂家方案。此仓库包只对上述实测批次完成验证。

“NX”也不是足够的适用条件。普通 NVIDIA 开发板、不同厂家的载板、R36/JetPack 6、4-lane AGX，均需先完成各自的 BSP/驱动适配。本包不会刷机，也不能替代缺失的内核驱动、设备树、MAX9296 字符设备或 PoC GPIO。

准备：匹配的厂家 BSP；本仓库/离线包；实际接入相机数量；可 SSH 登录并执行 sudo。首次部署不需要 AGX 在线，不需要知道每个机壳插座对应的 bus。

## 1. 把完整仓库放到新 NX

### 方式 A：复制打好的离线包（包含当前工作区内容）

在已有完整仓库的机器中执行：

```bash
cd /path/to/gmsl-camera-acceptance
./nx/package.sh /tmp/gmsl-camera-acceptance-nx.tar.gz
```

在操作电脑将包复制到新 NX。替换 `用户`、`NX地址`、`SSH端口`：

```bash
scp -P SSH端口 /path/to/gmsl-camera-acceptance-nx.tar.gz 用户@NX地址:/tmp/
ssh -p SSH端口 用户@NX地址
```

进入新 NX 后：

```bash
mkdir -p "$HOME/gmsl-camera-acceptance"
tar -xzf /tmp/gmsl-camera-acceptance-nx.tar.gz -C "$HOME/gmsl-camera-acceptance"
cd "$HOME/gmsl-camera-acceptance"
```

这里的 `$HOME` 是普通登录用户自己的目录；后续安装器采用明确的 `/opt/gmsl-nx`，不会把 `sudo -i` 后的 `/root` 当作用户主目录。

### 方式 B：从 Git 获取

当对应 NX 改动已经提交到仓库远端时：

```bash
git clone https://github.com/ccx1997/gmsl-camera-acceptance.git
cd gmsl-camera-acceptance
```

确认存在 `nx/deploy.py` 和 `nx/bundles/`。若远端版本还没有这些文件，用方式 A 复制完整工作区。

核验文件完整性：

```bash
cd nx/bundles
sha256sum -c SHA256SUMS
cd ../..
```

应全部显示 `OK`。校验失败先重新复制文件，不要跳过校验。

## 2. 补齐系统依赖

联网安装通用组件：

```bash
sudo ./nx/install-deps.sh
```

只安装依赖，不升级 JetPack、内核或设备树。`nvvidconv`、`nvv4l2h264enc` 等 NVIDIA 插件必须由匹配的 BSP/软件包提供。

如果 apt 软件源无法连接，且当前 BSP 已有通用工具，只缺 `rtspclientsink`：

```bash
sudo ./nx/install-deps.sh --offline-rtsp
```

仓库带有已校验的两个 Ubuntu Focal arm64 包。离线模式只补它们；如果 dpkg 提示其他依赖缺失，先在同版本系统中补齐缺失包，不要把“离线 RTSP 补包”当作完整系统安装包。插件已存在时不会覆盖或降级现有版本。

## 3. 运行只读预检查

```bash
sudo python3 nx/deploy.py preflight
```

正确结果包含：

```text
PASS: R35.4.1、8 节点、4 组 MAX9296、2-lane/VC 拓扑和依赖符合本适配器
```

此步骤核对系统、实际 VI endpoint、设备节点、MAX9296 芯片 ID、Python GI、GStreamer 和验收工具，不打开相机采集，也不写 I2C 寄存器。

若失败，按打印内容修复：

- 缺 `/dev/max9296_*`、PoC GPIO、I2C mux 或 2-lane/VC 不匹配：安装/修复该载板厂家的 BSP，不要拷贝 AGX 的 DTB。
- 缺命令或插件：返回第 2 步。NVIDIA 编码插件缺失要检查匹配的 L4T 包。
- MAX9296 无应答/ID 不符：核对载板、电源、I2C 总线和地址，不能继续套用此板级配置。
- 型号字符串显示 `Orin Nano Developer Kit`：部分厂家 DT 采用通用字符串；它本身不证明模块装错。以模块料号、厂家 BSP 和实际设备拓扑为准。

板级定义保存在 `nx/boards/tztek-orin-nx-r35.json`。修改该文件不能替代新板型验证；当前已验证的是默认文件描述的板型。

## 4. 停止占用相机的程序

第一次安装的新系统通常没有采集程序。先查看：

```bash
sudo fuser /dev/video0 /dev/video1 /dev/video2 /dev/video3 \
  /dev/video4 /dev/video5 /dev/video6 /dev/video7 /dev/ttyTHS1
```

没有输出且返回 1 表示空闲。若之前已部署本仓库的 NX 服务：

```bash
sudo systemctl stop nx-gmsl.target
```

主 target 停止后，子进程可能还需要片刻释放设备，工具会最多等待 12 秒。若仍被占用，根据 PID 查看所属进程/服务并停止其业务入口：

```bash
ps -fp 这里填PID
systemctl status 这里填PID
```

不要无差别杀进程，不要在仍采集时复位相机电源或改分辨率。工具不会替你终止不相关的采集程序。若 VI 线程处于无法退出的 D 状态，记录日志，在现场允许时整机重启，然后从预检查继续。

## 5. 自动探测实际接线

```bash
sudo python3 nx/probe.py --output /tmp/nx-gmsl-probe.json
```

约 50 秒。这个命令会：

1. 确认设备空闲，复位四组相机电源。
2. 逐个选择 A/B 链路，以 GMSL2 3 Gbps 测试锁定。
3. 连读三次锁定状态，锁定后读取相机端 serializer ID。
4. 在每组测试后读回确认，恢复该组测试前的速率/选链寄存器。
5. 保存本机探测报告。探测不是正式相机初始化；电源复位也意味着之前的相机配置不会保留。

本次实测示例：

```text
P0 bus30 linkA locked=True ID=0xc8
P1 bus30 linkB locked=True ID=0xc8
P2 bus31 linkA locked=True ID=0xc8
P3 bus31 linkB locked=True ID=0xc8
P4 bus32 linkA locked=False ID=-
P5 bus32 linkB locked=True ID=0xc8
P6 bus33 linkA locked=False ID=-
P7 bus33 linkB locked=True ID=0xc8
```

**新机器不要求接成这一组序号。** 例如实际是 P0～P5，安装器会采用那次探测结果。P0～P7 是软件拓扑序号，不能直接等同机壳标注；需要逐个遮挡/拔插确认的“前、后、左、右”命名留给现场标定。

若预期 6 台只探测到 4 台，不要为了通过安装把预期数改成 4。先查缺失链路的 PoC、连接器、线束和相机。若 `locked=True` 但读不到 0xc8，确认批次与串行器型号。探测脚本报错会保存失败记录；失败记录不能用于安装。

## 6. 安装、初始化、启动六路流

```bash
sudo python3 nx/deploy.py install \
  --probe-file /tmp/nx-gmsl-probe.json \
  --expected 6
```

默认旋转 180°，与本次倒装相机一致。相机正装时在安装命令加 `--flip 0`。`--expected` 应填写真实应装数量；配置生成支持 1～8 路，但本文实机吞吐验证是 6 路，不代表已完成 8 路压力验证。

安装器会：

- 拒绝其他机器的探测报告、数量不符、未知相机 ID、错误 BSP、其他进程占用 8554/9997 等情况。
- 校验内置包，先展开到临时目录，用指定的独立库检查 `ldd`。
- 备份旧配置/服务到 `/var/backups/gmsl-nx/<时间>/`；已有受管理安装也会保留原运行目录。
- 生成本机 `camera.json`、映射、验收 profile、运行文件 SHA256 和 systemd units。
- 停用错误的 `jetson-cam-cfg.service` 开机入口，避免它再写回 sensing/IMX390 配置。
- 电源复位、完整初始化；按 NX 2-lane/2400 Mbps 配置 CSI。
- 对“仅 B 链路接相机”的组，将输出从 VC0 修正为相应奇数 video 节点期望的 VC1，逐项读回验证。
- 启动 15 Hz 触发、MediaMTX、全部采集编码进程，并检测 30 秒的持续出帧。

终端应显示每路约 15 fps、`"ok": true`、`kernel_camera_errors: []`，最后输出部署完成与备份位置。安装或持续出帧检查失败时，会尝试恢复安装前文件和服务；日志及被替换运行目录保留用于分析。如果底层设备卡死导致恢复服务也失败，按第 11 节处理，不要重复循环安装。

如果确实已独立确认接线，也支持显式端口：

```bash
sudo python3 nx/deploy.py install --ports 0,1,2,3,5,7 --expected 6
```

这里的序号只用于已确认接线；新机器优先使用 probe。手工指定绕过了安装前的 serializer 批次筛选，初始化后仍会校验 ID，所以不能用它强行部署未知型号。

## 7. 完整验收：必须真拉流

```bash
sudo ./run.sh
```

期望：

```text
GMSL 相机验收结论：OK（自动检查通过）
自动状态：PASS | 预期 6 路 | 检测 6 路 | 出图 6 路
失败路：无
```

NX profile 强制通过 RTSP 解码每路实拍图；RTSP 不通会失败，不能拿旧 JPEG 冒充通过，也不会自动打开空闲 V4L2 节点试抓。它同时检查最近 5 分钟的相机内核日志。

刚从故障状态修好后，最近 5 分钟可能仍包含修复前的错误。先检查 `/opt/gmsl-nx/logs/stream-check-latest.json` 的本次时间窗口与最新内核日志，待旧错误自然移出 5 分钟窗口再运行验收。不要清空日志或缩短窗口来制造 PASS。

查看 `results/<时间>/report.html`，逐路确认方向、视角、遮挡、颜色、青条和撕裂。验收返回 0 为 PASS、1 为 WARN、2 为 FAIL。日常连续出帧复核：

```bash
sudo python3 /opt/gmsl-nx/check_streams.py
```

它根据已安装的映射检查全部相机，比较 30 秒内采集/编码帧数、RTSP 字节增长、进程启动时间和内核采集错误；不会直接打开 V4L2。

## 8. 在电脑上看流

同一局域网，先在 NX 上执行 `hostname -I` 获取 IP，然后在 VLC 等播放器中以 **RTSP over TCP** 打开：

```text
rtsp://NX的IP:8554/cam0
rtsp://NX的IP:8554/cam1
...
rtsp://NX的IP:8554/cam5
```

只能通过公网 SSH 访问时，在操作电脑建立隧道并保持窗口运行：

```bash
ssh -p SSH端口 -N -L 18554:127.0.0.1:8554 用户@SSH地址
```

本次机器示例：

```bash
ssh -p 10104 -N -L 18554:127.0.0.1:8554 ccx@116.198.150.2
```

电脑播放器打开 `rtsp://127.0.0.1:18554/cam0`，改末尾 camN 切换相机。此操作不需要新增公网 RTSP 端口。原始 `/dev/video*` 保持传感器方向，180° 旋转发生在推流/快照管线中。

## 9. 服务管理与开机复验

```bash
systemctl is-enabled nx-gmsl.target
systemctl status nx-gmsl.target 'nx-gmsl-stream@*' --no-pager
sudo systemctl restart nx-gmsl.target
```

主 target 的 `enabled` 表示开机启用。`nx-gmsl-init.service` 正常状态是 `active (exited)`；触发、MediaMTX 和每路 stream 服务应是 `active (running)`。

整套重启使用 target，不要只重跑初始化脚本而让采集仍占用设备。更换接线时先停 target，重新 probe，再 install；不能只改 camN 名字。

厂家初始化会配置共享 CPLD 触发器，即使 JSON 只选择两路，也可能改变全部相机的触发频率；触发服务同时占用串口还会造成应答竞争。不要在其他路运行时自行执行“单组初始化”。使用上面的 `restart nx-gmsl.target` 会短暂中断六路，并按依赖顺序重新初始化、恢复 15 Hz 和启动推流。

**服务重启通过不等于整机冷启动通过。** 在现场允许重启后，执行一次整机重启/断电重上电，再运行 `sudo ./run.sh` 和连续出帧检查，确认开机供电、驱动加载与触发时序。长时间运行与温度/线束振动测试仍需单独验收。

## 10. 运行目录与回滚

```text
/opt/gmsl-nx/
  demo/camera_config_only    厂家初始化入口，不采集 V4L2
  lib/                      独立厂家库及软链接
  bin/                      MediaMTX 和触发程序
  cfg/board.json            本机板级定义
  cfg/camera.json           本机实际接线的厂家配置
  cfg/mapping.json          camN、videoN、bus、link、旋转映射
  cfg/acceptance.json       本机验收配置
  cfg/runtime.sha256        安装时生成的运行文件指纹
  initialize.py             电源复位、初始化、单 B 链路 VC 修正
  stream.py                 持续采集、编码、RTSP、限量快照
  check_streams.py           30 秒连续出帧检查
  deployment.json           当前部署与上次备份位置
  logs/                     初始化、VC 修正、连续检测记录
/etc/systemd/system/nx-gmsl* 五个静态 unit/template
/etc/gmsl-camera-acceptance/profile  供 run.sh 自动选择当前配置
/var/backups/gmsl-nx/       每次安装的旧文件与服务状态
```

旧的手工修复目录 `/home/ccx/gmsl-nx-runtime` 可作为此次基准机的原始备份保留；新部署不再依赖它。

查看最近备份：

```bash
cat /opt/gmsl-nx/deployment.json
```

回滚时使用安装器打印的那个具体备份目录：

```bash
sudo python3 nx/deploy.py rollback --backup /var/backups/gmsl-nx/这里替换为时间目录
```

回滚会停止本套服务、恢复原 unit/selector/已管理运行目录，以及原来的开机启用状态；如果原 target 运行中，会尝试重新启动。换下来的文件保留在该备份中。相同备份只回滚一次；回滚不卸载通用系统依赖，也不改变内核/BSP。首次安装前本来没有可用相机服务时，回滚只恢复那个状态，不会凭空生成旧推流。

## 11. 故障按最先失败的一层排查

| 现象 | 优先检查 | 本次经验 |
|---|---|---|
| 有 video 节点，六路仍不 OK | BSP 是否只有占位节点、实际链路是否锁定 | 节点存在不证明相机出图 |
| 原生配置无相机应答 | 运行 probe，检查 3 Gbps 锁定和 serializer ID | 本次默认 6 Gbps 全不锁，3 Gbps 六路锁定 |
| 文件/服务缺失大量 FAIL | 是否使用了 NX 安装器生成的 profile | 原脚本误用 AGX 路径和服务 |
| `not-negotiated (-4)` | 映射与 V4L2 枚举尺寸；是否选了空的 video4/video6 | 本次后两路实际是 video5/video7 |
| 前四路短帧/`corr_err` | CSI lane 数、每 lane 速率、真实尺寸 | 2-lane/1500 多路出现短帧，2400 消除 |
| 只有单 B 组超时/绿图 | VC0/VC1 与奇数 video 节点对应关系 | 库默认单 B 输出 VC0，NX odd 节点期望 VC1 |
| 开机后 cam2/cam3 同时纯绿，链路和 ID 正常 | bus31 / CSI1 的采集超时与实际帧率；不能只看服务 active | 已复现约 1.6 fps；组内重上电和重新初始化后恢复。具体开机时序根因待确认，见修复记录 |
| 有图但没有 RTSP | rtspclientsink、MediaMTX、8554 监听、发布进程 | 本次补齐 RTSP 插件并加入服务 |
| 安装提示设备忙 | fuser 的 PID 所属服务、是否仍在停止中 | 工具等待释放，超时拒绝写配置 |
| 软件源 TLS/代理错误 | 先判断只缺 RTSP 还是其他依赖 | 只缺 RTSP 可用本仓库两个离线包 |
| V4L2 进程长时间 D 状态 | 保存 VI 日志、现场协调重启 | 不无限重试/强杀，也不继续复位正在采集的硬件 |
| 画面倒置或遮挡 | `--flip 0/2`、实拍图与安装位置 | 推流方向可调；软件不能消除机械遮挡 |

常用日志命令：

```bash
sudo journalctl -b -u nx-gmsl-init -u nx-gmsl-trigger -u nx-gmsl-mediamtx --no-pager
sudo journalctl -b -u 'nx-gmsl-stream@*' --no-pager
sudo journalctl -k --since '-5 minutes' --no-pager
sudo tail -n 80 /opt/gmsl-nx/logs/init-latest.log
sudo cat /opt/gmsl-nx/logs/vc-remap-latest.json
```

需要继续排查时提供：载板/模块型号、L4T 版本、相机料号/固件、实际应装数量、probe JSON、最新验收 `report.json`、初始化日志和对应时间的内核日志。一般不需要提供 SSH 密码、账户 token 或整机系统备份。

### SSH 经常断线时

可以让安装在 NX 的 systemd 下执行，断开 SSH 不会终止安装。先完成 probe 并确认报告已保存，再在仓库根目录运行：

```bash
sudo systemd-run --unit=nx-deploy --collect \
  -p Type=oneshot -p TimeoutStartSec=180 \
  /usr/bin/python3 "$PWD/nx/deploy.py" install \
  --probe-file /tmp/nx-gmsl-probe.json --expected 6
sudo journalctl -u nx-deploy -f
```

命令返回“Running as unit”只是已启动任务，不是安装成功。重新连接后检查 journal 中的最终结果，再运行 `sudo ./run.sh`。
