"""Open-source Doubao keyword research collector.

This project is unofficial and is not affiliated with ByteDance or Doubao.
"""

from typing import Any

__all__ = ["create_app"]
__version__ = "1.0.1"


def create_app(*args: Any, **kwargs: Any) -> Any:
    """按需加载网页服务，避免桌面版携带不需要的服务组件。"""

    from .server import create_app as _create_app

    return _create_app(*args, **kwargs)
