#!/usr/bin/env python3
"""Deploy the verified NX runtime; keep reversible backups of replaced files."""
import argparse
import datetime
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
import nx_config as hw

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SELECTOR = Path('/etc/gmsl-camera-acceptance/profile')
UNIT_DIR = Path('/etc/systemd/system')
UNIT_NAMES = ['nx-gmsl.target', 'nx-gmsl-init.service', 'nx-gmsl-trigger.service',
              'nx-gmsl-mediamtx.service', 'nx-gmsl-stream@.service']


def checked_root(value):
    path = Path(value)
    if not re.fullmatch(r'/(?:opt|home)/[A-Za-z0-9_./-]+', str(path)) or '..' in path.parts:
        raise ValueError('runtime-dir 必须是 /opt 或 /home 下不含空格的绝对路径')
    if len(path.parts) < 3 or path.is_symlink():
        raise ValueError('runtime-dir 不能是共享顶层目录或软链接')
    return path


def units(root, board, mapping):
    prefix = str(root)
    streams = ' '.join('nx-gmsl-stream@%s.service' % c['name'] for c in mapping)
    return {
        'nx-gmsl.target': '[Unit]\nDescription=NX ENTRON GMSL cameras\n'
            'Requires=nx-gmsl-init.service nx-gmsl-trigger.service nx-gmsl-mediamtx.service\n'
            'Wants=%s\nAfter=nx-gmsl-init.service\n\n[Install]\nWantedBy=multi-user.target\n' % streams,
        'nx-gmsl-init.service': '[Unit]\nDescription=Initialize NX GMSL cameras\nPartOf=nx-gmsl.target\n'
            'Conflicts=jetson-cam-cfg.service\nWants=systemd-udev-settle.service\nAfter=systemd-udev-settle.service\n'
            '\n[Service]\nType=oneshot\nRemainAfterExit=yes\nWorkingDirectory=%s\n'
            'ExecStart=/usr/bin/python3 %s/initialize.py\nTimeoutStartSec=120\nTimeoutStopSec=8\n' % (prefix, prefix),
        'nx-gmsl-trigger.service': '[Unit]\nDescription=NX camera 15 Hz trigger\nPartOf=nx-gmsl.target\n'
            'Requires=nx-gmsl-init.service\nAfter=nx-gmsl-init.service\n\n[Service]\n'
            'ExecStart=%s/bin/tztek-jetson-tool-internal-trigger-camera %s 15 1000\n'
            'Restart=on-failure\nRestartSec=3\nTimeoutStopSec=8\n' % (prefix, board['trigger_device']),
        'nx-gmsl-mediamtx.service': '[Unit]\nDescription=NX camera RTSP server\nPartOf=nx-gmsl.target\n'
            'After=network.target\n\n[Service]\nWorkingDirectory=%s\nExecStart=%s/bin/mediamtx %s/mediamtx.yml\n'
            'Restart=on-failure\nRestartSec=3\nTimeoutStopSec=8\n' % (prefix, prefix, prefix),
        'nx-gmsl-stream@.service': '[Unit]\nDescription=NX camera %%i RTSP publisher\nPartOf=nx-gmsl.target\n'
            'Requires=nx-gmsl-init.service nx-gmsl-trigger.service nx-gmsl-mediamtx.service\n'
            'After=nx-gmsl-init.service nx-gmsl-trigger.service nx-gmsl-mediamtx.service\n'
            'StartLimitIntervalSec=120\nStartLimitBurst=3\n\n[Service]\nWorkingDirectory=%s\n'
            'ExecStart=/usr/bin/python3 %s/stream.py %%i\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=8\n' % (prefix, prefix),
    }


def acceptance(board, mapping, root):
    profile = hw.load_json(REPO / 'profiles/geacx1-r35-current-6cam.json')
    by_phy = {c['physical_index']: c for c in mapping}
    profile.update(profile_name='Orin NX / ENTRON 0xc8 / %d cameras / R35.4.1' % len(mapping),
                   description='P labels are software topology indices, not confirmed chassis markings.',
                   expected_camera_count=len(mapping), startup_systemd_unit='nx-gmsl.target',
                   vendor_config=str(root / 'cfg/camera.json'),
                   runtime_library_path=str(root / 'lib') + ':' + str(root / 'lib/sensorlib'),
                   sha256_manifest=str(root / 'cfg/runtime.sha256'))
    profile['capture'].update(require_rtsp=True, allow_direct_capture=False,
                              snapshot_dir='/dev/shm/nx-gmsl')
    profile['deserializers'] = [{'name': 'pair%d' % i, 'bus': p['bus'], 'address': hex(p['address']),
                                'physical_ports': ['P%d' % (i * 2), 'P%d' % (i * 2 + 1)]}
                               for i, p in enumerate(board['pairs'])]
    for port in profile['ports']:
        n = port['logical_index']
        port.update(expected=n in by_phy, bus=board['pairs'][n // 2]['bus'],
                    declared_brand='殷创；本包对应实测 0xc8 批次' if n in by_phy else '空口',
                    expected_serializer_ids=['0xc8'] if n in by_phy else [],
                    snapshot_channel=int(by_phy[n]['name'][3:]) if n in by_phy else n + 8)
    files = ['demo/camera_config_only', 'lib/libgeaccam.so.1.2.0',
             'lib/sensorlib/libisx031std.so.1.2.0', 'lib/sensorlib/libmax9296.so.1.2.0',
             'lib/sensorlib/libyuv_cam.so.1.2.0', 'bin/mediamtx',
             'bin/tztek-jetson-tool-internal-trigger-camera', 'initialize.py', 'nx_config.py',
             'stream.py', 'check_streams.py', 'mediamtx.yml', 'cfg/board.json',
             'cfg/camera.json', 'cfg/mapping.json']
    profile['runtime_paths'] = [str(root / p) for p in files] + [str(UNIT_DIR / n) for n in UNIT_NAMES]
    profile['ldd_targets'] = [str(root / p) for p in files if '.so.1.2.0' in p or p in (
        'demo/camera_config_only', 'bin/tztek-jetson-tool-internal-trigger-camera')]
    return profile


def render(out, root, board, ports, flip):
    cfg, mapping = hw.make_config(board, ports, flip)
    hw.save_json(out / 'cfg/board.json', board)
    hw.save_json(out / 'cfg/camera.json', cfg)
    hw.save_json(out / 'cfg/mapping.json', mapping)
    hw.save_json(out / 'cfg/acceptance.json', acceptance(board, mapping, root))
    for name, content in units(root, board, mapping).items():
        p = out / 'systemd' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return mapping


def verify_bundles():
    for line in (HERE / 'bundles/SHA256SUMS').read_text().splitlines():
        digest, name = line.split()
        if hashlib.sha256((HERE / 'bundles' / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('文件校验失败：' + name)


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # Python 3.8 has no extraction filter; the bundled archives contain relative .so symlinks.
        for member in tar.getmembers():
            p = Path(member.name)
            if p.is_absolute() or '..' in p.parts or not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError('不安全的归档成员：' + member.name)
            if member.issym() and (Path(member.linkname).is_absolute() or '..' in Path(member.linkname).parts):
                raise ValueError('不安全的软链接：' + member.name)
        tar.extractall(destination)


def stage_payload(stage, root, board, ports, flip):
    extract(HERE / 'bundles/camera-runtime-r35-aarch64.tar.gz', stage)
    extract(HERE / 'bundles/stream-tools-aarch64.tar.gz', stage / 'bin')
    # The archive's historical AGX sample is not an NX startup configuration.
    (stage / 'cfg/hb_j5dev.json').unlink(missing_ok=True)
    for src in (HERE / 'runtime').iterdir():
        if src.is_file() and src.suffix in ('.py', '.yml'):
            shutil.copy2(src, stage / src.name)
    shutil.copy2(HERE / 'nx_config.py', stage / 'nx_config.py')
    (stage / 'logs').mkdir(exist_ok=True)
    mapping = render(stage, root, board, ports, flip)
    profile = hw.load_json(stage / 'cfg/acceptance.json')
    hashes = []
    for name in profile['runtime_paths']:
        target = Path(name)
        source = stage / target.relative_to(root) if root in target.parents else stage / 'systemd' / target.name
        hashes.append(hashlib.sha256(source.read_bytes()).hexdigest() + '  ' + name)
    (stage / 'cfg/runtime.sha256').write_text('\n'.join(hashes) + '\n')
    env = dict(os.environ, LD_LIBRARY_PATH=str(stage / 'lib') + ':' + str(stage / 'lib/sensorlib'))
    for name in profile['ldd_targets']:
        result = hw.run(['ldd', stage / Path(name).relative_to(root)], env=env)
        if 'not found' in result.stdout:
            raise RuntimeError('缺少动态库：\n' + result.stdout)
    hw.run([stage / 'bin/mediamtx', '--version'])
    hw.save_json(stage / 'deployment.json', {'runtime_dir': str(root), 'ports': ports,
                                           'board_sha256': hw.fingerprint(board)})
    # Vendor archives preserve their original uid/modes; install root-owned files.
    for path in [stage] + list(stage.rglob('*')):
        os.chown(str(path), 0, 0, follow_symlinks=False)
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    return mapping


def system_state(unit, operation='is-active'):
    return hw.run(['systemctl', operation, unit], check=False).stdout.strip()


def check_listeners():
    pid = hw.run(['systemctl', 'show', 'nx-gmsl-mediamtx.service', '-p', 'MainPID', '--value'], check=False).stdout.strip()
    lines = hw.run(['ss', '-lntp']).stdout.splitlines()
    for line in lines:
        cols = line.split()
        if len(cols) > 3 and cols[3].rsplit(':', 1)[-1] in ('8554', '9997'):
            if not pid.isdigit() or pid == '0' or ('pid=%s,' % pid) not in line:
                raise RuntimeError('8554/9997 已由其他进程占用，请先处理端口冲突：\n' + line)


def make_backup(root):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = Path('/var/backups/gmsl-nx') / stamp
    backup.mkdir(parents=True)
    files = [UNIT_DIR / n for n in UNIT_NAMES] + [SELECTOR]
    present = []
    for index, path in enumerate(files):
        if path.exists() or path.is_symlink():
            shutil.copy2(path, backup / str(index), follow_symlinks=False)
            present.append(index)
    state = {'runtime_dir': str(root), 'had_runtime': root.exists(), 'present_files': present,
             'target_active': system_state('nx-gmsl.target') == 'active',
             'target_enabled': system_state('nx-gmsl.target', 'is-enabled'),
             'native_enabled': system_state('jetson-cam-cfg.service', 'is-enabled')}
    hw.save_json(backup / 'state.json', state)
    return backup, state


def restore(backup):
    state = hw.load_json(backup / 'state.json')
    if (backup / 'restored').exists():
        raise RuntimeError('此备份已回滚过；请勿重复回滚同一备份')
    if state['had_runtime'] and not (backup / 'runtime').is_dir():
        raise RuntimeError('备份运行目录缺失；未停止任何服务')
    root = checked_root(state['runtime_dir'])
    hw.run(['systemctl', 'stop', 'nx-gmsl.target'], timeout=40, check=False)
    board_path = root / 'cfg/board.json'
    hw.ensure_idle(hw.load_json(board_path if board_path.is_file() else hw.DEFAULT_BOARD), wait_s=12)
    # No deletion of old payloads: retain the failed/new install for inspection.
    if root.exists():
        failed = backup / ('replaced-runtime-' + str(int(datetime.datetime.now().timestamp())))
        shutil.move(str(root), str(failed))
    if state['had_runtime']:
        shutil.move(str(backup / 'runtime'), str(root))
    for index, path in enumerate([UNIT_DIR / n for n in UNIT_NAMES] + [SELECTOR]):
        if path.exists() or path.is_symlink():
            path.unlink()
        if index in state['present_files']:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / str(index), path, follow_symlinks=False)
    hw.run(['systemctl', 'daemon-reload'])
    for unit, old in [('nx-gmsl.target', state['target_enabled']), ('jetson-cam-cfg.service', state['native_enabled'])]:
        if old in ('enabled', 'enabled-runtime'):
            hw.run(['systemctl', 'enable', unit])
        elif old in ('disabled', 'not-found'):
            hw.run(['systemctl', 'disable', unit], check=False)
    if state['target_active']:
        hw.run(['systemctl', 'start', 'nx-gmsl.target'], timeout=150)
    (backup / 'restored').write_text(datetime.datetime.now().isoformat() + '\n')
    hw.log('已恢复此前文件和服务状态；替换下来的运行包保留在', backup)


def install(args, board, ports):
    root = checked_root(args.runtime_dir)
    if root.exists() and not (root / 'deployment.json').is_file():
        raise RuntimeError('目标目录已存在但不是本工具管理的安装；请使用新的 runtime-dir')
    hw.log(hw.preflight(board), flush=True)
    check_listeners()
    verify_bundles()
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.nx-gmsl-stage-', dir=str(root.parent)))
    backup = None
    applied = False
    try:
        mapping = stage_payload(stage, root, board, ports, args.flip)
        backup, state = make_backup(root)
        hw.log('备份：', backup, flush=True)
        if state['target_active']:
            hw.run(['systemctl', 'stop', 'nx-gmsl.target'], timeout=40)
        hw.ensure_idle(board, wait_s=12)
        # A partially executing old initializer must never race this transaction.
        if system_state('jetson-cam-cfg.service') == 'activating':
            raise RuntimeError('原厂家初始化仍在执行，请待结束后重试')
        if root.exists():
            shutil.move(str(root), str(backup / 'runtime'))
        applied = True
        stage.rename(root)
        for name in UNIT_NAMES:
            shutil.copy2(root / 'systemd' / name, UNIT_DIR / name)
        SELECTOR.parent.mkdir(parents=True, exist_ok=True)
        SELECTOR.write_text(str(root / 'cfg/acceptance.json') + '\n')
        hw.run(['systemctl', 'daemon-reload'])
        hw.run(['systemd-analyze', 'verify'] + [UNIT_DIR / n for n in UNIT_NAMES])
        if state['native_enabled'] == 'enabled':
            hw.run(['systemctl', 'disable', 'jetson-cam-cfg.service'])
        hw.run(['systemctl', 'enable', 'nx-gmsl.target'])
        hw.run(['systemctl', 'start', 'nx-gmsl.target'], timeout=150)
        hw.run(['systemctl', 'is-active', 'nx-gmsl-trigger.service', 'nx-gmsl-mediamtx.service'] +
               ['nx-gmsl-stream@%s.service' % c['name'] for c in mapping])
        time.sleep(5)
        result = hw.run(['/usr/bin/python3', root / 'check_streams.py'], timeout=50, check=False)
        hw.log(result.stdout, flush=True)
        if result.returncode:
            raise RuntimeError('持续出帧检查失败；将恢复安装前的服务')
        hw.save_json(root / 'deployment.json', dict(hw.load_json(root / 'deployment.json'), backup=str(backup)))
        hw.log('部署完成，30 秒持续出帧检查已通过。日常复核：sudo python3 %s/check_streams.py' % root)
        hw.log('验收：sudo ./run.sh；回滚：sudo python3 nx/deploy.py rollback --backup %s' % backup)
    except BaseException:
        if applied:
            hw.log('部署失败，正在恢复先前文件与服务...', file=sys.stderr, flush=True)
            restore(backup)
        elif backup and hw.load_json(backup / 'state.json')['target_active']:
            hw.run(['systemctl', 'start', 'nx-gmsl.target'], timeout=150, check=False)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)  # Only this invocation's generated staging directory.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('preflight', help='只读检查 BSP、设备节点与依赖')
    pre.add_argument('--board', type=Path, default=hw.DEFAULT_BOARD)
    for command in ('render', 'install'):
        p = sub.add_parser(command)
        p.add_argument('--board', type=Path, default=hw.DEFAULT_BOARD)
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument('--ports', help='显式指定软件拓扑序号，如 0,1,2,3,5,7')
        group.add_argument('--probe-file', type=Path, help='本机 nx/probe.py 的实测报告')
        p.add_argument('--expected', type=int, default=6)
        p.add_argument('--runtime-dir', type=Path, default=Path('/opt/gmsl-nx'))
        p.add_argument('--flip', type=int, choices=[0, 2], default=2, help='0=原方向，2=旋转180度')
        if command == 'render':
            p.add_argument('--output', type=Path, required=True)
    rollback = sub.add_parser('rollback')
    rollback.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    if args.command != 'render' and os.geteuid() != 0:
        parser.error('请使用 sudo')
    if args.command == 'rollback':
        restore(args.backup.resolve())
        return
    board = hw.load_json(args.board)
    if args.command == 'preflight':
        hw.log(hw.preflight(board))
        return
    ports = (hw.select_ports(args.ports, args.expected) if args.ports is not None else
             hw.ports_from_probe(hw.load_json(args.probe_file), board, args.expected, hw.machine_id()))
    if args.command == 'render':
        checked_root(args.runtime_dir)
        if args.output.exists():
            raise ValueError('render 输出目录已存在，请指定新目录')
        render(args.output, args.runtime_dir, board, ports, args.flip)
        hw.log('配置预览：', args.output)
    else:
        install(args, board, ports)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        hw.log('FAIL:', exc, file=sys.stderr)
        sys.exit(1)
