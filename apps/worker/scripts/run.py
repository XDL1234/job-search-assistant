"""求职助手的 Python 工作进程入口。"""
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from job_assistant.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
