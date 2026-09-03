import logging
import sys
import json
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_object = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add additional context information (if exists)
        if hasattr(record, 'context') and record.context:  # type: ignore
            log_object["context"] = record.context  # type: ignore
            
        if record.exc_info:
            log_object["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_object, ensure_ascii=False)


# ANSI color codes
COLORS = {
    "TIMESTAMP": "\033[90m",    # Gray (timestamp)
    "PATH": "\033[36m",         # Cyan (file path)
    "DEBUG": "\033[36m",        # Cyan
    "INFO": "\033[32m",         # Green
    "WARNING": "\033[33m",      # Yellow
    "ERROR": "\033[31m",        # Red
    "CRITICAL": "\033[35m",     # Purple
    "MESSAGE": "\033[97m",      # White (message content)
    "RESET": "\033[0m",         # Reset
}

class TextFormatter(logging.Formatter):
    """Text formatter referencing Supervisor log format (smart color support)
    - Output to terminal (isatty=True): with ANSI colors
    - Output to file/pipe (isatty=False): plain text, no color codes
    """

    def __init__(self, fmt=None, datefmt=None, enable_color=None):
        super().__init__(fmt=fmt, datefmt=datefmt)
        # enable_color=None (auto-detect), True (force color), False (force plain text)
        self.enable_color = enable_color

    def format(self, record):
        timestamp = self.formatTime(record, self.datefmt)
        # Color control: decide whether to output ANSI color codes based on whether target stream is a terminal
        if self.enable_color:
            color = COLORS.get(record.levelname, COLORS["RESET"])
            reset = COLORS["RESET"]
            return (
                f"{COLORS['TIMESTAMP']}[{timestamp}]{reset}"
                f"{COLORS['PATH']}[{record.pathname}:{record.lineno}]{reset} "
                f"{color}[{record.levelname}]{reset} "
                f"{COLORS['MESSAGE']}[{record.getMessage()}]{reset}"
            )
        else:
            return (
                f"[{timestamp}]"
                f"[{record.pathname}:{record.lineno}] "
                f"[{record.levelname}] "
                f"[{record.getMessage()}]"
            )

def add_context_to_log_record(context: Dict[str, Any]):
    """Add context information to log record for current thread"""
    old_factory = logging.getLogRecordFactory()
    
    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.context = context  # type: ignore
        return record
    
    logging.setLogRecordFactory(record_factory)

def get_logger(name: str) -> logging.Logger:
    """Get logger instance.
    
    Args:
        name: Logger name, typically use __name__
        
    Returns:
        logging.Logger instance
    """
    return logging.getLogger(name)


def setup_logging(
    level: str,
    format_type: str,
    log_dir: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
):
    """Configure logging system.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: Log format (json or text)
        log_dir: Log file directory, if None only output to stdout
        max_bytes: Maximum bytes per log file (default 10MB)
        backup_count: Number of log files to keep (default 5)
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # If already configured, skip (prevent adding duplicate handlers)
    if root_logger.handlers:
        return
    
    # Choose formatter based on format type
    if format_type.lower() == "json":
        formatter = JSONFormatter()
    else:
        # text format references Supervisor log format
        formatter = TextFormatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - [%(pathname)s:%(lineno)d] - [%(message)s]",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    
    # Standard output handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    # stdout: auto-detect if connected to terminal (isatty), if yes output with color, otherwise plain text
    if format_type.lower() == "json":
        stdout_handler.setFormatter(JSONFormatter())
    else:
        stdout_handler.setFormatter(
            TextFormatter(
                fmt="%(asctime)s - %(name)s - %(levelname)s - [%(pathname)s:%(lineno)d] - [%(message)s]",
                datefmt="%Y-%m-%d %H:%M:%S",
                enable_color=sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else False,
            )
        )
    root_logger.addHandler(stdout_handler)

    # File handler (with rotation) — always use plain text format to avoid writing ANSI color codes to log files
    if log_dir:
        try:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            log_file = log_path / "taurus-executor.log"
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            if format_type.lower() == "json":
                file_handler.setFormatter(JSONFormatter())
            else:
                file_handler.setFormatter(
                    TextFormatter(
                        fmt="%(asctime)s - %(name)s - %(levelname)s - [%(pathname)s:%(lineno)d] - [%(message)s]",
                        datefmt="%Y-%m-%d %H:%M:%S",
                        enable_color=False,  # File logs always plain text
                    )
                )
            root_logger.addHandler(file_handler)

            root_logger.info(f"Log file created: {log_file}")
        except (PermissionError, OSError) as e:
            # Permission denied or directory creation failed, fallback to stdout only
            print(
                f"WARNING: Cannot create log directory {log_dir}: {e}. "
                "Logs will only be output to stdout.",
                file=sys.stderr,
            )


class CommandLogger:
    """Command execution interruption logger utility class, unified formatting for various interruption scenarios."""

    @staticmethod
    def timeout(
        logger: logging.Logger,
        timeout: int,
        cmd: str,
        args: list,
        pid: Optional[int] = None,
        pgid: Optional[int] = None,
    ) -> None:
        logger.warning(
            "Command timed out after %ds, terminating process group. "
            "cmd=%r, args=%r, pid=%s, pgid=%s",
            timeout, cmd, args, pid or "N/A", pgid or "N/A",
        )

    @staticmethod
    def sending_sigterm(logger: logging.Logger, pgid: int) -> None:
        logger.info("Sending SIGTERM to process group pgid=%d", pgid)

    @staticmethod
    def command_not_found(
        logger: logging.Logger,
        cmd: str,
        args: list,
        error: Exception,
    ) -> None:
        logger.error(
            "Command not found. cmd=%r, args=%r, error=%s",
            cmd, args, error,
        )

    @staticmethod
    def permission_denied(
        logger: logging.Logger,
        cmd: str,
        args: list,
        error: Exception,
    ) -> None:
        logger.error(
            "Permission denied executing command. cmd=%r, args=%r, error=%s",
            cmd, args, error,
        )

    @staticmethod
    def sending_sigkill(
        logger: logging.Logger,
        pid: int,
        pgid: int,
    ) -> None:
        logger.warning(
            "Process did not terminate gracefully after SIGTERM, sending SIGKILL. "
            "pid=%d, pgid=%d",
            pid, pgid,
        )

    @staticmethod
    def process_already_terminated(logger: logging.Logger) -> None:
        logger.info("Process already terminated")

    @staticmethod
    def cancelled(
        logger: logging.Logger,
        cmd: str,
        args: list,
        pid: Optional[int] = None,
        execution_id: Optional[str] = None,
    ) -> None:
        if execution_id:
            logger.warning(
                "Command execution was cancelled. cmd=%r, args=%r, pid=%s, execution_id=%s",
                cmd, args, pid or "N/A", execution_id,
            )
        else:
            logger.warning(
                "Command execution was cancelled. cmd=%r, args=%r, pid=%s",
                cmd, args, pid or "N/A",
            )

    @staticmethod
    def cancelled_by_user(
        logger: logging.Logger,
        execution_id: str,
        cmd: str,
        args: list,
    ) -> None:
        logger.info(
            "Command execution %s was cancelled by user. cmd=%r, args=%r",
            execution_id, cmd, args,
        )

    @staticmethod
    def unexpected_error(
        logger: logging.Logger,
        cmd: str,
        args: list,
        error: Exception,
    ) -> None:
        logger.error(
            "Unexpected error during command execution. cmd=%r, args=%r, error=%s",
            cmd, args, error,
            exc_info=True,
        )

    @staticmethod
    def finally_cleanup(
        logger: logging.Logger,
        cmd: str,
        pid: int,
    ) -> None:
        logger.info("Cleaning up process in finally block. cmd=%r, pid=%d", cmd, pid)

    @staticmethod
    def finally_terminated(logger: logging.Logger, pid: int) -> None:
        logger.info("Process terminated successfully. pid=%d", pid)

    @staticmethod
    def client_disconnect(
        logger: logging.Logger,
        cmd: str,
        args: list,
    ) -> None:
        logger.info(
            "Client disconnected, cancelling command execution. cmd=%r, args=%r",
            cmd, args,
        )

    @staticmethod
    def terminate_by_disconnect(
        logger: logging.Logger,
        cmd: str,
        args: list,
    ) -> None:
        logger.warning(
            "Terminating command execution due to client disconnection. cmd=%r, args=%r",
            cmd, args,
        )

    @staticmethod
    def terminate_by_user(
        logger: logging.Logger,
        execution_id: str,
    ) -> None:
        logger.info("Terminating command execution %s by user request", execution_id)

    @staticmethod
    def terminate_success(
        logger: logging.Logger,
        execution_id: str,
    ) -> None:
        logger.info("Command execution %s terminated successfully", execution_id)

    @staticmethod
    def websocket_task_cancelled(
        logger: logging.Logger,
        execution_id: str,
    ) -> None:
        logger.info("WebSocket handler: task for execution %s was cancelled", execution_id)

    @staticmethod
    def websocket_task_error(
        logger: logging.Logger,
        execution_id: str,
        error: Exception,
    ) -> None:
        logger.warning(
            "WebSocket handler: task for execution %s ended with error: %s",
            execution_id, error,
        )