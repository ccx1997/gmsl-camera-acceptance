#!/usr/bin/env python3
"""Reset idle cameras, locate GMSL2 3 Gbps links, restore rate/link registers."""
import argparse
import os
import time
from pathlib import Path
import nx_config as hw


def probe_pair(pair):
    bus, addr = pair['bus'], pair['address']
    saved = {reg: hw.i2c_read(bus, addr, reg) for reg in (0x0001, 0x0010)}
    rows = []
    try:
        for link in range(2):
            hw.i2c_write(bus, addr, 0x0001, (saved[1] & ~3) | 1)
            hw.i2c_write(bus, addr, 0x0010, 0x21 + link)
            time.sleep(5)
            values = []
            for _ in range(3):
                values.append(hw.i2c_read(bus, addr, 0x0013))
                time.sleep(0.15)
            locked = all(v & 8 for v in values)
            sensor_id = None
            if locked:
                # PoC reset returns serializers to their default address.
                sensor_id = hw.i2c_read(bus, 0x40, 0x000d)
            rows.append({'link': 'AB'[link], 'bus': bus, 'locked': locked,
                         'serializer_id': sensor_id, 'lock_samples': [hex(v) for v in values]})
    finally:
        failures = []
        for reg, value in saved.items():
            try:
                hw.i2c_write(bus, addr, reg, value)
                if hw.i2c_read(bus, addr, reg) != value:
                    failures.append(hex(reg))
            except Exception as exc:
                failures.append(str(exc))
        if failures:
            raise RuntimeError('恢复寄存器失败：' + ', '.join(failures))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', type=Path, default=hw.DEFAULT_BOARD)
    parser.add_argument('--output', type=Path, default=Path('/tmp/nx-gmsl-probe.json'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('请使用 sudo')
    board = hw.load_json(args.board)
    hw.preflight(board, dependencies=False)
    hw.ensure_idle(board, wait_s=12)
    report = {'board_sha256': hw.fingerprint(board), 'machine_id': hw.machine_id(),
              'generated_at': time.time(), 'rate_gbps': 3, 'links': [], 'errors': [], 'restored': False}
    hw.log('相机电源复位后逐条测试 8 条链路，约 50 秒；不会打开 V4L2 采集。', flush=True)
    try:
        hw.reset_power(board)
        for i, pair in enumerate(board['pairs']):
            for link, row in enumerate(probe_pair(pair)):
                row['physical_index'] = i * 2 + link
                report['links'].append(row)
                hw.log('P%d bus%d link%s locked=%s ID=%s' % (
                    row['physical_index'], row['bus'], row['link'], row['locked'],
                    hex(row['serializer_id']) if row['serializer_id'] is not None else '-'), flush=True)
        report['restored'] = True
    except (Exception, KeyboardInterrupt) as exc:
        report['errors'].append(str(exc) or 'interrupted')
    finally:
        hw.save_json(args.output, report)
    hw.log('报告：', args.output)
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
