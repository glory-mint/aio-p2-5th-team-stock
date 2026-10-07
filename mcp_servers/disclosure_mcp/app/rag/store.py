"""사업보고서 청크와 pgvector 검색을 위한 전용 저장소."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from collections.abc import Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .chunker import ReportChunk


@dataclass(frozen=True)
class StoredReport:
    id: int
    stock_code: str
    report_year: int
    report_type: str
    report_name: str
    receipt_number: str
    published_at: datetime | None
    source_url: str


@dataclass(frozen=True)
class SearchHit:
    section_title: str
    content: str
    score: float


class ReportStore:
    """보고서 메타데이터와 벡터 청크를 트랜잭션으로 교체·검색한다."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("database_url is required.")
        self._database_url = database_url

    def get_report(
        self, stock_code: str, report_type: str, report_year: int | None = None
    ) -> StoredReport | None:
        query = """
            SELECT id, stock_code, report_year, report_type, report_name, receipt_number, published_at, source_url
            FROM annual_reports
            WHERE stock_code = %s AND report_type = %s
        """
        params: tuple[object, ...] = (stock_code, report_type)
        if report_year is not None:
            query += " AND report_year = %s"
            params = (stock_code, report_type, report_year)
        query += " ORDER BY report_year DESC LIMIT 1"
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            row = connection.execute(query, params).fetchone()
        return StoredReport(**row) if row else None

    def available_years(self, stock_code: str, report_type: str) -> list[int]:
        query = """
            SELECT report_year FROM annual_reports
            WHERE stock_code = %s AND report_type = %s
            ORDER BY report_year DESC
        """
        with psycopg.connect(self._database_url) as connection:
            rows = connection.execute(query, (stock_code, report_type)).fetchall()
        return [row[0] for row in rows]

    def replace_report(
        self,
        *,
        stock_code: str,
        report_year: int,
        report_type: str,
        report_name: str,
        receipt_number: str,
        published_at: datetime,
        source_url: str,
        chunks: Sequence[ReportChunk],
        embeddings: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> None:
        """해당 기업·연도의 기존 청크를 새 정정본으로 원자적으로 교체한다."""

        if len(chunks) != len(embeddings):
            raise ValueError("chunks와 embeddings 길이가 일치해야 합니다.")
        report_hash = hashlib.sha256(
            "\n".join(chunk.content for chunk in chunks).encode("utf-8")
        ).hexdigest()
        vector_rows = [
            (
                chunk.chunk_index,
                chunk.section_title,
                chunk.content,
                hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                chunk.has_table,
                _vector_literal(vector),
                embedding_model,
                Jsonb({}),
            )
            for chunk, vector in zip(chunks, embeddings, strict=True)
        ]
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                DELETE FROM annual_reports
                WHERE stock_code = %s AND report_year = %s AND report_type = %s
                """,
                (stock_code, report_year, report_type),
            )
            report_row = connection.execute(
                """
                INSERT INTO annual_reports (
                    stock_code, report_year, report_type, report_name, receipt_number, published_at,
                    source_url, chunk_count, content_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    stock_code,
                    report_year,
                    report_type,
                    report_name,
                    receipt_number,
                    published_at,
                    source_url,
                    len(chunks),
                    report_hash,
                ),
            ).fetchone()
            assert report_row is not None
            report_id = report_row[0]
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO report_chunks (
                        annual_report_id, chunk_index, section_title, content, content_hash,
                        has_table, embedding, embedding_model, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                    """,
                    [(report_id, *row) for row in vector_rows],
                )

    def search(
        self,
        *,
        report_id: int,
        query_embedding: Sequence[float],
        top_k: int,
    ) -> list[SearchHit]:
        query = """
            SELECT section_title, content, 1 - (embedding <=> %s::vector) AS score
            FROM report_chunks
            WHERE annual_report_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        vector = _vector_literal(query_embedding)
        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            rows = connection.execute(query, (vector, report_id, vector, top_k)).fetchall()
        return [SearchHit(**row) for row in rows]


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(value, ".12g") for value in vector) + "]"
