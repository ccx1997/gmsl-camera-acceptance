# 随仓库保存的运行文件

本目录约 29 MB（压缩后）。目标：aarch64、Ubuntu 20.04、Jetson Linux R35.4.1、厂家 TZTEK MAX9296 字符设备 ABI。安装时先验证 `SHA256SUMS`，展开到独立运行目录，明确设置库搜索路径。

## Git 存储方式

本版直接使用普通 Git 保存运行包，不使用 Git LFS。最大的受跟踪文件是 `stream-tools-aarch64.tar.gz`，约 27.37 MiB，低于 GitHub 的 50 MiB 警告线和 100 MiB 单文件限制（[官方说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)）。因此，在本次提交推送后，普通 `git clone` 即可取得实际二进制，不需要另外安装 Git LFS 或下载 LFS 对象。

这些是固定版本的部署依赖。后续若频繁更新大型运行包或增加 BSP 镜像，应重新评估 Git LFS / Release 附件，避免 Git 历史持续膨胀。完整离线发布包、现场照片和运行日志仍放在仓库之外。普通 clone 也不会补齐厂家 BSP 或系统依赖，需继续执行部署手册的预检查及依赖安装步骤。

| 文件 | 内容与来源 | 用途 |
|---|---|---|
| `camera-runtime-r35-aarch64.tar.gz` | 从已推流的 AGX 的 `camera_demo_geac` 复制；包含 `demo/camera_config_only`、其源文件/头文件、`libgeaccam` 及同套 sensorlib 软链接链 | 用户态相机初始化。安装器不使用包内历史 AGX JSON，而是生成 NX 专用 JSON |
| `stream-tools-aarch64.tar.gz` | 已运行 AGX 上的 MediaMTX v1.19.2 和 `tztek-jetson-tool-internal-trigger-camera` | RTSP 服务与串口 15 Hz 触发 |
| `libgstrtspserver-1.0-0_1.16.2-3_arm64.deb` | Ubuntu Focal arm64 官方包；下载时与设备 apt 元数据的 SHA256 比对 | RTSP 库 |
| `gstreamer1.0-rtsp_1.16.2-3_arm64.deb` | 同上 | `rtspclientsink` |

2026-10-19 从已运行 AGX 复制，并在 NX 上实际初始化、采集与解码验证。AGX 相机运行包来源路径为 `/home/nvidia/camera-migration/source-r36/home/nvidia/camera_new/camera_demo_geac`；目录名中的 `source-r36` 不代表本包已验证 R36，实际运行环境是 R35.4.1。

关键文件 SHA256：

```text
0b5c9b899d20d6199a95aa3a75f15e08e53cf015f1e38835f17079690ae28023  lib/libgeaccam.so.1.2.0
7dda18a1b696189b7a0ec372736a0c614ab9fa90c027f7a4ad5b48b4d58bdf64  lib/sensorlib/libisx031std.so.1.2.0
ef26536e220feb44032b8a3a334f06d102e084d336cedc6e51e876fe8571febb  lib/sensorlib/libmax9296.so.1.2.0
623e3ed541774de7ad94c3a916175993e3cc6a41e906f086f3a97fd426c1c2f3  lib/sensorlib/libyuv_cam.so.1.2.0
```

没有附带 BSP 刷机包、内核、DTB、AGX 业务程序或服务配置，也没有复制现场影像或账户凭据。厂家二进制保留原内容，本仓库不额外授予其许可证。MediaMTX 的 MIT 许可证见 `LICENSE.mediamtx`，对应 [v1.19.2 原始许可](https://github.com/bluenviron/mediamtx/blob/v1.19.2/LICENSE)。Ubuntu 包保留其内部版权文件；软件源为 [Ubuntu ports](https://ports.ubuntu.com/ubuntu-ports/pool/universe/g/gst-rtsp-server1.0/)。

手工核验：

```bash
cd nx/bundles
sha256sum -c SHA256SUMS
```

更新运行包时应重新生成 SHA256SUMS，并重新进行探测、初始化、6 路拉流、服务重启及回滚测试。不要只替换其中一个 `.so` 后继续沿用原验收结论。
