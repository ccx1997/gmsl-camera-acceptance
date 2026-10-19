#!/usr/bin/env python3
"""Boot-time initialization with no V4L2 capture opened."""
import os
from pathlib import Path
import nx_config as hw

root = Path(__file__).resolve().parent
board = hw.load_json(root / 'cfg/board.json')
mapping = hw.load_json(root / 'cfg/mapping.json')
ports = [c['physical_index'] for c in mapping]
hw.preflight(board, dependencies=False)
hw.ensure_idle(board, wait_s=12)
hw.reset_power(board)
env = dict(os.environ, LD_LIBRARY_PATH=str(root / 'lib') + ':' + str(root / 'lib/sensorlib'))
result = hw.run([root / 'demo/camera_config_only', root / 'cfg/camera.json'],
                timeout=90, check=False, env=env, cwd=str(root))
(root / 'logs/init-latest.log').write_text(result.stdout)
if result.returncode or 'camera configuration completed' not in result.stdout:
    raise RuntimeError('初始化失败；查看 logs/init-latest.log')
for camera in mapping:
    pair = board['pairs'][camera['physical_index'] // 2]
    addr = 0x41 + camera['physical_index'] % 2
    if hw.i2c_read(pair['bus'], addr, 0x000d) != board['serializer_id']:
        raise RuntimeError('相机 ID 与运行包不匹配：' + camera['name'])
hw.save_json(root / 'logs/vc-remap-latest.json', hw.fix_single_b(board, ports))
hw.log('NX initialization completed:', ports, flush=True)
