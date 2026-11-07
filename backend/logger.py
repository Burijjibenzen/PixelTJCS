"""统一日志：控制台 + logs/ 目录文件双输出，方便调试与交接排查。

用法:
    from backend.logger import get_logger
    log = get_logger(__name__)
    log.info("世界初始化完成")
"""

import logging
import os

# 日志目录: <项目根>/logs/（已被 .gitignore 排除）
_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)
_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger；首次调用时完成全局初始化（幂等）。"""
    global _CONFIGURED
    if not _CONFIGURED:
        os.makedirs(_LOG_DIR, exist_ok=True)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"
        )

        console = logging.StreamHandler()
        console.setFormatter(fmt)

        file_handler = logging.FileHandler(
            os.path.join(_LOG_DIR, "roamchestra.log"), encoding="utf-8"
        )
        file_handler.setFormatter(fmt)

        root = logging.getLogger("roamchestra")
        root.setLevel(logging.INFO)
        root.addHandler(console)
        root.addHandler(file_handler)
        root.propagate = False
        _CONFIGURED = True

    # 子 logger 挂在 roamchestra 命名空间下，自动继承 handler
    short = name.split(".")[-1] if "." in name else name
    return logging.getLogger(f"roamchestra.{short}")
