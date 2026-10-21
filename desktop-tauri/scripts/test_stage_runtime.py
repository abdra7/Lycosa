"""Tests for the runtime staging script's file pruning and checks."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).with_name("stage_runtime.py")
_spec = importlib.util.spec_from_file_location("stage_runtime", _SCRIPT)
stage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stage)


def _symlink(link: Path, target: str) -> None:
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks need developer mode or admin on Windows")


def _postgres_tree(root: Path) -> Path:
    postgres = root / "postgres"
    for sub in ("bin", "lib", "share/extension"):
        (postgres / sub).mkdir(parents=True)
    (postgres / "bin" / "postgres").write_text("kept")
    (postgres / "lib" / "libpq.5.dylib").write_text("kept")
    return postgres


def test_prune_removes_library_symlinks_with_their_targets(tmp_path: Path) -> None:
    # Unix PostgreSQL builds ship libpgtypes.dylib -> libpgtypes.3.dylib; a
    # link left dangling after its target is pruned breaks the Tauri build.
    postgres = _postgres_tree(tmp_path)
    lib = postgres / "lib"
    (lib / "libpgtypes.3.dylib").write_text("pruned")
    _symlink(lib / "libpgtypes.dylib", "libpgtypes.3.dylib")
    (lib / "libecpg.so.6.16").write_text("pruned")
    _symlink(lib / "libecpg.so.6", "libecpg.so.6.16")
    _symlink(lib / "libecpg.so", "libecpg.so.6")

    stage.prune_postgres(postgres)

    left = sorted(p.name for p in lib.iterdir())
    assert left == ["libpq.5.dylib"]


def test_prune_removes_extension_files_and_keeps_the_rest(tmp_path: Path) -> None:
    postgres = _postgres_tree(tmp_path)
    ext = postgres / "share" / "extension"
    (ext / "uuid-ossp.control").write_text("pruned")
    (ext / "uuid-ossp--1.1.sql").write_text("pruned")
    (ext / "plpgsql.control").write_text("kept")
    (postgres / "lib" / "uuid-ossp.so").write_text("pruned")
    (postgres / "bin" / "ecpg").write_text("pruned")

    stage.prune_postgres(postgres)

    assert sorted(p.name for p in ext.iterdir()) == ["plpgsql.control"]
    assert sorted(p.name for p in (postgres / "lib").iterdir()) == ["libpq.5.dylib"]
    assert sorted(p.name for p in (postgres / "bin").iterdir()) == ["postgres"]


def test_dangling_symlinks_fail_staging_with_their_paths(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "real.so").write_text("x")
    _symlink(tmp_path / "lib" / "ok.so", "real.so")
    stage.check_no_dangling_symlinks(tmp_path)

    _symlink(tmp_path / "lib" / "gone.so", "missing.so")
    with pytest.raises(SystemExit, match="dangling"):
        stage.check_no_dangling_symlinks(tmp_path)


def test_unresolved_libraries_are_all_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_ldd(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        out = "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x0)\n"
        if Path(cmd[1]).name == "bad.so":
            out += "\tlibossp-uuid.so.16 => not found\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(stage.subprocess, "run", fake_ldd)
    (tmp_path / "good.so").write_bytes(b"\x7fELF" + bytes(12))
    (tmp_path / "notes.txt").write_text("not an ELF file")
    stage.check_linked_libraries(tmp_path)

    (tmp_path / "bad.so").write_bytes(b"\x7fELF" + bytes(12))
    with pytest.raises(SystemExit, match="1 shared libraries"):
        stage.check_linked_libraries(tmp_path)
