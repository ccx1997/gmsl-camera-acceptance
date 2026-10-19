#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ $EUID -eq 0 ]] || { echo '请使用 sudo'; exit 1; }
[[ $(uname -m) == aarch64 ]] && grep -Eq 'R35 .*REVISION: *4\.1\b' /etc/nv_tegra_release || {
  echo '此安装包仅验证 aarch64 / Jetson Linux R35.4.1'; exit 1;
}
case "${1:-}" in
  --offline-rtsp)
    (cd bundles && sha256sum -c SHA256SUMS)
    if gst-inspect-1.0 rtspclientsink >/dev/null 2>&1; then
      echo 'rtspclientsink 已存在，不覆盖现有包。'
    else
      dpkg -i bundles/libgstrtspserver-1.0-0_1.16.2-3_arm64.deb bundles/gstreamer1.0-rtsp_1.16.2-3_arm64.deb
      gst-inspect-1.0 rtspclientsink >/dev/null
    fi
    ;;
  '')
    apt-get install -y --no-install-recommends --no-upgrade \
      python3 python3-gi gir1.2-gstreamer-1.0 v4l-utils i2c-tools ffmpeg psmisc iproute2 \
      gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad gstreamer1.0-rtsp
    ;;
  *) echo '用法: sudo ./nx/install-deps.sh [--offline-rtsp]'; exit 2 ;;
esac
