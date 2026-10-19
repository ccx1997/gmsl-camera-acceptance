"""Board checks and configuration shared by deployment and boot initialization."""
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_BOARD = Path(__file__).parent / 'boards/tztek-orin-nx-r35.json'
PLUGINS = ['v4l2src', 'nvvidconv', 'nvv4l2h264enc', 'h264parse', 'rtspclientsink', 'jpegenc']
COMMANDS = ['systemctl', 'journalctl', 'i2ctransfer', 'i2cdetect', 'v4l2-ctl', 'media-ctl',
            'ffmpeg', 'fuser', 'ldd', 'gst-inspect-1.0', 'ss']


def log(*args, **kwargs):
    """Losing an SSH output pipe must not interrupt a hardware transaction."""
    try:
        print(*args, **kwargs)
    except BrokenPipeError:
        if kwargs.get('file') is sys.stderr:
            sys.stderr = open(os.devnull, 'w')
        else:
            sys.stdout = open(os.devnull, 'w')


def run(args, timeout=15, check=True, **kwargs):
    return subprocess.run([str(a) for a in args], timeout=timeout, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kwargs)


def load_json(path):
    return json.loads(Path(path).read_text())


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def fingerprint(board):
    return hashlib.sha256(json.dumps(board, sort_keys=True).encode()).hexdigest()


def machine_id():
    return hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()


def select_ports(text, expected):
    ports = [int(s) for s in text.split(',')]
    if not ports or len(set(ports)) != len(ports) or any(p < 0 or p > 7 for p in ports):
        raise ValueError('ports 必须是 0..7 范围内不重复的序号，如 0,1,2,3,5,7')
    if len(ports) != expected:
        raise ValueError('端口数量 %d 与 --expected %d 不一致' % (len(ports), expected))
    return sorted(ports)


def ports_from_probe(report, board, expected, current_machine):
    if report.get('board_sha256') != fingerprint(board) or report.get('machine_id') != current_machine:
        raise ValueError('探测报告不是此机器/此板级配置生成的，请重新 probe')
    if report.get('errors') or not report.get('restored'):
        raise ValueError('探测有错误或寄存器恢复未确认，不能安装')
    rows = report.get('links', [])
    if sorted(r['physical_index'] for r in rows) != list(range(8)):
        raise ValueError('探测报告不包含完整的 8 条链路')
    online = [r for r in rows if r.get('locked')]
    if any(r.get('serializer_id') != board['serializer_id'] for r in online):
        raise ValueError('在线相机 ID 不符合此运行包（要求 0xc8），不能自动套用 extra_mode=2')
    return select_ports(','.join(str(r['physical_index']) for r in online), expected)


def i2c_read(bus, addr, reg):
    result = run(['i2ctransfer', '-f', '-y', bus, 'w2@0x%x' % addr,
                  hex(reg >> 8), hex(reg & 255), 'r1'])
    return int(result.stdout.strip(), 16)


def i2c_write(bus, addr, reg, value):
    run(['i2ctransfer', '-f', '-y', bus, 'w3@0x%x' % addr,
         hex(reg >> 8), hex(reg & 255), hex(value)])


def ensure_idle(board, wait_s=0):
    devices = ['/dev/video%d' % p for p in range(8)] + [board['trigger_device']]
    deadline = time.monotonic() + wait_s
    while True:
        result = run(['fuser'] + devices, check=False)
        if result.returncode == 1:
            return
        if result.returncode != 0:
            raise RuntimeError('无法可靠检查设备占用：' + result.stdout)
        if time.monotonic() >= deadline:
            raise RuntimeError('相机/触发设备正在被占用；请先停止所属采集服务。\n' + result.stdout)
        time.sleep(0.25)


def reset_power(board):
    # All paths were checked before writing; always restore power if interrupted.
    paths = [Path(pair['power']) for pair in board['pairs']]
    try:
        for p in paths:
            p.write_text('0')
        time.sleep(5)
    finally:
        for p in paths:
            p.write_text('1')
    time.sleep(1)


def preflight(board, dependencies=True):
    errors = []
    if platform.machine() != board['architecture']:
        errors.append('需要 aarch64 Jetson，当前为 ' + platform.machine())
    release = Path('/etc/nv_tegra_release')
    text = release.read_text() if release.exists() else ''
    if not re.search(r'R35 .*REVISION:\s*4\.1\b', text):
        errors.append('此运行包仅验证 R35.4.1；不要用于 R36/JetPack 6')
    if platform.release() != board['kernel']:
        errors.append('内核应为 ' + board['kernel'])
    if len(board['pairs']) != 4 or board['lane_mode'] != 32:
        errors.append('此适配器仅支持四组 MAX9296 / 2-lane 板级布局')
    for i, pair in enumerate(board['pairs']):
        for p in [pair['device'], pair['power'], '/dev/i2c-%d' % pair['bus']]:
            if not Path(p).exists():
                errors.append('BSP 缺少 ' + p)
        for link in range(2):
            n = i * 2 + link
            if not Path('/dev/video%d' % n).exists():
                errors.append('BSP 缺少 /dev/video%d' % n)
            endpoint = Path('/sys/firmware/devicetree/base/tegra-capture-vi/ports/port@%d/endpoint' % n)
            for key, expected in [('port-index', pair['csi_port']), ('bus-width', 2), ('vc-id', link)]:
                try:
                    actual = struct.unpack('>I', (endpoint / key).read_bytes())[0]
                    if actual != expected:
                        errors.append('video%d %s=%d，期望 %d' % (n, key, actual, expected))
                except (OSError, struct.error):
                    errors.append('设备树缺少有效字段 ' + str(endpoint / key))
    if not Path(board['trigger_device']).exists():
        errors.append('缺少触发串口 ' + board['trigger_device'])
    commands = COMMANDS if dependencies else ['i2ctransfer', 'fuser']
    for cmd in commands:
        if not shutil.which(cmd):
            errors.append('缺少命令 ' + cmd + '；先执行 nx/install-deps.sh')
    if dependencies and shutil.which('gst-inspect-1.0'):
        for plugin in PLUGINS:
            if run(['gst-inspect-1.0', plugin], check=False).returncode:
                errors.append('缺少 GStreamer 插件 ' + plugin)
        if run(['/usr/bin/python3', '-c', 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst, GLib'], check=False).returncode:
            errors.append('缺少 Python GI/GStreamer 绑定')
    if errors:
        raise RuntimeError('\n'.join(errors))
    for pair in board['pairs']:
        if i2c_read(pair['bus'], pair['address'], 0x000d) != board['deserializer_id']:
            raise RuntimeError('MAX9296 ID 不符：bus %d addr %#x' % (pair['bus'], pair['address']))
    return 'PASS: R35.4.1、8 节点、4 组 MAX9296、2-lane/VC 拓扑和依赖符合本适配器'


def make_config(board, ports, flip=2):
    cfg = {'global': {'init_in_seq': 1, 'trigger_mode': 2, 'partial_enable': 0,
                      'env': {'CAM_LOGLEVEL': '5', 'CAM_DRIVER_NOVERSION': '1', 'CAM_MODULE_NOCHECK': '1'},
                      'trig_fps': [30], 'offset': [11], 'duty_time': [1000]}}
    for i, pair in enumerate(board['pairs']):
        cfg['deserial_%d' % i] = {'deserial_name': 'max9296', 'deserial_addr': hex(pair['address']),
            'bus_num': pair['bus'], 'deserial_gpio': {'camerr_pin': [9, 14], 'trig_pin': [6, 0]},
            'poc': {'poc_addr': '0xff', 'poc_map': '0x3210'},
            'lane_speed': board['lane_speed'], 'lane_mode': board['lane_mode']}
    mapping = []
    for channel, phy in enumerate(sorted(ports)):
        link, pair = phy % 2, phy // 2
        cfg['port_%d' % phy] = {'sensor_name': board['sensor_name'], 'serial_addr': hex(0x41 + link),
            'sensor_addr': hex(0x11 + link), 'eeprom_addr': hex(0x52 + link), 'sensor_mode': 1,
            'fps': 30, 'format': 1, 'width': board['width'], 'height': board['height'],
            'extra_mode': board['extra_mode'], 'config_index': [9], 'deserial_index': pair, 'deserial_port': link}
        # Do not compact lone B to an even video node: vendor library selects the physical node.
        mapping.append({'name': 'cam%d' % channel, 'physical_index': phy, 'video': phy,
                        'bus': board['pairs'][pair]['bus'], 'link': 'AB'[link], 'flip': flip})
    return cfg, mapping


def fix_single_b(board, ports):
    rows = []
    for i, pair in enumerate(board['pairs']):
        if i * 2 in ports or i * 2 + 1 not in ports:
            continue
        bus, addr = pair['bus'], pair['address']
        for reg, expected in [(0x048e, 0x1e), (0x0490, 0x00), (0x0492, 0x01), (0x0494, 0x12)]:
            before = i2c_read(bus, addr, reg)
            if before & 0x3f != expected:
                raise RuntimeError('未知的输出包映射，拒绝覆盖 bus%d reg%#x=%#x' % (bus, reg, before))
            value = expected | 0x40
            i2c_write(bus, addr, reg, value)
            if i2c_read(bus, addr, reg) != value:
                raise RuntimeError('VC1 写入未通过读回验证')
            rows.append({'bus': bus, 'register': hex(reg), 'before': hex(before), 'after': hex(value)})
    return rows
