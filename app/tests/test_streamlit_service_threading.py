"""Regression coverage for Streamlit service/thread ownership."""

from __future__ import annotations

import threading

from app.cases.service import CaseService
from app.ui.tabs import common


def test_case_service_cache_is_thread_local(monkeypatch):
    """Streamlit must not share one sqlite-backed service across threads."""

    class DummyService:
        pass

    monkeypatch.setattr(common, "CaseService", DummyService)
    monkeypatch.setattr(common, "_SERVICE_LOCAL", threading.local())

    main_first = common.get_case_service()
    main_second = common.get_case_service()

    worker_services = []

    def worker() -> None:
        worker_services.append(common.get_case_service())
        worker_services.append(common.get_case_service())

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert main_first is main_second
    assert worker_services[0] is worker_services[1]
    assert main_first is not worker_services[0]


def test_thread_local_services_do_not_share_sqlite_connections(
    monkeypatch, tmp_path
):
    """A service created in one thread must not back UI calls in another."""

    db_path = tmp_path / "healthai.db"

    def service_factory() -> CaseService:
        return CaseService(db_path=db_path)

    monkeypatch.setattr(common, "CaseService", service_factory)
    monkeypatch.setattr(common, "_SERVICE_LOCAL", threading.local())

    main_service = common.get_case_service()
    created = main_service.create_case("main-thread.txt")

    worker_result = {}

    def worker() -> None:
        worker_service = common.get_case_service()
        cases = worker_service.list_cases()
        worker_result["service"] = worker_service
        worker_result["connection"] = worker_service.conn
        worker_result["case_ids"] = [case.case_id for case in cases]

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert worker_result["service"] is not main_service
    assert worker_result["connection"] is not main_service.conn
    assert created.case_id in worker_result["case_ids"]
