"""Device viewport presets for UI testing."""

DEVICE_CONFIGS = {
    "mobile": {"width": 375, "height": 812},
    "mobile_small": {"width": 320, "height": 568},
    "tablet": {"width": 768, "height": 1024},
    "desktop": {"width": 1280, "height": 720},
    "desktop_hd": {"width": 1920, "height": 1080},
}


def get_device_config(name="mobile"):
    return DEVICE_CONFIGS.get(name, DEVICE_CONFIGS["mobile"])
