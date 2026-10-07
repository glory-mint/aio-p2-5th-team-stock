"""OpenDART의 기업코드·공시목록·원문을 조회하는 클라이언트.

XML 본문을 구조적으로 해석하거나 MCP 응답 형태로 변환하지 않는다. 그 역할은
service와 rag/parser 계층에 둔다.
"""

from __future__ import annotations

from io import BytesIO
import re
from typing import Any, cast
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import httpx

from app.core.config import DisclosureConfig, get_config
from app.schemas.re import (
    DartCorpCode,
    DartDisclosureListResponse,
    DartDisclosureRecord,
    DartDocument,
    DartPeriodicReportType,
)


class DartClientError(RuntimeError):
    """OpenDART 통신 중 발생하는 공통 오류."""


class DartTimeoutError(DartClientError):
    """OpenDART 요청이 시간 안에 끝나지 않았다."""


class DartUnavailableError(DartClientError):
    """OpenDART 연결 또는 응답 형식에 문제가 있다."""


class DartApiError(DartClientError):
    """OpenDART가 HTTP 200과 함께 오류 상태값을 반환했다."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        super().__init__(message)


class DartClient:
    """OpenDART 필수 엔드포인트 3개를 감싼 동기 HTTP 클라이언트."""

    def __init__(
        self,
        config: DisclosureConfig | None = None,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config or get_config()
        self._config.validate_for_disclosures()
        self._client = httpx.Client(
            base_url=self._config.dart_api_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DartClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_corp_codes(self) -> list[DartCorpCode]:
        """``corpCode.xml`` ZIP을 풀어 상장사·비상장사 기업코드 목록을 반환한다."""

        xml = self._get_zip_xml("/corpCode.xml", {})
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as error:
            raise DartUnavailableError("DART 기업코드 XML 형식이 올바르지 않습니다.") from error

        records: list[DartCorpCode] = []
        for item in root.findall(".//list"):
            records.append(
                {
                    "corp_code": (item.findtext("corp_code") or "").strip(),
                    "corp_name": (item.findtext("corp_name") or "").strip(),
                    "corp_eng_name": (item.findtext("corp_eng_name") or "").strip(),
                    "stock_code": (item.findtext("stock_code") or "").strip(),
                    "modify_date": (item.findtext("modify_date") or "").strip(),
                }
            )
        return records

    def get_disclosures(
        self,
        *,
        corp_code: str,
        begin_date: str,
        end_date: str,
        page_no: int = 1,
        page_count: int = 100,
        disclosure_type: str | None = None,
    ) -> DartDisclosureListResponse:
        """``list.json`` 원본 응답을 반환한다.

        ``status == '013'``은 정상적인 조회 결과 없음이므로 예외로 바꾸지 않는다.
        """

        if not 1 <= page_count <= 100:
            raise ValueError("page_count는 1~100이어야 합니다.")
        if page_no < 1:
            raise ValueError("page_no는 1 이상이어야 합니다.")

        params: dict[str, Any] = {
            "corp_code": corp_code,
            "bgn_de": begin_date,
            "end_de": end_date,
            "page_no": page_no,
            "page_count": page_count,
        }
        if disclosure_type:
            params["pblntf_ty"] = disclosure_type
        return cast(
            DartDisclosureListResponse,
            self._get_json("/list.json", params, allow_no_data=True),
        )

    def get_document(self, receipt_number: str) -> DartDocument:
        """``document.xml`` ZIP을 해제해 원문 XML만 반환한다.

        표 평탄화·섹션 추출·청킹은 여기서 하지 않는다.
        """

        if not receipt_number.isdigit() or len(receipt_number) != 14:
            raise ValueError("receipt_number는 14자리 숫자 문자열이어야 합니다.")
        return {
            "receipt_number": receipt_number,
            "xml": self._get_zip_xml("/document.xml", {"rcept_no": receipt_number}),
        }

    def get_periodic_reports(
        self,
        *,
        corp_code: str,
        begin_date: str,
        end_date: str,
        report_type: DartPeriodicReportType,
    ) -> list[DartDisclosureRecord]:
        """정기공시에서 사업·반기·분기보고서만 골라 반환한다.

        DART에는 보고서 종류별 목록 API가 없으므로 정기공시(``pblntf_ty=A``)를
        조회한 뒤 제목으로 분류한다. ``[기재정정]`` 같은 접두어도 허용한다.
        """

        report_names = {
            "annual": "사업보고서",
            "semi_annual": "반기보고서",
            "quarterly": "분기보고서",
        }
        response = self.get_disclosures(
            corp_code=corp_code,
            begin_date=begin_date,
            end_date=end_date,
            page_count=100,
            disclosure_type="A",
        )
        expected_name = report_names[report_type]
        return [
            record
            for record in response.get("list", [])
            if self._normalized_report_name(record["report_nm"]).startswith(expected_name)
        ]

    def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        allow_no_data: bool = False,
    ) -> dict[str, Any]:
        response = self._request(path, params)
        try:
            payload = response.json()
        except ValueError as error:
            raise DartUnavailableError("DART JSON 응답 형식이 올바르지 않습니다.") from error
        if not isinstance(payload, dict):
            raise DartUnavailableError("DART JSON 응답이 객체가 아닙니다.")

        status = str(payload.get("status", ""))
        if status == "000" or (allow_no_data and status == "013"):
            return payload
        raise DartApiError(
            status or "unknown",
            str(payload.get("message", "DART 요청에 실패했습니다.")),
        )

    def _get_zip_xml(self, path: str, params: dict[str, Any]) -> str:
        response = self._request(path, params)
        try:
            with ZipFile(BytesIO(response.content)) as archive:
                filenames = [name for name in archive.namelist() if not name.endswith("/")]
                if not filenames:
                    raise DartUnavailableError("DART ZIP 응답에 파일이 없습니다.")
                filename = self._select_xml_member(archive, filenames, params)
                content = archive.read(filename)
        except BadZipFile as error:
            self._raise_xml_api_error(response.content)
            raise DartUnavailableError("DART가 올바른 ZIP 응답을 반환하지 않았습니다.") from error

        for encoding in ("utf-8", "cp949"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                pass
        raise DartUnavailableError("DART XML 인코딩을 해석하지 못했습니다.")

    @staticmethod
    def _select_xml_member(
        archive: ZipFile, filenames: list[str], params: dict[str, Any]
    ) -> str:
        """document.xml ZIP에서 첨부가 아닌 접수번호 본문을 고른다."""

        receipt_number = str(params.get("rcept_no", ""))
        if not receipt_number:
            return filenames[0]
        expected_name = f"{receipt_number}.xml".casefold()
        for filename in filenames:
            if filename.rsplit("/", maxsplit=1)[-1].casefold() == expected_name:
                return filename
        xml_files = [name for name in filenames if name.casefold().endswith(".xml")]
        candidates = xml_files or filenames
        return max(candidates, key=lambda name: archive.getinfo(name).file_size)

    @staticmethod
    def _raise_xml_api_error(content: bytes) -> None:
        """ZIP 엔드포인트가 반환한 오류 XML을 DART 상태 오류로 바꾼다."""

        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            return
        status = (root.findtext(".//status") or "").strip()
        if status and status != "000":
            message = (root.findtext(".//message") or "DART 요청에 실패했습니다.").strip()
            raise DartApiError(status, message)

    @staticmethod
    def _normalized_report_name(report_name: str) -> str:
        """DART 정정 공시 접두어를 제거해 원래 보고서명을 비교한다."""

        normalized = report_name.strip()
        while normalized.startswith("["):
            normalized = re.sub(r"^\[[^\]]+\]\s*", "", normalized, count=1)
        return normalized

    def _request(self, path: str, params: dict[str, Any]) -> httpx.Response:
        request_params = {"crtfc_key": self._config.dart_api_key, **params}
        last_error: DartClientError | None = None

        # 네트워크·타임아웃 오류만 한 번 재시도한다.
        for _ in range(2):
            try:
                response = self._client.get(path, params=request_params)
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                last_error = DartTimeoutError("DART 요청 시간이 초과되었습니다.")
            except httpx.HTTPStatusError as error:
                raise DartUnavailableError(
                    f"DART HTTP 상태 코드: {error.response.status_code}"
                ) from error
            except httpx.HTTPError:
                last_error = DartUnavailableError("DART 서버에 연결하지 못했습니다.")

        assert last_error is not None
        raise last_error
