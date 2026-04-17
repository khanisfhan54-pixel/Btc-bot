import traceback
import hashlib
import time
from typing import Any, Dict, List


class TracebackEngine:
    """
    Production-grade traceback system with:
    - Adaptive frame budgeting
    - Error fingerprinting
    - Context injection
    - Deduplication support
    """

    def __init__(self):
        self._max_line_chars = 180
        self._max_total_chars = 4000

    # ==========================================
    # PUBLIC API
    # ==========================================
    def capture(
        self,
        exc: Exception,
        context: Dict[str, Any] | None = None,
        frame_budget: int = 6,
    ) -> Dict[str, Any]:
        """
        Main entrypoint.
        Returns structured traceback object.
        """

        try:
            tb_exc = traceback.TracebackException.from_exception(exc, capture_locals=False)
            stack = list(tb_exc.stack)

            frames = self._select_frames(stack, frame_budget)
            formatted_frames = self._format_frames(frames)

            error_type = type(exc).__name__
            message = str(exc)

            fingerprint = self._fingerprint(error_type, message, formatted_frames)

            return {
                "trace_id": self._generate_trace_id(),
                "type": error_type,
                "message": self._clip(message, 300),
                "frames": formatted_frames,
                "frame_count": len(stack),
                "fingerprint": fingerprint,
                "context": self._safe_context(context),
                "timestamp": time.time(),
            }
        except Exception:
            safe_type = type(exc).__name__
            safe_message = self._clip(str(exc), 300)
            return {
                "trace_id": self._generate_trace_id(),
                "type": safe_type,
                "message": safe_message,
                "frames": [],
                "frame_count": 0,
                "fingerprint": self._fingerprint(safe_type, safe_message, []),
                "context": self._safe_context(context),
                "timestamp": time.time(),
            }

    # ==========================================
    # CORE LOGIC
    # ==========================================
    def _select_frames(self, stack: List[Any], budget: int) -> List[Any]:
        budget = max(int(budget), 1)
        if len(stack) <= budget:
            return stack

        head = stack[:2]
        tail = stack[-3:]

        remaining = max(budget - (len(head) + len(tail)), 0)
        if remaining == 0:
            return (head + tail)[:budget]
        mid = stack[len(stack)//2 - remaining//2: len(stack)//2 + remaining//2]

        return (head + mid + tail)[:budget]

    def _format_frames(self, frames: List[Any]) -> List[Dict[str, Any]]:
        out = []
        total_chars = 0
        for f in frames:
            line = (f.line or "").strip()

            if len(line) > self._max_line_chars:
                line = line[: self._max_line_chars - 3] + "..."

            file_s = f.filename if isinstance(f.filename, str) else str(f.filename)
            func_s = f.name if isinstance(f.name, str) else str(f.name)
            code_s = line if isinstance(line, str) else str(line)
            file_s = file_s[:120]
            func_s = func_s[:80]
            code_s = code_s[:180]

            frame_obj = {
                "file": file_s,
                "line": f.lineno,
                "func": func_s,
                "code": code_s,
            }
            estimated = len(file_s) + len(func_s) + len(code_s) + 32
            if total_chars + estimated > self._max_total_chars:
                break
            out.append(frame_obj)
            total_chars += estimated
        return out

    # ==========================================
    # UTILITIES
    # ==========================================
    def _fingerprint(self, err_type: str, msg: str, frames: List[Dict]) -> str:
        base = err_type + msg[:50]

        for f in frames[:3]:  # only top frames for stability
            base += f["file"] + f["func"] + str(f.get("line", ""))

        return hashlib.sha1(base.encode()).hexdigest()[:16]

    def _generate_trace_id(self) -> str:
        return hex(int(time.time() * 1e6))[2:]

    def _clip(self, text: str, limit: int) -> str:
        return text[:limit] if len(text) <= limit else text[:limit - 3] + "..."

    def _safe_context(self, ctx: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(ctx, dict):
            return {}

        safe = {}
        for k, v in ctx.items():
            try:
                safe[k] = str(v)[:200]
            except Exception:
                safe[k] = "UNSERIALIZABLE"
        return safe
