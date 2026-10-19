#!/usr/bin/env python3
"""Persistent V4L2 -> H.264/RTSP, with bounded snapshots and frame counters."""
import json
import os
import signal
import sys
import time
from pathlib import Path

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

name = sys.argv[1]
mapping = json.loads((Path(__file__).parent / 'cfg/mapping.json').read_text())
camera = next(c for c in mapping if c['name'] == name)
device = '/dev/video%d' % camera['video']
root = Path('/dev/shm/nx-gmsl')
root.mkdir(exist_ok=True)
Gst.init(None)
pipeline = Gst.parse_launch(
    'v4l2src device=%s io-mode=mmap do-timestamp=true name=vsrc '
    '! video/x-raw,format=YUY2,width=1920,height=1536 '
    '! nvvidconv name=capconv flip-method=%d '
    '! video/x-raw(memory:NVMM),format=NV12,width=1920,height=1536 '
    '! queue max-size-buffers=4 max-size-bytes=0 max-size-time=0 leaky=downstream '
    '! tee name=t '
    't. ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream '
    '! nvv4l2h264enc bitrate=3000000 control-rate=1 vbv-size=3000000 '
    'insert-sps-pps=true insert-vui=true iframeinterval=15 idrinterval=15 maxperf-enable=true '
    '! h264parse config-interval=-1 name=encoded '
    '! queue max-size-buffers=8 max-size-bytes=0 max-size-time=0 leaky=downstream '
    '! rtspclientsink location=rtsp://127.0.0.1:8554/%s protocols=tcp '
    't. ! queue name=jpgq max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream '
    '! nvvidconv ! video/x-raw,format=I420,width=1920,height=1536 '
    '! jpegenc quality=85 '
    '! multifilesink location=/dev/shm/nx-gmsl/%s_%%06d.jpg max-files=5 sync=false async=false'
    % (device, camera['flip'], name, name)
)
state = {'camera': name, 'device': device, 'frames': 0, 'encoded': 0,
         'started': time.time(), 'last_frame': None, 'pts_ns': None, 'error': None}
last_jpeg = [0.0]
loop = GLib.MainLoop()

def capture_probe(pad, info):
    buf = info.get_buffer()
    if buf is not None:
        state['frames'] += 1
        state['last_frame'] = time.time()
        state['pts_ns'] = int(buf.pts)
    return Gst.PadProbeReturn.OK

def encoded_probe(pad, info):
    if info.get_buffer() is not None:
        state['encoded'] += 1
    return Gst.PadProbeReturn.OK

def jpeg_probe(pad, info):
    now = time.monotonic()
    if now - last_jpeg[0] < 1:
        return Gst.PadProbeReturn.DROP
    last_jpeg[0] = now
    return Gst.PadProbeReturn.OK

def save_state():
    state['updated'] = time.time()
    temp = root / (name + '.json.tmp')
    temp.write_text(json.dumps(state) + '\n')
    os.replace(str(temp), str(root / (name + '.json')))
    return True

def bus_message(bus, message):
    if message.type == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        state['error'] = '%s %s' % (err, debug)
        print(state['error'], flush=True)
        save_state()
        loop.quit()
    elif message.type == Gst.MessageType.EOS:
        loop.quit()

pipeline.get_by_name('capconv').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, capture_probe)
pipeline.get_by_name('encoded').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, encoded_probe)
pipeline.get_by_name('jpgq').get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, jpeg_probe)
bus = pipeline.get_bus()
bus.add_signal_watch()
bus.connect('message', bus_message)
GLib.timeout_add_seconds(2, save_state)
for sig in (signal.SIGINT, signal.SIGTERM):
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (loop.quit(), False)[1])
save_state()
print('Starting %s on %s' % (name, device), flush=True)
pipeline.set_state(Gst.State.PLAYING)
loop.run()
pipeline.set_state(Gst.State.NULL)
sys.exit(1 if state['error'] else 0)
