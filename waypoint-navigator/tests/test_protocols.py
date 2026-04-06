"""Tests for src/protocols.py — FrameConsumer and Stoppable Protocols."""
from __future__ import annotations

import numpy as np

from src.protocols import FrameConsumer, Stoppable


class _GoodConsumer:
    def set_frame_getter(self, getter):
        pass


class _GoodStoppable:
    def start(self):
        pass

    def stop(self):
        pass

    def is_running(self):
        return False


class _MissingMethod:
    pass


class TestFrameConsumer:

    def test_isinstance_with_matching_class(self):
        assert isinstance(_GoodConsumer(), FrameConsumer)

    def test_not_isinstance_without_method(self):
        assert not isinstance(_MissingMethod(), FrameConsumer)

    def test_lambda_with_correct_signature_matches(self):
        class _Dynamic:
            def set_frame_getter(self, getter):
                self._getter = getter

        assert isinstance(_Dynamic(), FrameConsumer)


class TestStoppable:

    def test_isinstance_with_all_methods(self):
        assert isinstance(_GoodStoppable(), Stoppable)

    def test_not_isinstance_missing_stop(self):
        class _NoStop:
            def start(self):
                pass

            def is_running(self):
                return False

        assert not isinstance(_NoStop(), Stoppable)

    def test_not_isinstance_missing_start(self):
        class _NoStart:
            def stop(self):
                pass

            def is_running(self):
                return False

        assert not isinstance(_NoStart(), Stoppable)

    def test_not_isinstance_empty_class(self):
        assert not isinstance(_MissingMethod(), Stoppable)
