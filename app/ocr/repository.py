"""SQLite repository for OCR page results (Milestone 9)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models.ocr_result import OCRPageResult, ProcessingMethod
from app.storage.database import DEFAULT_DB_PATH, connect, initialize_schema


class OCRResultRepository:
    """Persist + query page-level OCR results."""

    def __init__(self, conn: sqlite3.Connection | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._owns_conn = conn is None
        self.conn = conn or connect(db_path)
        initialize_schema(self.conn)

    @staticmethod
    def _row(row: sqlite3.Row) -> OCRPageResult:
        return OCRPageResult(
            ocr_id=row["ocr_id"],
            case_id=row["case_id"],
            document_id=row["document_id"],
            page_number=row["page_number"],
            raw_text=row["raw_text"] or "",
            confidence=row["confidence"],
            processing_method=ProcessingMethod(row["processing_method"]),
            timestamp=row["timestamp"],
        )

    def add(self, result: OCRPageResult) -> OCRPageResult:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO ocr_results
                (ocr_id, case_id, document_id, page_number, raw_text,
                 confidence, processing_method, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.ocr_id,
                result.case_id,
                result.document_id,
                result.page_number,
                result.raw_text,
                result.confidence,
                result.processing_method.value,
                result.timestamp,
            ),
        )
        self.conn.commit()
        return result

    def add_many(self, results: list[OCRPageResult]) -> int:
        for r in results:
            self.add(r)
        return len(results)

    def for_document(self, document_id: str) -> list[OCRPageResult]:
        rows = self.conn.execute(
            "SELECT * FROM ocr_results WHERE document_id = ? ORDER BY page_number ASC, rowid ASC",
            (document_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def for_case(self, case_id: str) -> list[OCRPageResult]:
        rows = self.conn.execute(
            "SELECT * FROM ocr_results WHERE case_id = ? ORDER BY document_id ASC, page_number ASC, rowid ASC",
            (case_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def count_for_case(self, case_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM ocr_results WHERE case_id = ?", (case_id,)
        ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()
