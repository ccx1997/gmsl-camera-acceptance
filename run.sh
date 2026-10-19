#!/usr/bin/env bash
# 开机后的“一键验收”入口。相机服务未运行时会启动配置中指定的 systemd unit。
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROFILE=${CAMERA_ACCEPTANCE_PROFILE:-}
if [ -z "$PROFILE" ]; then
  if [ -f /etc/gmsl-camera-acceptance/profile ]; then
    IFS= read -r PROFILE < /etc/gmsl-camera-acceptance/profile
  elif [[ "$(tr -d '\0' < /proc/device-tree/model 2>/dev/null)" == *"AGX Orin"* ]]; then
    PROFILE=$SCRIPT_DIR/profiles/geacx1-r35-current-6cam.json
  else
    echo "本机尚未选择验收配置。NX 请先按 docs/ORIN_NX_ENTRON.md 部署；其他机型请设置 CAMERA_ACCEPTANCE_PROFILE。" >&2
    exit 2
  fi
fi

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "需要 root 才能完整读取 I2C、设备节点和内核日志，正在通过 sudo 重新运行..."
  exec sudo -E "$0" "$@"
fi

echo "验收配置: $PROFILE"
echo "注意: 若相机服务尚未运行，本脚本会启动它，可能触发一次 PoC 电源复位和相机初始化。"
exec python3 "$SCRIPT_DIR/camera_acceptance.py" --profile "$PROFILE" --start "$@"
