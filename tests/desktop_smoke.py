"""人工可见的离线烟测，只操作已打开的“求职助手本地验证”窗口。"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/job-search-assistant/scripts'))
from job_assistant.desktop import Desktop

parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['fill', 'submit', 'greet', 'interview'])
args = parser.parse_args()
desktop = Desktop(ROOT / '.verification/desktop')
desktop.bind('求职助手本地验证')
frame = desktop.capture()
# 坐标来自本轮已经查看的 1800×1425 离线页面截图，不用于真实网站。
if frame['window']['rect'][2] - frame['window']['rect'][0] != 1800:
    raise SystemExit('模拟窗口尺寸变化，请重新观察后调整测试坐标')
operations = {
    'fill': [
        {'op': 'click', 'x': 300, 'y': 355}, {'op': 'type', 'text': '测试张三'},
        {'op': 'click', 'x': 300, 'y': 470}, {'op': 'type', 'text': 'test@example.com'},
        {'op': 'click', 'x': 300, 'y': 735}, {'op': 'type', 'text': '虚构验证：使用 STM32 完成传感器数据采集。'}],
    'submit': [{'op': 'click', 'x': 150, 'y': 1005}],
    'greet': [{'op': 'click', 'x': 1060, 'y': 590}],
    'interview': [{'op': 'click', 'x': 1420, 'y': 590}],
}
for operation in operations[args.stage]:
    frame = desktop.act(frame['path'], operation)
print(json.dumps(frame, ensure_ascii=False))
