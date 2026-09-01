from stock_watcher.ui.windows_runtime import exit_if_secondary_instance

exit_if_secondary_instance()

from stock_watcher.ui.app import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
