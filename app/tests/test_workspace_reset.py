"""Regression tests for the Streamlit workspace reset helpers."""

from __future__ import annotations

from app.ui import dashboard


def test_delete_reset_artifacts_removes_cache_dirs_db_and_sqlite_sidecars(tmp_path):
    streamlit_cache = tmp_path / ".streamlit" / "cache"
    pytest_cache = tmp_path / ".pytest_cache"
    db_path = tmp_path / "data" / "healthai.db"

    streamlit_cache.mkdir(parents=True)
    pytest_cache.mkdir()
    db_path.parent.mkdir()
    (streamlit_cache / "cache.bin").write_text("cached", encoding="utf-8")
    (pytest_cache / "state").write_text("cached", encoding="utf-8")
    db_path.write_text("db", encoding="utf-8")
    db_path.with_name(db_path.name + "-wal").write_text("wal", encoding="utf-8")
    db_path.with_name(db_path.name + "-shm").write_text("shm", encoding="utf-8")

    removed, errors = dashboard._delete_reset_artifacts(
        project_root=tmp_path,
        db_path=db_path,
    )

    assert errors == []
    assert len(removed) == 5
    assert not streamlit_cache.exists()
    assert not pytest_cache.exists()
    assert not db_path.exists()
    assert not db_path.with_name(db_path.name + "-wal").exists()
    assert not db_path.with_name(db_path.name + "-shm").exists()
