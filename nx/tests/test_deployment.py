import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))
import nx_config as hw
import probe
import deploy
import camera_acceptance as acceptance


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.board = hw.load_json(hw.DEFAULT_BOARD)

    def test_noncontiguous_b_links_keep_odd_video_and_dense_rtsp_names(self):
        cfg, cameras = hw.make_config(self.board, [0, 1, 2, 3, 5, 7])
        self.assertEqual([c['video'] for c in cameras], [0, 1, 2, 3, 5, 7])
        self.assertEqual([c['name'] for c in cameras], ['cam%d' % i for i in range(6)])
        self.assertNotIn('port_4', cfg)
        self.assertNotIn('port_6', cfg)
        self.assertEqual(cfg['port_7']['deserial_port'], 1)
        self.assertEqual(cfg['deserial_0']['lane_speed'], 2400)
        self.assertEqual(cfg['deserial_0']['lane_mode'], 32)
        self.assertEqual(cfg['global']['partial_enable'], 0)

    def test_all_ports_and_one_camera_are_representable_without_phantoms(self):
        for ports in ([7], list(range(8)), [0, 2, 4, 6]):
            cfg, mapping = hw.make_config(self.board, ports, flip=0)
            self.assertEqual(len(mapping), len(ports))
            self.assertEqual(len([k for k in cfg if k.startswith('port_')]), len(ports))
            self.assertTrue(all(c['flip'] == 0 for c in mapping))

    def test_bad_port_sets_and_wrong_count_fail(self):
        for text, count in [('0,0', 2), ('8', 1), ('-1', 1), ('0,1', 6), ('', 0)]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                hw.select_ports(text, count)

    def report(self):
        return {'machine_id': 'test-machine', 'board_sha256': hw.fingerprint(self.board),
                'errors': [], 'restored': True,
                'links': [{'physical_index': i, 'locked': i in [0, 1, 2, 3, 5, 7],
                           'serializer_id': 200 if i in [0, 1, 2, 3, 5, 7] else None} for i in range(8)]}

    def test_probe_rejects_other_machine_brand_missing_links_and_restore_failure(self):
        self.assertEqual(hw.ports_from_probe(self.report(), self.board, 6, 'test-machine'), [0, 1, 2, 3, 5, 7])
        for change in ('machine', 'id', 'missing', 'restore', 'count'):
            report = self.report()
            if change == 'machine': report['machine_id'] = 'another-machine'
            if change == 'id': report['links'][0]['serializer_id'] = 0x91
            if change == 'missing': report['links'].pop()
            if change == 'restore': report['restored'] = False
            if change == 'count': report['links'][0]['locked'] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                hw.ports_from_probe(report, self.board, 6, 'test-machine')

    def test_single_b_remap_only_affects_sparse_b_pairs_and_preserves_packet_type(self):
        regs = {(bus, r): v for bus in [30, 31, 32, 33]
                for r, v in [(0x48e, 0x1e), (0x490, 0), (0x492, 1), (0x494, 0x12)]}
        writes = []
        def write(bus, addr, reg, value):
            writes.append((bus, reg, value))
            regs[bus, reg] = value
        with patch.object(hw, 'i2c_read', side_effect=lambda bus, addr, r: regs[bus, r]), \
             patch.object(hw, 'i2c_write', side_effect=write):
            hw.fix_single_b(self.board, [0, 1, 2, 3, 5, 7])
        self.assertEqual({b for b, _, _ in writes}, {32, 33})
        self.assertEqual(len(writes), 8)
        self.assertTrue(all(v & 0x40 for _, _, v in writes))
        self.assertEqual(regs[32, 0x48e] & 0x3f, 0x1e)

    def test_unknown_packet_mapping_is_not_overwritten(self):
        with patch.object(hw, 'i2c_read', return_value=0xff), patch.object(hw, 'i2c_write') as write:
            with self.assertRaises(RuntimeError):
                hw.fix_single_b(self.board, [7])
            write.assert_not_called()

    def test_target_stop_allows_its_children_to_release_devices(self):
        busy = subprocess.CompletedProcess([], 0, '/dev/video0: 123')
        free = subprocess.CompletedProcess([], 1, '')
        with patch.object(hw, 'run', side_effect=[busy, free]) as run, patch.object(hw.time, 'sleep'):
            hw.ensure_idle(self.board, wait_s=12)
        self.assertEqual(run.call_count, 2)
        with patch.object(hw, 'run', return_value=busy), self.assertRaises(RuntimeError):
            hw.ensure_idle(self.board)

    def test_probe_restores_original_registers_after_sensor_read_failure(self):
        regs = {1: 2, 0x10: 1, 0x13: 0xda}
        def read(bus, addr, reg):
            if addr == 0x40:
                raise RuntimeError('I2C camera read failed')
            return regs[reg]
        with patch.object(hw, 'i2c_read', side_effect=read), \
             patch.object(hw, 'i2c_write', side_effect=lambda bus, addr, reg, value: regs.update({reg: value})), \
             patch.object(probe.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                probe.probe_pair(self.board['pairs'][0])
        self.assertEqual((regs[1], regs[0x10]), (2, 1))

    def test_service_lifecycle_and_profile_follow_selected_cameras(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, root = Path(tmp), Path('/opt/gmsl-nx')
            deploy.render(out, root, self.board, [1, 7], 2)
            target = (out / 'systemd/nx-gmsl.target').read_text()
            self.assertIn('nx-gmsl-stream@cam1.service', target)
            self.assertNotIn('nx-gmsl-stream@cam2.service', target)
            stream = (out / 'systemd/nx-gmsl-stream@.service').read_text()
            self.assertIn('PartOf=nx-gmsl.target', stream)
            self.assertIn('After=nx-gmsl-init.service nx-gmsl-trigger.service nx-gmsl-mediamtx.service', stream)
            profile = hw.load_json(out / 'cfg/acceptance.json')
            self.assertEqual([p['logical_index'] for p in profile['ports'] if p['expected']], [1, 7])
            self.assertTrue(profile['capture']['require_rtsp'])
            self.assertFalse(profile['capture']['allow_direct_capture'])

    def test_bundled_artifacts_are_complete_and_match_hashes(self):
        deploy.verify_bundles()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deploy.extract(ROOT / 'bundles/camera-runtime-r35-aarch64.tar.gz', root)
            for name in ['demo/camera_config_only', 'lib/libgeaccam.so.1',
                         'lib/sensorlib/libisx031std.so', 'lib/sensorlib/libmax9296.so',
                         'lib/sensorlib/libyuv_cam.so']:
                self.assertTrue((root / name).is_file(), name)

    def test_full_staging_builds_complete_manifest_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            result = subprocess.CompletedProcess([], 0, 'resolved')
            with patch.object(hw, 'run', return_value=result), patch.object(deploy.os, 'chown'):
                deploy.stage_payload(stage, Path('/opt/gmsl-nx'), self.board, [0, 1, 2, 3, 5, 7], 2)
            meta = hw.load_json(stage / 'deployment.json')
            self.assertEqual(meta['board_sha256'], hw.fingerprint(self.board))
            manifest = (stage / 'cfg/runtime.sha256').read_text()
            self.assertIn('/opt/gmsl-nx/stream.py', manifest)
            self.assertIn('/etc/systemd/system/nx-gmsl.target', manifest)
            self.assertFalse((stage / 'cfg/hb_j5dev.json').exists())

    def test_disconnected_ssh_output_cannot_abort_hardware_transaction(self):
        with patch('builtins.print', side_effect=BrokenPipeError), patch.object(hw.sys, 'stdout'):
            hw.log('status')
            hw.sys.stdout.close()


class AcceptanceTests(unittest.TestCase):
    def test_failed_rtsp_does_not_accept_snapshot_or_open_v4l2(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / '0_existing.jpg').write_bytes(b'old snapshot')
            cfg = {'snapshot_dir': tmp, 'require_rtsp': True, 'allow_direct_capture': False,
                   'rtsp_url_template': 'rtsp://127.0.0.1:8554/cam{channel}'}
            with patch.object(acceptance.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(acceptance, 'run_command', return_value={'returncode': 1, 'output': 'no stream'}) as run:
                result = acceptance.capture_port({'name': 'P0', 'video_node': '/dev/video0'},
                                                 {'exists': True, 'busy': False}, cfg, p)
            self.assertFalse(result['ok'])
            self.assertEqual(run.call_count, 1)
            self.assertNotIn('/dev/video0', run.call_args[0][0])

    def test_ldd_uses_the_same_isolated_libraries_as_initializer(self):
        with tempfile.NamedTemporaryFile() as binary:
            profile = {'ldd_targets': [binary.name], 'runtime_library_path': '/opt/gmsl-nx/lib'}
            with patch.object(acceptance.shutil, 'which', return_value='/usr/bin/ldd'), \
                 patch.object(acceptance, 'run_command', return_value={'output': 'all resolved'}) as run:
                acceptance.inspect_runtime(profile)
            self.assertEqual(run.call_args[1]['env']['LD_LIBRARY_PATH'], '/opt/gmsl-nx/lib')


if __name__ == '__main__':
    unittest.main()
