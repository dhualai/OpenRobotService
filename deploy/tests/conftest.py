# -*- coding: utf-8 -*-
"""让测试可直接 import deploy/ 下的 notify / notify_image（无包结构）。"""
import sys
from pathlib import Path

sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
