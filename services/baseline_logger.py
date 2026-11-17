import sys
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

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
        self.timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if log_dir is None:
            # Backend root directory: new_agent_2_BE/logs
            backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.log_dir = os.path.join(backend_root, "logs")
        else:
            self.log_dir = log_dir

        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception:
            pass

        self.unified_log_path = os.path.join(self.log_dir, "baseline_extraction.log")
        self.run_log_path = os.path.join(self.log_dir, f"baseline_p{project_id}_d{document_id}.log")
        self.run_timestamp_log_path = os.path.join(self.log_dir, f"baseline_p{project_id}_d{document_id}_{self.timestamp_str}.log")
        self.step1_log_path = os.path.join(self.log_dir, f"step1_document_parsing_p{project_id}_d{document_id}.log")
        self.step2_log_path = os.path.join(self.log_dir, f"step2_scope_sections_p{project_id}_d{document_id}.log")
        self.step3_log_path = os.path.join(self.log_dir, f"step3_candidate_extraction_p{project_id}_d{document_id}.log")
        self.current_step = None
        self._lock = threading.Lock()

    def _format_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def raw_log(self, text: str):
        if os.getenv("ENABLE_STEP_PRINTS", "true").lower() in ("true", "1", "yes"):
            try:
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
            except Exception:
                try:
                    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
                    safe_text = text.encode(enc, errors="replace").decode(enc)
                    sys.stdout.write(safe_text + "\n")
                    sys.stdout.flush()
                except Exception:
                    pass

        if os.getenv("ENABLE_DISK_LOGS", "true").lower() in ("true", "1", "yes"):
            try:
                with self._lock:
                    if not os.path.exists(self.log_dir):
                        os.makedirs(self.log_dir, exist_ok=True)
                    # 1. Main run log and unified cumulative log
                    with open(self.run_log_path, "a", encoding="utf-8") as f_run:
                        f_run.write(text + "\n")
                    if hasattr(self, "run_timestamp_log_path") and self.run_timestamp_log_path:
                        with open(self.run_timestamp_log_path, "a", encoding="utf-8") as f_ts:
                            f_ts.write(text + "\n")
                    with open(self.unified_log_path, "a", encoding="utf-8") as f_u:
                        f_u.write(text + "\n")

                    # 2. Per-step logs for Steps 1, 2, 3
                    step_val = str(getattr(self, "current_step", ""))
                    if step_val in ("1", "Step 1", "step1"):
                        with open(self.step1_log_path, "a", encoding="utf-8") as f_s1:
                            f_s1.write(text + "\n")
                    elif step_val in ("2", "Step 2", "step2"):
                        with open(self.step2_log_path, "a", encoding="utf-8") as f_s2:
                            f_s2.write(text + "\n")
                    elif step_val in ("3", "Step 3", "step3"):
                        with open(self.step3_log_path, "a", encoding="utf-8") as f_s3:
                            f_s3.write(text + "\n")
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

    def step_start(self, step_num: Any, step_title: str):
        self.current_step = step_num
        sep = "-" * 80
        ts = self._format_timestamp()
        self.raw_log(f"\n{sep}")
        self.raw_log(f"[*] STEP {step_num}: {step_title.upper()} | Started: {ts}")
        self.raw_log(f"{sep}")

    def step_end(self, step_num: Any, step_title: str, duration_sec: float):
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
        err_block = (
            f"\n{sep}\n"
            f"[{ts}] [ERROR] Baseline Extraction failed at: {context}\n"
            f"Error Message: {error_msg}\n"
        )
        if traceback_str:
            err_block += f"Traceback:\n{traceback_str}\n"
        err_block += f"{sep}\n"
        self.raw_log(err_block)
        pass

    def get_log_paths(self) -> Dict[str, str]:
        return {
            "unified_log": self.unified_log_path,
            "run_log": self.run_log_path
        }


class BaseStepInspectorLogger:
    """
    Standardized base logger for Step 4-10 Inspectors, providing thread-safe
    dual terminal/file output, structured section headers, and error logging without duplicate code.
    """
    def __init__(self, step_prefix: str, project_id: int, document_id: int, timestamp_str: str, log_dir: Optional[str] = None):
        self.step_prefix = step_prefix
        self.project_id = project_id
        self.document_id = document_id
        self.timestamp_str = timestamp_str

        if log_dir is None:
            backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.log_dir = os.path.join(backend_root, "logs")
        else:
            self.log_dir = log_dir

        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except Exception:
            pass

        self.run_log_path = os.path.join(self.log_dir, f"{step_prefix}_p{project_id}_d{document_id}_{timestamp_str}.log")
        self.active_log_path = os.path.join(self.log_dir, f"{step_prefix}_p{project_id}_d{document_id}.log")
        self._lock = threading.Lock()

    def _format_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def raw_log(self, text: str):
        if os.getenv("ENABLE_STEP_PRINTS", "true").lower() in ("true", "1", "yes"):
            try:
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
            except Exception:
                try:
                    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
                    safe_text = text.encode(enc, errors="replace").decode(enc)
                    sys.stdout.write(safe_text + "\n")
                    sys.stdout.flush()
                except Exception:
                    pass

        if os.getenv("ENABLE_DISK_LOGS", "true").lower() in ("true", "1", "yes"):
            try:
                with self._lock:
                    if not os.path.exists(self.log_dir):
                        os.makedirs(self.log_dir, exist_ok=True)
                    if hasattr(self, "active_log_path") and self.active_log_path:
                        with open(self.active_log_path, "a", encoding="utf-8") as f_act:
                            f_act.write(text + "\n")
                    if hasattr(self, "run_log_path") and self.run_log_path:
                        with open(self.run_log_path, "a", encoding="utf-8") as f_run:
                            f_run.write(text + "\n")
            except Exception:
                pass

    def log(self, message: str, level: str = "INFO"):
        prefix = f"[{self._format_timestamp()}] [{level.upper()}] "
        self.raw_log(f"{prefix}{message}")

    def banner(self, title: str):
        border = "=" * 80
        self.raw_log(f"\n{border}\n  {title}\n{border}")

    def section(self, title: str):
        border = "-" * 80
        self.raw_log(f"\n{border}\n[*] {title} | {self._format_timestamp()}\n{border}")

    def input(self, label: str, value: Any):
        self.raw_log(f"[{self._format_timestamp()}] [INPUT]  --> {label}: {value}")

    def process(self, description: str):
        self.raw_log(f"[{self._format_timestamp()}] [PROCESS] {description}")

    def output(self, label: str, value: Any):
        if isinstance(value, dict):
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] <-- {label}:")
            for k, v in value.items():
                self.raw_log(f"    - {k}: {v}")
        elif isinstance(value, list):
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] <-- {label} ({len(value)} items):")
            for item in value:
                self.raw_log(f"    - {item}")
        else:
            self.raw_log(f"[{self._format_timestamp()}] [OUTPUT] <-- {label}: {value}")

    def item(self, index: int, name: str, details: Dict[str, Any]):
        details_str = " | ".join(f"{k}={v}" for k, v in details.items())
        self.raw_log(f"    [{index:03d}] {name} | {details_str}")

    def quality_check(self, check_name: str, status: str, details: str):
        self.raw_log(f"    [{status}] {check_name}: {details}")

    def error(self, context: str, error_msg: str, traceback_str: Optional[str] = None):
        ts = self._format_timestamp()
        sep = "!" * 80
        err_block = (
            f"\n{sep}\n"
            f"[{ts}] [ERROR] Problem in {self.__class__.__name__} at: {context}\n"
            f"Error Message: {error_msg}\n"
        )
        if traceback_str:
            err_block += f"Traceback:\n{traceback_str}\n"
        err_block += f"{sep}\n"
        self.raw_log(err_block)

