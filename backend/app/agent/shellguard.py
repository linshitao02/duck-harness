"""计划模式下，只放行解析后仍然只读的 PowerShell 命令。"""

from .pwshsec import command_is_readonly

__all__ = ["command_is_readonly"]
