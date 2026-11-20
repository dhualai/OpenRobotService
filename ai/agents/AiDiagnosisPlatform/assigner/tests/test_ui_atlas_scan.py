"""界面图鉴扫图：JSON 解析、增量合并。"""

import base64
import io

from ai.agents.AiDiagnosisPlatform.assigner.ui_atlas_scan import (
    _box_to_pixels,
    _parse_scan_json,
    merge_scan_regions,
    prepare_explain_images,
)


def test_parse_hard_regions_only():
    raw = '''
    这是说明文字
    {"page_caption":"监控页：左车列表右地图",
     "regions":[{"x":0.7,"y":0.8,"w":0.1,"h":0.05,"question":"右下百分比是什么？"}]}
    '''
    data = _parse_scan_json(raw)
    assert data["page_caption"] == "监控页：左车列表右地图"
    assert len(data["regions"]) == 1
    r = data["regions"][0]
    assert r["question"] == "右下百分比是什么？"
    assert r["status"] == "pending"
    assert 0 <= r["x"] <= 1


def test_parse_empty_regions_ok():
    data = _parse_scan_json('{"page_caption":"首页","regions":[]}')
    assert data["page_caption"] == "首页"
    assert data["regions"] == []


def test_parse_drops_oversized_box():
    data = _parse_scan_json(
        '{"regions":[{"x":0.0,"y":0.0,"w":0.9,"h":0.8,"question":"整页？"},'
        '{"x":0.7,"y":0.8,"w":0.1,"h":0.05,"question":"电量？"}]}'
    )
    assert len(data["regions"]) == 1
    assert data["regions"][0]["question"] == "电量？"


def test_merge_keeps_existing_and_skips_overlap():
    existing = [{
        "id": "e1", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1,
        "question": "旧", "answer": "电量", "status": "answered",
    }]
    newly = [
        {"id": "n1", "x": 0.11, "y": 0.11, "w": 0.1, "h": 0.1, "question": "重复"},
        {"id": "n2", "x": 0.7, "y": 0.7, "w": 0.1, "h": 0.1, "question": "新点"},
    ]
    merged = merge_scan_regions(existing, newly)
    assert len(merged) == 2
    assert merged[0]["answer"] == "电量"
    assert merged[1]["question"] == "新点"


def test_box_to_pixels_clamps():
    left, top, right, bottom = _box_to_pixels(1000, 800, 0.1, 0.6, 0.05, 0.04, pad_ratio=0.1)
    assert 0 <= left < right <= 1000
    assert 0 <= top < bottom <= 800


def test_prepare_explain_images_returns_two_data_urls():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (200, 200), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 120, 60, 140], fill=(255, 200, 0))
    draw.text((24, 122), "待命", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    images = prepare_explain_images(data_url, 0.1, 0.6, 0.2, 0.1)
    assert len(images) == 2
    assert all(u.startswith("data:image/") for u in images)
