"""Console entry point for the STDIO-only safe MCP profile."""

from __future__ import annotations

import logging
import sqlite3
import sys

from .config import ConfigurationError, ServerConfig
from .ledger import Ledger, LedgerError
from .locking import LockError, TargetLockManager, TargetScope
from .net_api import NetApiClient
from .server import create_server
from .service import SafeRuntimeService


def main() -> int:
    """Validate configuration and serve only newline-delimited MCP on stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
        force=True,
    )
    try:
        config = ServerConfig.from_env()
    except ConfigurationError as error:
        logging.getLogger(__name__).error("refusing unsafe startup: %s", error)
        return 2

    try:
        allowed_state_root = config.state_dir.path.parent
        ledger = Ledger(config.state_dir.path, allowed_root=allowed_state_root)
        lock_manager = TargetLockManager(
            config.state_dir.path,
            allowed_root=allowed_state_root,
        )
        target_scope = TargetScope.derive(
            workbench_host=config.workbench_host,
            workbench_port=config.workbench_port,
            project_host_path=config.project_host_path.path,
            project_engine_path=config.project_engine_path.value,
            world=config.allowed_world.value,
        )
    except (LedgerError, LockError, sqlite3.Error, OSError, ValueError) as error:
        logging.getLogger(__name__).error("refusing unsafe state directory: %s", error)
        return 2

    client = NetApiClient(config.workbench_host, config.workbench_port)
    service = SafeRuntimeService(
        config,
        client,
        ledger=ledger,
        lock_manager=lock_manager,
        target_scope=target_scope,
    )
    server = create_server(service)
    logging.getLogger(__name__).info(
        "starting safe STDIO profile; Workbench target=%s:%d",
        config.workbench_host,
        config.workbench_port,
    )
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
