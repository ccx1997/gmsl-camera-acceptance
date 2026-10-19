# GMSL 相机部署与验收

支持保留的 AGX 验收流程，以及新增的 **TZTEK Orin NX / R35.4.1 / 殷创 0xc8 批次相机**部署流程。验收同时检查 I2C、链路锁定、视频格式、真实 RTSP 解码和内核采集错误。

- **新 NX 从零部署： [保姆级操作手册](docs/ORIN_NX_ENTRON.md)**
- [从六路失败到六路 PASS 的排查记录](docs/NX_FIX_RECORD.md)
- [原 AGX 部署与验收资料](docs/AGX_REFERENCE.md)
- [BSP 与文件依赖边界](DRIVER_AND_FILES.md)
- [随仓库附带的二进制、来源、版本和校验](nx/bundles/PROVENANCE.md)

## NX 快速入口

适用于已安装匹配厂家 BSP 的 NX。普通 NVIDIA 系统仅有 `/dev/video*` 不等于具备这块 8 口板的驱动；脚本会检查 4 组 MAX9296、I2C mux、PoC、触发串口及 2-lane/VC 设备树。R36、4-lane AGX、0x91 批次不适用本运行包。

在仓库根目录依次执行：

```bash
sudo ./nx/install-deps.sh
sudo python3 nx/deploy.py preflight
sudo python3 nx/probe.py --output /tmp/nx-gmsl-probe.json
sudo python3 nx/deploy.py install --probe-file /tmp/nx-gmsl-probe.json --expected 6
sudo ./run.sh
```

探测会复位相机电源；有采集进程占用时会拒绝执行。安装会校验文件、生成实际接线配置、备份旧服务、初始化、启动并进行 30 秒持续出帧检查。失败会尝试恢复此前文件和服务；不会杀死不相关的采集程序，也不会刷写内核或设备树。

公网软件源不通、且只缺 RTSP 插件时，可用随仓库带的两个 Ubuntu arm64 安装包：

```bash
sudo ./nx/install-deps.sh --offline-rtsp
sudo python3 nx/deploy.py preflight
```

这只是 RTSP 补包，不能代替全部系统依赖或 BSP。第一次成功后，`nx-gmsl.target` 会随系统开机启动。日常只需：

```bash
sudo ./run.sh
sudo python3 /opt/gmsl-nx/check_streams.py
```

同局域网播放 `rtsp://<NX-IP>:8554/cam0`～`cam5`，使用 RTSP over TCP。端口顺序、远程 SSH 隧道、重启、回滚和故障处理见完整手册。

## 已验证结果与范围

2026-10-19，6 台相机在 NX 上通过：1920×1536 YUYV → H.264，约 15 fps；六路 RTSP 独立解码成功；最终验收 PASS。安装实机复验记录见 [修复记录](docs/NX_FIX_RECORD.md)。

本次接线对应 `/dev/video0,1,2,3,5,7`，但**新机器必须重新探测**。P0～P7 是软件拓扑标签，不能当作已经确认的机壳插座丝印。不同接线会重新生成 camN 与 videoN 的对应关系。

自动 PASS 只代表这次检查通过。现场仍需看画面方向、遮挡和视角，并完成整机冷启动及长时间运行验证。

## 验收输出

`run.sh` 优先使用 `CAMERA_ACCEPTANCE_PROFILE`；其次使用 NX 安装器生成的 `/etc/gmsl-camera-acceptance/profile`；未部署的 NX 会提示先安装，不再误用 AGX 配置。AGX 未设置 NX selector 时沿用原 AGX profile。

- `results/YYYYMMDD-HHMMSS/report.html`：中文报告、每路实拍图。
- `report.json`、`report.md`：完整记录。
- 返回码：`0` PASS、`1` WARN、`2` FAIL。

只检查、不尝试启动服务：

```bash
sudo python3 camera_acceptance.py --profile /opt/gmsl-nx/cfg/acceptance.json
```

## 打包复制到新机器

```bash
./nx/package.sh /tmp/gmsl-camera-acceptance-nx.tar.gz
```

该包包含当前工作区的文档、脚本和二进制，不依赖 `git archive HEAD`，也不包含 `.git`、现场照片、运行日志、SSH 凭据或个人配置。复制、解压方法见 [完整手册](docs/ORIN_NX_ENTRON.md)。

## 开发验证

```bash
python3 -m unittest discover -s nx/tests -v
python3 nx/deploy.py render --ports 0,1,2,3,5,7 --output /tmp/nx-config-preview
```

`render` 可以在非 Jetson 电脑运行，只生成配置预览，不连接硬件或改变系统服务。
