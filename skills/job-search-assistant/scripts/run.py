"""使用当前 Python 启动统一入口；首次调用按 SKILL.md 运行 bootstrap。"""
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from job_assistant.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
