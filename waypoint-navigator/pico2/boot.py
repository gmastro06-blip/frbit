"""boot.py — Pico 2 USB HID Boot Configuration.

This runs BEFORE code.py and configures the USB HID descriptors.
Flash to Pico 2 running CircuitPython 9.x+.

Required board: Raspberry Pi Pico 2 (RP2350)
Required firmware: CircuitPython 9.0+ from circuitpython.org
"""

import usb_hid

# Enable both keyboard and mouse HID devices.
# These are the standard CircuitPython HID descriptors.
usb_hid.enable(
    (usb_hid.Device.KEYBOARD, usb_hid.Device.MOUSE),
)
