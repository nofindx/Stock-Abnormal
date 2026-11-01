import unittest
from unittest.mock import patch

from server.services.announcement_service import AnnouncementService


class PdfBodyExtractionTests(unittest.TestCase):
    def test_live_compatible_page_size_and_sorting_retrieve_second_page(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        calls = []
        def request(payload):
            calls.append(payload)
            self.assertEqual(payload["pageSize"], 30)
            self.assertEqual(payload["sortType"], "desc")
            page = payload["pageNum"]
            return {"announcements": [{"announcementTitle": "股票交易异常波动公告", "secCode": "002724",
                     "adjunctUrl": f"page-{page}.pdf"}], "hasMore": page == 1, "totalAnnouncement": 2}
        service._request = request
        result = service.query_market("20260805", "20260805", page_size=50)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(len(calls), 6)

    def test_repeated_page_stops_without_using_entire_daily_budget(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        calls = []
        def request(payload):
            calls.append(payload)
            return {"announcements": [{"announcementTitle": "股票交易异常波动公告", "secCode": "002724",
                     "adjunctUrl": "same.pdf"}], "hasMore": True}
        service._request = request
        result = service.query_market("20260805", "20260805")
        self.assertFalse(result["coverage"]["complete"])
        self.assertIn("repeated_page", result["error"])
        self.assertEqual(len(calls), 6)

    def test_total_count_mismatch_is_not_complete_coverage(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        service._request = lambda _: {"announcements": [], "hasMore": False, "totalAnnouncement": 42}
        result = service.query_market("20260805", "20260805")
        self.assertFalse(result["coverage"]["complete"])
        self.assertIn("total_mismatch", result["error"])

    def test_multi_day_total_mismatch_is_retried_as_date_partitions(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        calls = []

        def request(payload):
            calls.append(dict(payload))
            window = payload["seDate"]
            return {"announcements": [{"announcementTitle": "股票交易异常波动公告",
                    "secCode": "002724", "adjunctUrl": window + ".pdf"}],
                    "hasMore": False, "totalAnnouncement": 2 if window == "2026-10-29~2026-10-30" else 1}

        service._request = request
        result = service.query_market("20261029", "20261030")
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(len(calls), 9)

    def test_repeated_multi_day_page_is_retried_as_date_partitions(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        calls = []

        def request(payload):
            calls.append(dict(payload))
            window = payload["seDate"]
            return {"announcements": [{"announcementTitle": "股票交易异常波动公告",
                    "secCode": "002724", "adjunctUrl": window + ".pdf"}],
                    "hasMore": window == "2026-10-29~2026-10-30"}

        service._request = request
        result = service.query_market("20261029", "20261030")
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(len(calls), 12)

    def test_board_inquiry_letter_is_supporting_material_not_an_event(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        service._request = lambda _: {"announcements": [{"announcementTitle": "宝泰隆新材料股份有限公司董事会关于股票交易异常波动问询函",
            "secCode": "601011", "adjunctUrl": "finalpage/2026-08-21/inquiry.PDF"}], "hasMore": False}
        result = service.query_market("20260821", "20260821")
        self.assertEqual(result["items"], [])
        self.assertEqual(len(result["supportingItems"]), 1)

    def test_full_range_is_split_instead_of_silently_truncated(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        service.market_pages_per_range = 1
        calls = []
        def request(payload):
            calls.append(dict(payload))
            window = payload["seDate"]
            return {"announcements": [{"announcementTitle": "股票交易异常波动公告", "secCode": "002724",
                    "adjunctUrl": window + ".pdf"}], "hasMore": window == "2026-10-29~2026-10-30"}
        service._request = request
        result = service.query_market("20261029", "20261030")
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(len(calls), 9)
        self.assertEqual(len(result["items"]), 3)

    def test_single_day_cap_is_reported_incomplete(self):
        service = AnnouncementService()
        service.keywords = ("股票交易异常波动",)
        service.market_pages_per_day = 2
        service._request = lambda p: {"announcements": [{"announcementTitle": "股票交易异常波动公告",
            "secCode": "002724", "adjunctUrl": "same.pdf"}], "hasMore": True}
        result = service.query_market("20261030", "20261030")
        self.assertTrue(result["partial"])
        self.assertFalse(result["available"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertIn("pagination_incomplete", result["error"])
        self.assertEqual(len(result["items"]), 1)

    def setUp(self):
        self.service = AnnouncementService()

    def test_text_layer_is_preferred_and_cached(self):
        body = "公司股票连续10个交易日涨幅偏离值累计达到100%，构成严重异常波动。"
        with patch("server.services.announcement_service.urlopen") as open_url, patch.object(
            self.service, "_extract_pdf_text_layer", return_value=body
        ) as text_layer, patch.object(self.service, "_extract_pdf_ocr") as ocr:
            open_url.return_value.read.return_value = b"%PDF-test"
            result = self.service.extract_pdf_body("https://static.cninfo.com.cn/a.pdf")
            cached = self.service.extract_pdf_body("https://static.cninfo.com.cn/a.pdf")
        self.assertEqual(result["text"], body)
        self.assertEqual(result["method"], "text_layer")
        self.assertEqual(cached, result)
        open_url.assert_called_once()
        text_layer.assert_called_once()
        ocr.assert_not_called()

    def test_scanned_pdf_uses_ocr_fallback(self):
        body = "公司股票连续10个交易日涨幅偏离值累计达到100%，构成严重异常波动。"
        with patch("server.services.announcement_service.urlopen") as open_url, patch.object(
            self.service, "_extract_pdf_text_layer", return_value=""
        ), patch.object(self.service, "_extract_pdf_ocr", return_value={
            "text": body, "confidence": 83.4, "pages": 2, "error": ""
        }) as ocr:
            open_url.return_value.read.return_value = b"%PDF-scan"
            result = self.service.extract_pdf_body("https://static.cninfo.com.cn/scanned.pdf")
        self.assertEqual(result["text"], body)
        self.assertEqual(result["method"], "ocr")
        self.assertEqual(result["confidence"], 83.4)
        self.assertEqual(result["pages"], 2)
        ocr.assert_called_once_with(b"%PDF-scan")

    def test_low_confidence_ocr_remains_unreadable(self):
        with patch("server.services.announcement_service.urlopen") as open_url, patch.object(
            self.service, "_extract_pdf_text_layer", return_value=""
        ), patch.object(self.service, "_extract_pdf_ocr", return_value={
            "text": "", "confidence": 11.2, "pages": 1, "error": "ocr_low_confidence"
        }):
            open_url.return_value.read.return_value = b"%PDF-scan"
            result = self.service.extract_pdf_body("https://static.cninfo.com.cn/unclear.pdf")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["method"], "unavailable")
        self.assertEqual(result["error"], "ocr_low_confidence")

    def test_non_pdf_response_is_not_passed_to_parsers(self):
        with patch("server.services.announcement_service.urlopen") as open_url, patch.object(
            self.service, "_extract_pdf_text_layer"
        ) as text_layer:
            open_url.return_value.read.return_value = b"<html>not a pdf</html>"
            result = self.service.extract_pdf_body("https://static.cninfo.com.cn/not-pdf")
        self.assertEqual(result["error"], "not_pdf")
        text_layer.assert_not_called()

    def test_company_event_announcements_are_separated_from_inquiry_replies(self):
        self.service.keywords = ("股票交易异常波动",)
        responses = iter([
            {"announcements": [
                {"announcementTitle": "股票交易异常波动公告", "secCode": "603728", "secName": "鸣志电器",
                 "adjunctUrl": "finalpage/2026-10-24/notice.pdf", "announcementTime": 1792771200000},
                {"announcementTitle": "关于公司股票交易异常波动征询函的回函", "secCode": "603728", "secName": "鸣志电器",
                 "adjunctUrl": "finalpage/2026-10-24/reply.pdf", "announcementTime": 1792771200000},
            ], "hasMore": False},
            {"announcements": [], "hasMore": False},
            {"announcements": [], "hasMore": False},
        ])
        self.service._request = lambda _payload: next(responses)
        result = self.service.query_market("2026-10-24", "2026-10-24")
        self.assertEqual([item["title"] for item in result["items"]], ["股票交易异常波动公告"])
        self.assertEqual([item["title"] for item in result["supportingItems"]], ["关于公司股票交易异常波动征询函的回函"])


if __name__ == "__main__":
    unittest.main()
