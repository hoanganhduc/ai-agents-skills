from __future__ import annotations

import base64
import contextlib
import inspect
import io
import json
from datetime import datetime, timezone
from typing import Any


def execute_manifest(job: dict[str, Any], *, resource_class: str) -> dict[str, Any]:
    payload = dict(job.get("payload", {}) or {})
    started_at = datetime.now(timezone.utc).isoformat()
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        result = execute_python_payload(job, payload)

    return {
        "job_id": job.get("job_id"),
        "status": "completed",
        "template": job.get("template"),
        "template_version": job.get("template_version", "v1"),
        "resource_class": resource_class,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "result": json_safe(result),
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
    }


def execute_python_payload(job: dict[str, Any], payload: dict[str, Any]) -> Any:
    source = payload.get("python_source")
    if not source and payload.get("python_source_b64"):
        source = base64.b64decode(payload["python_source_b64"]).decode("utf-8")

    if not source:
        raise ValueError("v1 remote execution requires payload.python_source or payload.python_source_b64.")

    entrypoint = payload.get("entrypoint", "main")
    namespace: dict[str, Any] = {
        "__name__": "__research_compute_job__",
        "JOB": job,
    }
    exec(compile(source, f"<research-compute:{job.get('job_id', 'job')}>", "exec"), namespace, namespace)

    if entrypoint in namespace and callable(namespace[entrypoint]):
        func = namespace[entrypoint]
        signature = inspect.signature(func)
        if len(signature.parameters) == 0:
            return func()
        return func(job)

    if "RESULT" in namespace:
        return namespace["RESULT"]

    raise ValueError(f"Entrypoint '{entrypoint}' not found and RESULT was not set.")


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)
