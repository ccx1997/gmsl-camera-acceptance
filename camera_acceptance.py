#!/usr/bin/env python3
"""GMSL camera installation acceptance tool.

The tool is deliberately conservative: it never resets camera power and never
stops a running camera process.  Starting the profile's systemd unit only
happens when --start is explicitly supplied (the run.sh wrapper does this).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE = SCRIPT_DIR / "profiles" / "geacx1-r35-current-6cam.json"


def run_command(
    command: Sequence[str], timeout: float = 8, env: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return {
            "command": list(command),
            "returncode": proc.returncode,
            "output": proc.stdout.strip(),
            "elapsed_s": round(time.monotonic() - started, 3),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return {
            "command": list(command),
            "returncode": 124,
            "output": output.strip(),
            "elapsed_s": round(time.monotonic() - started, 3),
            "timed_out": True,
        }
    except OSError as exc:
        return {
            "command": list(command),
            "returncode": 127,
            "output": str(exc),
            "elapsed_s": round(time.monotonic() - started, 3),
            "timed_out": False,
        }


def read_text(path: Path, binary_nul: bool = False) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if binary_nul:
        data = data.replace(b"\x00", b"")
    return data.decode(errors="replace").strip()


def parse_number(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 0)


def normalize_hex(value: str) -> str:
    match = re.search(r"0x[0-9a-fA-F]+", value or "")
    if not match:
        return ""
    return f"0x{int(match.group(0), 16):x}"


def most_common(values: Iterable[str]) -> str:
    cleaned = [value for value in values if value]
    return Counter(cleaned).most_common(1)[0][0] if cleaned else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_issue(issues: List[Dict[str, str]], level: str, code: str, message: str) -> None:
    issues.append({"level": level, "code": code, "message": message})


def dependency_check(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    commands = profile.get(
        "required_commands",
        [
            "python3",
            "v4l2-ctl",
            "media-ctl",
            "i2cdetect",
            "i2ctransfer",
            "ffmpeg",
            "journalctl",
            "systemctl",
            "fuser",
        ],
    )
    results = [
        {"command": command, "found": bool(shutil.which(command)), "path": shutil.which(command) or ""}
        for command in commands
    ]
    if shutil.which("gst-inspect-1.0"):
        for plugin in profile.get("required_gstreamer_plugins", []):
            check = run_command(["gst-inspect-1.0", plugin], timeout=12)
            results.append(
                {
                    "command": f"gst:{plugin}",
                    "found": check["returncode"] == 0,
                    "path": "GStreamer plugin" if check["returncode"] == 0 else check["output"][-300:],
                }
            )
    for package in profile.get("required_packages", []):
        name = package["name"] if isinstance(package, dict) else str(package)
        version_prefix = package.get("version_prefix", "") if isinstance(package, dict) else ""
        if shutil.which("dpkg-query"):
            check = run_command(["dpkg-query", "-W", "-f=${Version}", name], timeout=5)
            version = check["output"] if check["returncode"] == 0 else ""
            results.append(
                {
                    "command": f"pkg:{name}",
                    "found": bool(version),
                    "path": version,
                    "version_match": bool(version.startswith(version_prefix)) if version_prefix and version else None,
                    "expected_version": version_prefix,
                }
            )
    return results


def i2c_read16_data8(bus: int, address: int, register: int) -> Dict[str, Any]:
    command = [
        "i2ctransfer",
        "-f",
        "-y",
        str(bus),
        f"w2@0x{address:02x}",
        f"0x{register >> 8:02x}",
        f"0x{register & 0xff:02x}",
        "r1",
    ]
    result = run_command(command, timeout=3)
    result["value"] = normalize_hex(result["output"]) if result["returncode"] == 0 else ""
    return result


def inspect_i2c(profile: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    samples = max(1, int(profile.get("i2c_samples", 3)))
    pause = float(profile.get("i2c_sample_pause_s", 0.15))
    status_register = parse_number(profile.get("deserializer_status_register", "0x0013"))
    id_register = parse_number(profile.get("serializer_id_register", "0x000d"))
    locked_values = {normalize_hex(item) for item in profile.get("locked_status_values", [])}
    unlocked_values = {normalize_hex(item) for item in profile.get("unlocked_status_values", [])}
    serializer_names = {
        normalize_hex(key): value for key, value in profile.get("serializer_id_names", {}).items()
    }

    pairs: List[Dict[str, Any]] = []
    pair_by_name: Dict[str, Dict[str, Any]] = {}
    for pair in profile.get("deserializers", []):
        bus = int(pair["bus"])
        address = parse_number(pair["address"])
        values = []
        raw = []
        for sample in range(samples):
            result = i2c_read16_data8(bus, address, status_register)
            raw.append(result)
            values.append(result.get("value", ""))
            if sample + 1 < samples:
                time.sleep(pause)
        value = most_common(values)
        if value in locked_values:
            state = "locked"
        elif value in unlocked_values:
            state = "unlocked"
        elif value:
            state = "unknown"
        else:
            state = "no_ack"
        item = {
            **pair,
            "status_register": f"0x{status_register:04x}",
            "samples": values,
            "status_value": value,
            "state": state,
            "raw": raw,
        }
        pairs.append(item)
        pair_by_name[str(pair["name"])] = item

    ports: Dict[str, Dict[str, Any]] = {}
    for port in profile.get("ports", []):
        bus = int(port["bus"])
        address = parse_number(port["serializer_address"])
        values = []
        raw = []
        for sample in range(samples):
            result = i2c_read16_data8(bus, address, id_register)
            raw.append(result)
            values.append(result.get("value", ""))
            if sample + 1 < samples:
                time.sleep(pause)
        value = most_common(values)
        ports[str(port["name"])] = {
            "samples": values,
            "id": value,
            "serializer_name": serializer_names.get(value, "unknown" if value else "no response"),
            "responding": bool(value),
            "raw": raw,
        }
    return pairs, ports


def parse_v4l2_format(output: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"width": None, "height": None, "pixel_format": ""}
    size = re.search(r"Width/Height\s*:\s*(\d+)\s*/\s*(\d+)", output)
    if size:
        result["width"], result["height"] = int(size.group(1)), int(size.group(2))
    pixel = re.search(r"Pixel Format\s*:\s*'([^']+)'", output)
    if pixel:
        result["pixel_format"] = pixel.group(1)
    return result


def inspect_video_node(port: Dict[str, Any]) -> Dict[str, Any]:
    node = Path(port["video_node"])
    sys_name = Path("/sys/class/video4linux") / node.name / "name"
    result: Dict[str, Any] = {
        "exists": node.exists(),
        "driver_name": read_text(sys_name),
        "width": None,
        "height": None,
        "pixel_format": "",
        "busy": False,
        "users": "",
        "query": {},
    }
    if not node.exists():
        return result
    query = run_command(["v4l2-ctl", "-d", str(node), "--get-fmt-video"], timeout=4)
    result["query"] = query
    result.update(parse_v4l2_format(query["output"]))
    if shutil.which("fuser"):
        users = run_command(["fuser", str(node)], timeout=3)
        result["busy"] = users["returncode"] == 0
        result["users"] = users["output"]
    return result


def latest_snapshot(snapshot_dir: Path, channel: int) -> Optional[Path]:
    candidates = []
    try:
        candidates = list(snapshot_dir.glob(f"{channel}_*.jpg"))
    except OSError:
        pass
    candidates = [path for path in candidates if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def capture_port(
    port: Dict[str, Any], video: Dict[str, Any], capture_cfg: Dict[str, Any], image_dir: Path
) -> Dict[str, Any]:
    target = image_dir / f"{port['name'].lower()}.jpg"
    channel = int(port.get("snapshot_channel", port.get("logical_index", 0)))
    freshness = float(capture_cfg.get("snapshot_freshness_s", 10))
    snapshot_dir = Path(capture_cfg.get("snapshot_dir", "/dev/shm/pic"))
    snapshot = latest_snapshot(snapshot_dir, channel)
    if snapshot:
        age = time.time() - snapshot.stat().st_mtime
        if age <= freshness:
            shutil.copy2(snapshot, target)
            return {
                "ok": True,
                "source": "fresh_snapshot",
                "source_path": str(snapshot),
                "age_s": round(age, 2),
                "image": str(target),
                "detail": "",
            }

    rtsp_template = capture_cfg.get("rtsp_url_template", "")
    if rtsp_template and shutil.which("ffmpeg"):
        url = rtsp_template.format(
            channel=channel, logical=port.get("logical_index", channel), port=port["name"].lower()
        )
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(target),
        ]
        result = run_command(command, timeout=float(capture_cfg.get("capture_timeout_s", 15)))
        if result["returncode"] == 0 and target.exists() and target.stat().st_size > 0:
            return {
                "ok": True,
                "source": "rtsp",
                "source_path": url,
                "age_s": 0,
                "image": str(target),
                "detail": "",
            }
        rtsp_error = result["output"][-800:]
    else:
        rtsp_error = "RTSP not configured or ffmpeg missing"

    if video.get("busy"):
        return {
            "ok": False,
            "source": "busy",
            "source_path": "",
            "age_s": None,
            "image": "",
            "detail": f"video node is busy and no fresh snapshot/RTSP frame was available: {rtsp_error}",
        }
    if not video.get("exists"):
        return {
            "ok": False,
            "source": "missing_node",
            "source_path": "",
            "age_s": None,
            "image": "",
            "detail": "video node does not exist",
        }
    if not shutil.which("ffmpeg"):
        return {
            "ok": False,
            "source": "missing_ffmpeg",
            "source_path": "",
            "age_s": None,
            "image": "",
            "detail": "ffmpeg is not installed",
        }

    width = video.get("width") or int(capture_cfg.get("fallback_width", 1920))
    height = video.get("height") or int(capture_cfg.get("fallback_height", 1080))
    pixel = (video.get("pixel_format") or "").upper()
    ffmpeg_format = {"YUYV": "yuyv422", "YUY2": "yuyv422", "UYVY": "uyvy422"}.get(pixel)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2"]
    if ffmpeg_format:
        command.extend(["-input_format", ffmpeg_format])
    command.extend(
        [
            "-video_size",
            f"{width}x{height}",
            "-i",
            port["video_node"],
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(target),
        ]
    )
    result = run_command(command, timeout=float(capture_cfg.get("capture_timeout_s", 15)))
    ok = result["returncode"] == 0 and target.exists() and target.stat().st_size > 0
    return {
        "ok": ok,
        "source": "v4l2" if ok else "v4l2_failed",
        "source_path": port["video_node"],
        "age_s": 0 if ok else None,
        "image": str(target) if ok else "",
        "detail": "" if ok else result["output"][-800:],
    }


def inspect_vendor_config(profile: Dict[str, Any]) -> Dict[str, Any]:
    config_path = Path(profile.get("vendor_config", ""))
    result: Dict[str, Any] = {"path": str(config_path), "exists": config_path.is_file(), "ports": {}}
    if not config_path.is_file():
        return result
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = str(exc)
        return result
    for key, value in data.items():
        if key.startswith("port_") and isinstance(value, dict):
            result["ports"][key[5:]] = {
                field: value.get(field)
                for field in (
                    "width",
                    "height",
                    "format",
                    "fps",
                    "extra_mode",
                    "config_index",
                    "deserial_index",
                    "deserial_port",
                )
            }
    result["global"] = data.get("global", {})
    return result


def inspect_runtime(profile: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    expected_hashes: Dict[str, str] = {}
    manifest_path = Path(profile.get("sha256_manifest", ""))
    if not manifest_path.is_absolute():
        manifest_path = SCRIPT_DIR / manifest_path
    if manifest_path.is_file():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                expected_hashes[parts[1].lstrip("*")] = parts[0].lower()

    files = []
    for value in profile.get("runtime_paths", []):
        path = Path(value)
        item: Dict[str, Any] = {
            "path": str(path),
            "exists": path.exists(),
            "kind": "directory" if path.is_dir() else "file",
            "sha256": "",
            "expected_sha256": expected_hashes.get(str(path), ""),
            "hash_match": None,
        }
        if path.is_file():
            try:
                item["sha256"] = sha256_file(path)
                if item["expected_sha256"]:
                    item["hash_match"] = item["sha256"] == item["expected_sha256"]
            except OSError as exc:
                item["error"] = str(exc)
        files.append(item)

    libraries = []
    for value in profile.get("ldd_targets", []):
        path = Path(value)
        if path.is_file() and shutil.which("ldd"):
            result = run_command(["ldd", str(path)], timeout=8)
            missing = [line.strip() for line in result["output"].splitlines() if "not found" in line]
            libraries.append({"path": str(path), "output": result["output"], "missing": missing})
        else:
            libraries.append({"path": str(path), "output": "target or ldd missing", "missing": ["unavailable"]})
    return files, libraries


def inspect_kernel(minutes: int) -> Dict[str, Any]:
    if not shutil.which("journalctl"):
        return {"available": False, "output": "journalctl missing"}
    result = run_command(
        ["journalctl", "-k", f"--since=-{minutes} minutes", "--no-pager", "-o", "cat"],
        timeout=15,
    )
    if result["returncode"] != 0:
        return {"available": False, "output": result["output"]}
    matching = []
    corr_by_channel: Dict[str, int] = defaultdict(int)
    uncorr_by_channel: Dict[str, int] = defaultdict(int)
    timeout_count = 0
    recover_count = 0
    for line in result["output"].splitlines():
        if not re.search(r"tegra-capture|nvcsi|vi5|CHANSEL|CSIMUX", line, re.I):
            continue
        if not re.search(r"corr_err|uncorr_err|timeout|err_rec|CHANSEL|CSIMUX", line, re.I):
            continue
        matching.append(line)
        channel = re.search(r"chan_id\s+(\d+)", line)
        key = channel.group(1) if channel else "unknown"
        if "corr_err" in line and "uncorr_err" not in line:
            corr_by_channel[key] += 1
        if "uncorr_err" in line:
            uncorr_by_channel[key] += 1
        if re.search(r"request timed out|timeout after|capture.*timeout", line, re.I):
            timeout_count += 1
        if "err_rec" in line:
            recover_count += 1
    return {
        "available": True,
        "window_minutes": minutes,
        "corr_by_channel": dict(corr_by_channel),
        "uncorr_by_channel": dict(uncorr_by_channel),
        "corr_total": sum(corr_by_channel.values()),
        "uncorr_total": sum(uncorr_by_channel.values()),
        "timeout_count": timeout_count,
        "recover_count": recover_count,
        "matching_lines": matching[-1000:],
    }


def md_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def issue_summary(issues: List[Dict[str, str]]) -> Tuple[str, int, int]:
    failures = sum(item["level"] == "FAIL" for item in issues)
    warnings = sum(item["level"] == "WARN" for item in issues)
    return ("FAIL" if failures else "WARN" if warnings else "PASS", failures, warnings)


ISSUE_LABELS = {
    "NOT_ROOT": "未使用 root 权限，检查结果可能不完整",
    "MISSING_DEPENDENCY": "缺少验收工具依赖",
    "DEPENDENCY_VERSION_MISMATCH": "工具版本与基准不一致",
    "START_FAILED": "相机初始化服务启动失败",
    "UNIT_INACTIVE": "相机初始化服务未运行",
    "MISSING_RUNTIME": "缺少相机运行文件",
    "HASH_MISMATCH": "相机运行文件与基准版本不一致",
    "MISSING_LIBRARY": "相机程序缺少动态库",
    "COUNT_MISMATCH": "实测相机数量与预期不符",
    "CAMERA_NO_ACK": "预期相机无 I2C 应答",
    "LINK_UNLOCKED": "GMSL 链路未锁定",
    "VIDEO_NODE_MISSING": "视频节点不存在",
    "NO_FRAME": "未取得验收画面",
    "UNEXPECTED_CAMERA": "配置为空口但检测到相机",
    "SERIALIZER_ID_MISMATCH": "加串器 ID 与预期不符",
    "V4L2_SIZE_MISMATCH": "V4L2 尺寸与验收基准不一致",
    "CONFIG_V4L2_SIZE_MISMATCH": "厂家配置与 V4L2 尺寸不一致",
    "VI_CORRECTABLE_ERRORS": "VI 出现可恢复错误 corr_err",
    "VI_FATAL_ERRORS": "VI 出现超时或不可恢复错误",
    "KERNEL_LOG_UNAVAILABLE": "无法读取相机内核日志",
}


def render_terminal_summary(report: Dict[str, Any], output_dir: Path) -> None:
    """Print a short, self-contained acceptance result for shop-floor use."""
    status, failures, warnings = issue_summary(report["issues"])
    pair_map = {str(item["name"]): item for item in report["deserializers"]}
    expected_ports = [port for port in report["ports"] if port.get("expected")]
    capture_enabled = any(
        port.get("capture", {}).get("source") != "disabled" for port in expected_ports
    )

    route_rows = []
    ok_routes: List[str] = []
    failed_routes: List[str] = []
    empty_routes: List[str] = []
    unexpected_routes: List[str] = []
    for port in report["ports"]:
        pair = pair_map.get(str(port.get("pair")), {})
        i2c = port.get("i2c", {})
        video = port.get("video", {})
        capture = port.get("capture", {})
        expected = bool(port.get("expected"))
        route = f"{port['name']}({port['video_node']})"
        link_ok = pair.get("state") == "locked"
        base_ok = bool(i2c.get("responding")) and link_ok and bool(video.get("exists"))
        route_ok = base_ok and (bool(capture.get("ok")) if capture_enabled else True)

        if expected and route_ok:
            label = "OK" if capture_enabled else "链路OK"
            ok_routes.append(route)
        elif expected:
            label = "失败"
            failed_routes.append(route)
        elif i2c.get("responding"):
            label = "非预期"
            unexpected_routes.append(route)
        else:
            label = "空口"
            empty_routes.append(route)

        pixel = video.get("pixel_format") or "?"
        dimensions = f"{video.get('width') or '?'}x{video.get('height') or '?'}"
        route_rows.append(
            "  [{label:<6}] {name:<3} {node:<12} ID={sid:<6} LOCK={lock:<6} {pixel} {dimensions}".format(
                label=label,
                name=str(port["name"]),
                node=str(port["video_node"]),
                sid=i2c.get("id") or "无应答",
                lock=pair.get("status_value") or "无应答",
                pixel=pixel,
                dimensions=dimensions,
            )
        )

    if not capture_enabled:
        verdict = "未完整验收（本次未抓图，只检查链路）"
    elif failures:
        verdict = "不OK（存在必须处理的失败项）"
    elif warnings:
        verdict = "OK（全部预期相机均出图，但有警告）"
    else:
        verdict = "OK（自动检查通过）"

    print("\n" + "=" * 72)
    print(f"GMSL 相机验收结论：{verdict}")
    print(
        f"自动状态：{status}  |  预期 {report['expected_camera_count']} 路  |  "
        f"检测 {report['detected_camera_count']} 路  |  出图 {report['captured_camera_count']} 路"
    )
    healthy_label = "正常出图" if capture_enabled else "链路正常"
    print(f"{healthy_label}：{', '.join(ok_routes) if ok_routes else '无'}")
    print(f"失败路：  {', '.join(failed_routes) if failed_routes else '无'}")
    print(f"空口：    {', '.join(empty_routes) if empty_routes else '无'}")
    if unexpected_routes:
        print(f"非预期：  {', '.join(unexpected_routes)}")

    print("\n逐路状态：")
    for row in route_rows:
        print(row)

    important = [item for item in report["issues"] if item["level"] in ("FAIL", "WARN")]
    print("\n关键问题：")
    if not important:
        print("  无自动检查异常；仍需人工确认视角、方向、遮挡和画质。")
    else:
        grouped: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for item in important:
            grouped[(item["level"], item["code"])].append(item["message"])
        for (level, code), messages in grouped.items():
            label = ISSUE_LABELS.get(code, code)
            ports = sorted(
                set(re.findall(r"\bP\d+\b", " ".join(messages))),
                key=lambda value: int(value[1:]),
            )
            if len(messages) > 1:
                scope = (
                    f"（涉及 {', '.join(ports)}；共 {len(messages)} 项）"
                    if ports
                    else f"（共 {len(messages)} 项）"
                )
                print(f"  [{level}] {label}{scope}")
            else:
                print(f"  [{level}] {label}：{messages[0]}")

    print(f"\n详细图文报告：{output_dir / 'report.html'}")
    print("=" * 72)


def render_markdown(report: Dict[str, Any], output_dir: Path) -> str:
    status, failures, warnings = issue_summary(report["issues"])
    lines = [
        "# GMSL 相机安装验收报告",
        "",
        f"- 结论：**{status}**（失败 {failures}，警告 {warnings}）",
        f"- 时间：{report['generated_at']}",
        f"- 配置：{report['profile_name']}",
        f"- 平台：{report['system']['model']} / {report['system']['kernel']}",
        "",
        "## 端口状态",
        "",
        "| 端口 | 预期 | I2C bus / Link | 节点 | 加串器 | 解串器 | 格式 | 画面 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    pair_map = {str(item["name"]): item for item in report["deserializers"]}
    for port in report["ports"]:
        pair = pair_map.get(str(port["pair"]), {})
        i2c = port["i2c"]
        video = port["video"]
        capture = port["capture"]
        fmt = (
            f"{video.get('pixel_format') or '?'} "
            f"{video.get('width') or '?'}×{video.get('height') or '?'}"
        )
        image = "成功" if capture.get("ok") else "空口跳过" if not port.get("expected") else "失败"
        lines.append(
            "| {name} | {expected} | {bus}/{link} | {node} | {sid} {sname} | {lock} {state} | {fmt} | {image} |".format(
                name=md_escape(port["name"]),
                expected="有相机" if port.get("expected") else "空口",
                bus=port["bus"],
                link=port["link"],
                node=md_escape(port["video_node"]),
                sid=md_escape(i2c.get("id") or "无应答"),
                sname=md_escape(i2c.get("serializer_name", "")),
                lock=md_escape(pair.get("status_value") or "无应答"),
                state=md_escape(pair.get("state", "")),
                fmt=md_escape(fmt),
                image=image,
            )
        )

    lines.extend(["", "## 异常与提示", ""])
    if report["issues"]:
        for issue in report["issues"]:
            lines.append(f"- **{issue['level']} / {issue['code']}**：{issue['message']}")
    else:
        lines.append("- 未发现自动检查异常。仍需人工确认画面方向、遮挡和安装视角。")

    lines.extend(["", "## 实拍画面", ""])
    for port in report["ports"]:
        capture = port["capture"]
        if capture.get("ok") and capture.get("image"):
            relative = Path(capture["image"]).relative_to(output_dir)
            lines.extend([f"### {port['name']}", "", f"![{port['name']}]({relative.as_posix()})", ""])
    lines.extend(
        [
            "> 人工验收要点：画面是否倒置、左右是否符合安装方位、是否被结构件遮挡、颜色是否正常、是否有青条/撕裂/重影。",
            "",
            "## 依赖与运行文件",
            "",
            "| 项目 | 状态 | 路径/说明 |",
            "|---|---|---|",
        ]
    )
    for dep in report["dependencies"]:
        lines.append(f"| 命令 `{dep['command']}` | {'OK' if dep['found'] else 'MISSING'} | {md_escape(dep['path'])} |")
    for item in report["runtime_files"]:
        hash_state = ""
        if item.get("hash_match") is True:
            hash_state = "，SHA256 匹配"
        elif item.get("hash_match") is False:
            hash_state = "，SHA256 不匹配"
        lines.append(
            f"| 运行文件 | {'OK' if item['exists'] else 'MISSING'} | {md_escape(item['path'])}{hash_state} |"
        )
    lines.extend(
        [
            "",
            "## 原始诊断文件",
            "",
            "- [完整 JSON](report.json)",
            "- [I2C 总线](i2c-list.txt)",
            "- [V4L2 设备](v4l2-devices.txt)",
            "- [Media 拓扑](media-topology.txt)",
            "- [相机内核日志](kernel-camera.log)",
            "- [动态库解析](runtime-libraries.txt)",
            "",
        ]
    )
    return "\n".join(lines)


def render_html(report: Dict[str, Any], output_dir: Path) -> str:
    status, failures, warnings = issue_summary(report["issues"])
    status_class = status.lower()
    pair_map = {str(item["name"]): item for item in report["deserializers"]}
    rows = []
    cards = []
    for port in report["ports"]:
        pair = pair_map.get(str(port["pair"]), {})
        video, i2c, capture = port["video"], port["i2c"], port["capture"]
        healthy = capture.get("ok") and (not port.get("expected") or i2c.get("responding"))
        rows.append(
            "<tr class='{row_class}'><td>{name}</td><td>{expected}</td><td>{bus}/{link}</td>"
            "<td>{node}</td><td>{sid} {serializer}</td><td>{lock} {lock_state}</td>"
            "<td>{pixel} {width}×{height}</td><td>{capture}</td></tr>".format(
                row_class="ok" if healthy else "bad" if port.get("expected") else "idle",
                name=html.escape(str(port["name"])),
                expected="有相机" if port.get("expected") else "空口",
                bus=port["bus"],
                link=html.escape(str(port["link"])),
                node=html.escape(str(port["video_node"])),
                sid=html.escape(i2c.get("id") or "无应答"),
                serializer=html.escape(i2c.get("serializer_name", "")),
                lock=html.escape(pair.get("status_value") or "无应答"),
                lock_state=html.escape(pair.get("state", "")),
                pixel=html.escape(video.get("pixel_format") or "?"),
                width=video.get("width") or "?",
                height=video.get("height") or "?",
                capture="成功" if capture.get("ok") else "空口跳过" if not port.get("expected") else "失败",
            )
        )
        if capture.get("ok") and capture.get("image"):
            relative = Path(capture["image"]).relative_to(output_dir).as_posix()
            cards.append(
                f"<figure><figcaption>{html.escape(str(port['name']))}</figcaption>"
                f"<a href='{html.escape(relative)}'><img src='{html.escape(relative)}'></a></figure>"
            )
    issue_items = "".join(
        f"<li class='{item['level'].lower()}'><strong>{html.escape(item['level'])} / "
        f"{html.escape(item['code'])}</strong>：{html.escape(item['message'])}</li>"
        for item in report["issues"]
    ) or "<li>未发现自动检查异常；仍需人工确认安装视角。</li>"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>GMSL 相机安装验收报告</title><style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:24px;background:#f4f6f8;color:#17212b}}
.summary{{padding:18px;border-radius:10px;background:white;border-left:8px solid #768390}}
.summary.pass{{border-color:#1a7f37}} .summary.warn{{border-color:#bf8700}} .summary.fail{{border-color:#cf222e}}
table{{border-collapse:collapse;width:100%;background:white;margin:18px 0}} th,td{{border:1px solid #d0d7de;padding:8px;text-align:left}}
th{{background:#eef1f4}} tr.ok td:last-child{{color:#1a7f37;font-weight:700}} tr.bad{{background:#fff0f0}}
.gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
figure{{background:white;padding:10px;margin:0;border-radius:8px}} figcaption{{font-weight:700;margin-bottom:8px}}
img{{display:block;width:100%;height:auto}} li{{margin:7px 0}} li.warn{{color:#7a4d00}} li.fail{{color:#b42318}}
code{{background:#eaeef2;padding:2px 5px;border-radius:4px}} a{{color:#0969da}}
</style></head><body>
<h1>GMSL 相机安装验收报告</h1>
<section class="summary {status_class}"><h2>{status}</h2>
<p>失败 {failures}，警告 {warnings}；{html.escape(report['generated_at'])}</p>
<p>{html.escape(report['profile_name'])} · {html.escape(report['system']['model'])} · {html.escape(report['system']['kernel'])}</p></section>
<h2>端口状态</h2><table><thead><tr><th>端口</th><th>预期</th><th>I2C/Link</th><th>节点</th><th>加串器</th><th>解串器</th><th>格式</th><th>画面</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>异常与提示</h2><ul>{issue_items}</ul>
<h2>实拍画面</h2><p>请人工确认方向、视角、遮挡、颜色、青条、撕裂和重影。</p><section class="gallery">{''.join(cards)}</section>
<h2>原始诊断</h2><p><a href="report.md">Markdown 报告</a> · <a href="report.json">完整 JSON</a> ·
<a href="kernel-camera.log">内核日志</a> · <a href="media-topology.txt">Media 拓扑</a></p>
</body></html>"""


def build_report(args: argparse.Namespace, profile: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    issues: List[Dict[str, str]] = []
    system = {
        "model": read_text(Path("/proc/device-tree/model"), binary_nul=True) or "unknown",
        "compatible": read_text(Path("/sys/firmware/devicetree/base/compatible"), binary_nul=True),
        "kernel": os.uname().release,
        "architecture": os.uname().machine,
        "l4t_release": read_text(Path("/etc/nv_tegra_release")),
        "root": os.geteuid() == 0,
    }
    if not system["root"]:
        add_issue(issues, "WARN", "NOT_ROOT", "当前不是 root；I2C、内核日志或视频节点检查可能不完整。")

    dependencies = dependency_check(profile)
    for dep in dependencies:
        if not dep["found"]:
            add_issue(issues, "FAIL", "MISSING_DEPENDENCY", f"缺少依赖 {dep['command']}。")
        elif dep.get("version_match") is False:
            add_issue(
                issues,
                "WARN",
                "DEPENDENCY_VERSION_MISMATCH",
                f"{dep['command']} 版本为 {dep.get('path')}，基准要求前缀 {dep.get('expected_version')}。",
            )

    unit = profile.get("startup_systemd_unit", "")
    startup: Dict[str, Any] = {"unit": unit, "start_requested": args.start}
    if unit and shutil.which("systemctl"):
        before = run_command(["systemctl", "is-active", unit], timeout=5)
        startup["state_before"] = before["output"]
        if args.start and before["output"] != "active":
            startup["start_result"] = run_command(
                ["systemctl", "start", unit], timeout=float(profile.get("startup_timeout_s", 360))
            )
            if startup["start_result"]["returncode"] != 0:
                add_issue(issues, "FAIL", "START_FAILED", f"相机服务 {unit} 启动失败。")
            else:
                time.sleep(float(profile.get("post_start_wait_s", 3)))
        after = run_command(["systemctl", "is-active", unit], timeout=5)
        startup["state_after"] = after["output"]
        if after["output"] != "active":
            add_issue(issues, "WARN", "UNIT_INACTIVE", f"相机服务 {unit} 当前状态为 {after['output'] or 'unknown'}。")

    raw_i2c = run_command(["i2cdetect", "-l"], timeout=8) if shutil.which("i2cdetect") else {"output": ""}
    raw_v4l = run_command(["v4l2-ctl", "--list-devices"], timeout=10) if shutil.which("v4l2-ctl") else {"output": ""}
    raw_media = run_command(["media-ctl", "-p"], timeout=15) if shutil.which("media-ctl") else {"output": ""}
    (output_dir / "i2c-list.txt").write_text(raw_i2c.get("output", "") + "\n", encoding="utf-8")
    (output_dir / "v4l2-devices.txt").write_text(raw_v4l.get("output", "") + "\n", encoding="utf-8")
    (output_dir / "media-topology.txt").write_text(raw_media.get("output", "") + "\n", encoding="utf-8")

    if shutil.which("i2ctransfer"):
        deserializers, i2c_ports = inspect_i2c(profile)
    else:
        deserializers, i2c_ports = [], {}

    vendor_config = inspect_vendor_config(profile)
    runtime_files, runtime_libraries = inspect_runtime(profile)
    for item in runtime_files:
        if not item["exists"]:
            add_issue(issues, "FAIL", "MISSING_RUNTIME", f"缺少运行文件：{item['path']}")
        elif item.get("hash_match") is False:
            add_issue(issues, "WARN", "HASH_MISMATCH", f"运行文件版本与基准机不同：{item['path']}")
    for library in runtime_libraries:
        if library["missing"]:
            add_issue(issues, "FAIL", "MISSING_LIBRARY", f"{library['path']} 存在未解析动态库：{', '.join(library['missing'])}")
    (output_dir / "runtime-libraries.txt").write_text(
        "\n\n".join(f"[{item['path']}]\n{item['output']}" for item in runtime_libraries) + "\n",
        encoding="utf-8",
    )

    port_results: List[Dict[str, Any]] = []
    for port in profile.get("ports", []):
        video = inspect_video_node(port)
        i2c = i2c_ports.get(str(port["name"]), {"responding": False, "id": "", "serializer_name": "unknown"})
        item = {**port, "video": video, "i2c": i2c, "capture": {"ok": False, "source": "disabled"}}
        config_port = vendor_config.get("ports", {}).get(str(port.get("logical_index")))
        item["vendor_config"] = config_port or {}
        port_results.append(item)

    image_dir = output_dir / "images"
    image_dir.mkdir(exist_ok=True)
    if not args.no_capture:
        workers = max(1, min(int(profile.get("capture_workers", 4)), len(port_results) or 1))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {}
            for item in port_results:
                if item.get("expected") or item["i2c"].get("responding"):
                    future = executor.submit(capture_port, item, item["video"], profile.get("capture", {}), image_dir)
                    future_map[future] = item
                else:
                    item["capture"] = {"ok": False, "source": "empty_expected", "detail": ""}
            for future in as_completed(future_map):
                future_map[future]["capture"] = future.result()

    pair_map = {str(item["name"]): item for item in deserializers}
    expected_count = int(args.expected if args.expected is not None else profile.get("expected_camera_count", 0))
    detected_count = sum(item["i2c"].get("responding") for item in port_results)
    captured_count = sum(item["capture"].get("ok") for item in port_results)
    if expected_count and detected_count != expected_count:
        add_issue(issues, "FAIL", "COUNT_MISMATCH", f"预期 {expected_count} 台，I2C 实际检测到 {detected_count} 台。")

    for item in port_results:
        expected = bool(item.get("expected"))
        i2c, video, capture = item["i2c"], item["video"], item["capture"]
        pair = pair_map.get(str(item["pair"]), {})
        if expected and not i2c.get("responding"):
            add_issue(issues, "FAIL", "CAMERA_NO_ACK", f"{item['name']} 预期有相机，但加串器无 I2C 应答。")
        if expected and pair.get("state") in ("unlocked", "no_ack"):
            add_issue(issues, "FAIL", "LINK_UNLOCKED", f"{item['name']} 所在解串器未锁定（{pair.get('status_value') or '无应答'}）。")
        if expected and not video.get("exists"):
            add_issue(issues, "FAIL", "VIDEO_NODE_MISSING", f"{item['name']} 对应节点 {item['video_node']} 不存在。")
        if expected and not args.no_capture and not capture.get("ok"):
            add_issue(issues, "FAIL", "NO_FRAME", f"{item['name']} 未取得验收画面：{capture.get('detail', '')}")
        if not expected and i2c.get("responding"):
            add_issue(issues, "WARN", "UNEXPECTED_CAMERA", f"{item['name']} 配置为空口，但检测到加串器 {i2c.get('id')}。")
        expected_ids = {normalize_hex(value) for value in item.get("expected_serializer_ids", [])}
        if i2c.get("responding") and expected_ids and i2c.get("id") not in expected_ids:
            add_issue(
                issues,
                "WARN",
                "SERIALIZER_ID_MISMATCH",
                f"{item['name']} 加串器 ID 为 {i2c.get('id')}，预期 {', '.join(sorted(expected_ids))}。",
            )
        expected_format = profile.get("expected_video", {})
        if expected and video.get("width") and expected_format:
            if video.get("width") != expected_format.get("width") or video.get("height") != expected_format.get("height"):
                add_issue(
                    issues,
                    "WARN",
                    "V4L2_SIZE_MISMATCH",
                    f"{item['name']} V4L2 为 {video.get('width')}×{video.get('height')}，验收基准为 {expected_format.get('width')}×{expected_format.get('height')}。",
                )
        config_port = item.get("vendor_config", {})
        if expected and config_port and video.get("width"):
            if config_port.get("width") != video.get("width") or config_port.get("height") != video.get("height"):
                add_issue(
                    issues,
                    "WARN",
                    "CONFIG_V4L2_SIZE_MISMATCH",
                    f"{item['name']} 厂家配置 {config_port.get('width')}×{config_port.get('height')}，但 V4L2 为 {video.get('width')}×{video.get('height')}。",
                )

    kernel = inspect_kernel(int(profile.get("kernel_window_minutes", 5)))
    if kernel.get("available"):
        (output_dir / "kernel-camera.log").write_text(
            "\n".join(kernel.get("matching_lines", [])) + "\n", encoding="utf-8"
        )
        if kernel.get("corr_total", 0):
            add_issue(
                issues,
                "WARN",
                "VI_CORRECTABLE_ERRORS",
                f"最近 {kernel['window_minutes']} 分钟出现 {kernel['corr_total']} 条 VI corr_err；请检查尺寸/行数、embedded data 和 CSI 配置。",
            )
        if kernel.get("uncorr_total", 0) or kernel.get("timeout_count", 0) or kernel.get("recover_count", 0):
            add_issue(
                issues,
                "FAIL",
                "VI_FATAL_ERRORS",
                f"最近窗口有 uncorr_err={kernel.get('uncorr_total', 0)}、timeout={kernel.get('timeout_count', 0)}、recover={kernel.get('recover_count', 0)}。",
            )
    else:
        (output_dir / "kernel-camera.log").write_text(kernel.get("output", "") + "\n", encoding="utf-8")
        add_issue(issues, "WARN", "KERNEL_LOG_UNAVAILABLE", "无法读取相机内核日志。")

    if captured_count:
        add_issue(issues, "INFO", "VISUAL_REVIEW", "已生成实拍图；请人工检查倒置、视角、遮挡、颜色、青条和撕裂。")

    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "profile_name": profile.get("profile_name", args.profile.name),
        "profile_path": str(args.profile),
        "system": system,
        "startup": startup,
        "dependencies": dependencies,
        "runtime_files": runtime_files,
        "runtime_libraries": runtime_libraries,
        "vendor_config": vendor_config,
        "deserializers": deserializers,
        "ports": port_results,
        "kernel": kernel,
        "expected_camera_count": expected_count,
        "detected_camera_count": detected_count,
        "captured_camera_count": captured_count,
        "issues": issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GMSL 相机安装后自动验收并生成图文报告")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="平台/端口 JSON 配置")
    parser.add_argument("--output", type=Path, help="报告目录；默认写入 results/时间戳")
    parser.add_argument("--expected", type=int, help="覆盖配置中的预期相机数量")
    parser.add_argument("--start", action="store_true", help="若配置中的相机 systemd 服务未运行，则启动它")
    parser.add_argument("--no-capture", action="store_true", help="只检查拓扑/I2C/日志，不抓验收图片")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.profile = args.profile.resolve()
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"无法读取配置 {args.profile}: {exc}", file=sys.stderr)
        return 2

    if args.output:
        output_dir = args.output.resolve()
    else:
        stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        output_dir = SCRIPT_DIR / "results" / stamp
    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.profile, output_dir / "profile.json")

    print(f"[camera-acceptance] profile: {profile.get('profile_name', args.profile.name)}")
    print(f"[camera-acceptance] report:  {output_dir}")
    if args.start:
        print("[camera-acceptance] --start 已启用；服务未运行时会启动相机初始化链。")
    report = build_report(args, profile, output_dir)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_markdown(report, output_dir), encoding="utf-8")
    (output_dir / "report.html").write_text(render_html(report, output_dir), encoding="utf-8")
    _, failures, warnings = issue_summary(report["issues"])
    render_terminal_summary(report, output_dir)
    return 2 if failures else 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
