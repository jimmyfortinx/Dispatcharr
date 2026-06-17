"""Label Postgres connections by Dispatcharr process role (for pg_stat_activity)."""

from __future__ import annotations

import os
import sys


def _is_uwsgi_worker() -> bool:
    """True when running inside a uWSGI worker (not master or a non-uWSGI process)."""
    try:
        import uwsgi
    except ImportError:
        return False

    worker_id = getattr(uwsgi, "worker_id", None)
    if worker_id is None:
        return False

    try:
        return worker_id() > 0
    except TypeError:
        unbound = getattr(worker_id, "__func__", None)
        if unbound is None:
            raise
        return unbound() > 0


def get_process_role(argv: list[str] | None = None) -> str:
    argv = argv if argv is not None else sys.argv
    argv0 = os.path.basename(argv[0]) if argv else ""
    cmdline = " ".join(argv)

    if "celery" in argv0 or any("celery" in arg for arg in argv):
        if "beat" in cmdline:
            return "celery-beat"
        if "-Q" in argv:
            try:
                if "dvr" in argv[argv.index("-Q") + 1]:
                    return "celery-dvr"
            except (IndexError, ValueError):
                pass
        return "celery-worker"
    if "daphne" in argv0:
        return "daphne"
    if argv0 == "manage.py" and len(argv) > 1:
        return f"manage-{argv[1]}"
    if _is_uwsgi_worker() or argv0 == "uwsgi":
        return "uwsgi"
    return "django"


def db_application_name() -> str:
    return f"Dispatcharr-{get_process_role()}-{os.getpid()}"
