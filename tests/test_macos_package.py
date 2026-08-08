from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAC_SPEC = ROOT / "packaging" / "stockwatcher-macos.spec"


def test_macos_pyinstaller_spec_is_native_and_excludes_windows_diagnostics() -> None:
    copy = MAC_SPEC.read_text(encoding="utf-8")

    assert 'name="StockWatcher.app"' in copy
    assert 'bundle_identifier="com.kahlilhazel.stockwatcher"' in copy
    assert '"NSHighResolutionCapable": True' in copy
    assert '"stockwatcher-macos.png"' in copy
    assert '"keyring.backends.Windows"' in copy
    assert '"stock_watcher.providers.tdxquant"' in copy
    assert '"stock_watcher.ui.tdx_session"' in copy
    assert "packaging/windows" not in copy
    assert "stockwatcher_portable.py" not in copy
    assert "Token" not in copy
    assert "token=" not in copy.casefold()
