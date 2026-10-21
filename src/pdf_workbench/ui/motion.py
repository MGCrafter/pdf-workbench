"""Short, interruptible feedback with a persisted reduced-motion preference."""
from __future__ import annotations

import os
import sys
import shutil
import subprocess

from PyQt6.QtCore import QEasingCurve, QObject, QPropertyAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect


def system_reduces_motion():
    if os.environ.get("PDF_WORKBENCH_REDUCED_MOTION", "").lower() in ("1", "true", "yes"):
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return not bool(enabled.value)
        except (AttributeError, OSError):
            pass
    elif sys.platform.startswith("linux") and shutil.which("gsettings"):
        try:
            result = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                                    capture_output=True, text=True, timeout=.5)
            return result.returncode == 0 and result.stdout.strip() == "false"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


class MotionController(QObject):
    KEY = "appearance/reduced_motion"

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.system_reduced = system_reduces_motion()
        self.reduced = settings.value(self.KEY, False, type=bool)
        self.animations = {}

    @property
    def enabled(self):
        return not (self.reduced or self.system_reduced)

    def set_reduced(self, reduced):
        self.reduced = bool(reduced)
        self.settings.setValue(self.KEY, self.reduced)
        self.settings.sync()
        if not self.enabled:
            for animation in self.animations.values():
                animation.stop()
                target = animation.targetObject()
                if target is not None:
                    target.setProperty(bytes(animation.propertyName()).decode(), animation.endValue())

    def animate(self, target, property_name, start, end, duration=180):
        key = (id(target), property_name)
        animation = self.animations.get(key)
        if animation is None:
            animation = QPropertyAnimation(target, property_name.encode(), self)
            self.animations[key] = animation
        animation.stop()
        if not self.enabled:
            target.setProperty(property_name, end)
            return
        animation.setDuration(duration)
        animation.setStartValue(start); animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.start()

    def feedback(self, widget):
        effect = widget.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        self.animate(effect, "opacity", .45, 1., 160)
