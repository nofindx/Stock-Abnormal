import unittest
from unittest.mock import patch

from server.services.announcement_service import AnnouncementService


class PdfBodyExtractionTests(unittest.TestCase):
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
