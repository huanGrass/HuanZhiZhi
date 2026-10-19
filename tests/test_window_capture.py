from __future__ import annotations

from threading import Event, Thread
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from huanzhizhi.platform.window_capture import WgcWindowCapture, WindowCaptureError


class FakeNativeCapture:
    instances = []

    def __init__(self, **options):
        self.options = options
        self.halt = Event()
        self.instances.append(self)
        y,x = np.mgrid[:18,:24]
        self.pixels = np.stack((x,y,np.full_like(x,100),np.full_like(x,255)),axis=2).astype(np.uint8)

    def event(self, callback):
        setattr(self,callback.__name__,callback)
        return callback

    def start_free_threaded(self):
        def run():
            while not self.halt.is_set():
                frame=SimpleNamespace(width=24,height=18,frame_buffer=self.pixels,timespan=int(perf_counter()*10_000_000))
                self.on_frame_arrived(frame,None)
                self.halt.wait(.005)
        self.thread=Thread(target=run,daemon=True)
        self.thread.start()
        return SimpleNamespace(stop=self.stop)

    def stop(self):
        self.halt.set()
        self.thread.join(timeout=1)


class WindowCaptureTests(unittest.TestCase):
    def setUp(self):
        FakeNativeCapture.instances=[]
        self.patch=patch('windows_capture.WindowsCapture',FakeNativeCapture)
        self.patch.start()
        self.capture=WgcWindowCapture(minimum_update_interval_ms=1)
        self.info=SimpleNamespace(hwnd=123,width=20,height=14)

    def tearDown(self):
        self.capture.close()
        self.patch.stop()

    def test_full_client_and_changed_region_are_cropped_before_rgb_copy(self):
        image=self.capture.capture_client(self.info,crop_left=2,crop_top=3)
        self.assertEqual(image.size,(20,14))
        self.assertEqual(image.getpixel((0,0)),(100,3,2))
        roi=self.capture.capture_client(self.info,crop_left=2,crop_top=3,region=(4,5,10,11))
        self.assertEqual(roi.size,(6,6))
        self.assertEqual(roi.getpixel((0,0)),(100,8,6))
        self.assertEqual(self.capture._frame.size,(6,6))
        self.assertLess(abs(perf_counter()-roi.info['capture_source_at']),.2)
        roi.putpixel((0,0),(0,0,0))
        self.assertNotEqual(self.capture._frame.getpixel((0,0)),(0,0,0))
        full=self.capture.capture_client(self.info,crop_left=2,crop_top=3)
        self.assertEqual(full.size,(20,14))
        self.assertEqual(len(FakeNativeCapture.instances),1)

    def test_invalid_region_and_closed_session_never_return_old_frame(self):
        with self.assertRaises(WindowCaptureError):
            self.capture.capture_client(self.info,crop_left=2,crop_top=3,region=(0,0,21,15))
        self.capture.capture_client(self.info,crop_left=2,crop_top=3)
        native=FakeNativeCapture.instances[0]
        native.stop();native.on_closed()
        with self.assertRaisesRegex(WindowCaptureError,'closed'):
            self.capture.capture_client(self.info,crop_left=2,crop_top=3)

    def test_new_window_restarts_capture_and_does_not_reuse_old_region(self):
        self.capture.capture_client(self.info,crop_left=2,crop_top=3,region=(1,1,3,3))
        other=SimpleNamespace(hwnd=456,width=20,height=14)
        image=self.capture.capture_client(other,crop_left=2,crop_top=3)
        self.assertEqual(image.size,(20,14))
        self.assertEqual(len(FakeNativeCapture.instances),2)
        self.assertTrue(FakeNativeCapture.instances[0].halt.is_set())

    def test_wait_for_post_action_frame_keeps_one_capture_session(self):
        first = self.capture.capture_client(self.info, crop_left=2, crop_top=3)
        after = perf_counter()
        FakeNativeCapture.instances[0].pixels[:, :, 2] = 200
        fresh = self.capture.capture_client(self.info, crop_left=2, crop_top=3, after=after)
        self.assertGreater(fresh.info['capture_source_at'], after)
        self.assertGreater(fresh.info['capture_source_at'], first.info['capture_source_at'])
        self.assertEqual(fresh.getpixel((0, 0))[0], 200)
        self.assertEqual(len(FakeNativeCapture.instances), 1)
        self.assertFalse(FakeNativeCapture.instances[0].halt.is_set())

    def test_only_changed_frame_is_not_discarded_by_python_throttling(self):
        self.capture.close()
        self.capture = WgcWindowCapture(minimum_update_interval_ms=1000)
        self.capture.capture_client(self.info, crop_left=2, crop_top=3)
        native = FakeNativeCapture.instances[0]
        self.assertEqual(native.options['minimum_update_interval'], 1000)
        native.stop()
        after = perf_counter()
        native.pixels[:, :, 2] = 210
        native.on_frame_arrived(SimpleNamespace(width=24, height=18, frame_buffer=native.pixels,
                                                timespan=int(perf_counter()*10_000_000)), None)
        frame = self.capture.capture_client(self.info, crop_left=2, crop_top=3, after=after)
        self.assertEqual(frame.getpixel((0, 0))[0], 210)
        self.assertEqual(len(FakeNativeCapture.instances), 1)

    def test_late_delivery_of_pre_action_frame_cannot_pass_freshness_check(self):
        first = self.capture.capture_client(self.info, crop_left=2, crop_top=3)
        native = FakeNativeCapture.instances[0]
        native.stop()
        after = perf_counter()

        def deliver():
            for source_at, red in ((first.info['capture_source_at'], 150),
                                   (after + .02, 220)):
                Event().wait(.03)
                native.pixels[:, :, 2] = red
                native.on_frame_arrived(SimpleNamespace(width=24, height=18,
                    frame_buffer=native.pixels, timespan=int(source_at*10_000_000)), None)

        sender = Thread(target=deliver)
        sender.start()
        try:
            frame = self.capture.capture_client(self.info, crop_left=2, crop_top=3, after=after)
            self.assertEqual(frame.getpixel((0, 0))[0], 220)
            self.assertGreater(frame.info['capture_source_at'], after)
        finally:
            sender.join()
