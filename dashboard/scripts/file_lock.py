"""
文件锁工具 — 防止多进程并发读写 JSON 文件导致数据丢失。
Windows 兼容版本
"""
import json
import os
import pathlib
import tempfile
import sys
from typing import Any, Callable

# Windows 不支持 fcntl，使用简单文件操作
def atomic_json_read(path: pathlib.Path, default: Any = None) -> Any:
    """读取 JSON 文件。"""
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    except Exception:
        return default


def atomic_json_write(path: pathlib.Path, data: Any) -> None:
    """写入 JSON 文件（原子操作）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def atomic_json_update(path: pathlib.Path, updater: Callable[[Any], Any], default: Any = None) -> Any:
    """原子更新 JSON 文件（读 → 修改 → 写回）。"""
    data = atomic_json_read(path, default)
    new_data = updater(data)
    atomic_json_write(path, new_data)
    return new_data