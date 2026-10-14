import os
import sys
import time
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional


class BaselineLogger:
    """
    Dedicated Logger for the Baseline Extraction Pipeline (EL / IFA).
    
    Guarantees:
    1. Direct terminal output with immediate flush (bypasses any stdout filtering).
    2. Synchronous file persistence in both a unified log file (`logs/baseline_extraction.log`)
       and a run-specific log file (`logs/baseline_p{project_id}_d{document_id}.log`).
    3. Clear, structured reporting of INPUT, PROCESS, and OUTPUT for every step.
    """

    def __init__(self, project_id: int, document_id: int, log_dir: Optional[str] = None):
        self.project_id = project_id
        self.document_id = document_id

        if log_dir is None:
            # Backend root directory: new_agent_2_BE/logs
            backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.log_dir = os.path.join(backend_root, "logs")
        else:
            self.log_dir = log_dir

        os.makedirs(self.log_dir, exist_ok=True)

        self.unified_log_path = os.path.join(self.log_dir, "baseline_extraction.log")
        self.run_log_path = os.path.join(self.log_dir, f"baseline_p{project_id}_d{document_id}.log")
        self._lock = threading.Lock()

    def _format_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def raw_log(self, text: str):
        """Writes text directly to stdout and appends to the log files with immediate flush."""
        # 1. Terminal stdout write with flush
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except Exception:
            pass

        # 2. File write with lock and flush
        with self._lock:
            try:
                with open(self.unified_log_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
                    f.flush()
            except Exception:
                pass

            try:
                with open(self.run_log_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
                    f.flush()
            except Exception:
                pass

    def log(self, message: str, level: str = "INFO"):
        ts = self._format_timestamp()
        line = f"[{ts}] [{level}] {message}"
        self.raw_log(line)

    def banner(self, title: str):
        sep = "=" * 80
        self.raw_log(f"\n{sep}")
        self.raw_log(f"  {title}")
        self.raw_log(f"{sep}")

    def step_start(self, step_num: int, step_title: str):
        sep = "-" * 80
        ts = self._format_timestamp()
        self.raw_log(f"\n{sep}")
        self.raw_log(f"[*] STEP {step_num}: {step_title.upper()} | Started: {ts}")
        self.raw_log(f"{sep}")

    def step_end(self, step_num: int, step_title: str, duration_sec: float):
        ts = self._format_timestamp()
        self.raw_log(f"[✓] STEP {step_num} COMPLETED in {duration_sec:.2f}s | {ts}\n")

    def input(self, label: str, details: Any = None):
        ts = self._format_timestamp()
        if details is not None:
            self.raw_log(f"[{ts}] [INPUT]  --> {label}: {details}")
        else:
            self.raw_log(f"[{ts}] [INPUT]  --> {label}")

    def process(self, description: str):
        ts = self._format_timestamp()
        self.raw_log(f"[{ts}] [PROCESS] {description}")

    def output(self, label: str, details: Any = None):
        ts = self._format_timestamp()
        if details is not None:
            self.raw_log(f"[{ts}] [OUTPUT] <-- {label}: {details}")
        else:
            self.raw_log(f"[{ts}] [OUTPUT] <-- {label}")

    def item(self, idx: int, name: str, details: Optional[Dict[str, Any]] = None):
        detail_str = ""
        if details:
            parts = [f"{k}={v}" for k, v in details.items() if v is not None]
            if parts:
                detail_str = " | " + " | ".join(parts)
        self.raw_log(f"    [{idx:02d}] {name}{detail_str}")

    def error(self, context: str, error_msg: str, traceback_str: Optional[str] = None):
        ts = self._format_timestamp()
        sep = "!" * 80
        self.raw_log(f"\n{sep}")
        self.raw_log(f"[{ts}] [ERROR] Baseline Extraction failed at: {context}")
        self.raw_log(f"Error Message: {error_msg}")
        if traceback_str:
            self.raw_log(f"Traceback:\n{traceback_str}")
        self.raw_log(f"{sep}\n")

    def get_log_paths(self) -> Dict[str, str]:
        return {
            "unified_log": self.unified_log_path,
            "run_log": self.run_log_path
        }
