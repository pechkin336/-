# -*- coding: utf-8 -*-
"""Тесты модуля безопасности (amirs.security), справочника 60 участков РК и headless-режима."""
import unittest

import amirs_converter as ac
from amirs.komi_courts import (
    KOMI_DISTRICT_NAMES,
    KOMI_SECTION_NAMES,
    komi_district_of,
    komi_match_section,
    komi_section_count,
    section_genitive,
)
from amirs.security import (
    is_safe_archive_member,
    redact_pii,
    safe_xml_fromstring,
    sanitize_csv_cell,
)


class SecurityTests(unittest.TestCase):
    def test_safe_xml_rejects_doctype_and_entity(self):
        xxe = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><root>&xxe;</root>'
        with self.assertRaises(ValueError):
            safe_xml_fromstring(xxe)

        billion_laughs = '<!DOCTYPE lolz [<!ENTITY lol "lol">]><lolz>&lol;</lolz>'
        with self.assertRaises(ValueError):
            safe_xml_fromstring(billion_laughs)

    def test_safe_xml_rejects_oversized_payload(self):
        with self.assertRaises(ValueError):
            safe_xml_fromstring(b"<root>1234567890</root>", max_bytes=10)

    def test_safe_xml_parses_valid_xml(self):
        el = safe_xml_fromstring(b"<root><item>ok</item></root>")
        self.assertEqual(el.findtext("item"), "ok")

    def test_sanitize_csv_cell_blocks_formulas_preserves_negative_numbers(self):
        self.assertEqual(sanitize_csv_cell("=CMD|' /C calc'!A0"), "'=CMD|' /C calc'!A0")
        self.assertEqual(sanitize_csv_cell("+1+1"), "'+1+1")
        self.assertEqual(sanitize_csv_cell("@SUM(A1:A2)"), "'@SUM(A1:A2)")
        self.assertEqual(sanitize_csv_cell("  =HYPERLINK()"), "'  =HYPERLINK()")
        self.assertEqual(sanitize_csv_cell("-150.50"), "-150.50")
        self.assertEqual(sanitize_csv_cell("Обычный текст"), "Обычный текст")

    def test_redact_pii_masks_sensitive_numbers(self):
        msg = "Ошибка: СНИЛС 112-233-445 95, паспорт 87 01 123456, счёт 40817810099910004312, ИНН 110112345678"
        masked = redact_pii(msg)
        self.assertNotIn("112-233-445 95", masked)
        self.assertNotIn("87 01 123456", masked)
        self.assertNotIn("40817810099910004312", masked)
        self.assertNotIn("110112345678", masked)
        self.assertIn("[СНИЛС]", masked)
        self.assertIn("[ПАСПОРТ]", masked)
        self.assertIn("[СЧЁТ]", masked)
        self.assertIn("[ИНН]", masked)

    def test_is_safe_archive_member_blocks_zipslip_and_zipbomb(self):
        self.assertTrue(is_safe_archive_member("docs/claim.pdf", 1024, 512))
        self.assertFalse(is_safe_archive_member("../evil.txt", 100, 50))
        self.assertFalse(is_safe_archive_member("/etc/passwd", 100, 50))
        self.assertFalse(is_safe_archive_member("C:\\Windows\\win.ini", 100, 50))
        self.assertFalse(is_safe_archive_member("bomb.txt", 50 * 1024 * 1024, 1024))
        self.assertFalse(is_safe_archive_member("bomb2.txt", 5 * 1024 * 1024, 100))


class KomiCourtsAndHeadlessTests(unittest.TestCase):
    def test_komi_60_sections_and_17_districts(self):
        self.assertEqual(komi_section_count(), 60)
        self.assertEqual(len(KOMI_SECTION_NAMES), 60)
        self.assertEqual(len(KOMI_DISTRICT_NAMES), 17)
        self.assertEqual(
            komi_district_of("Куратовский судебный участок г. Сыктывкара"),
            "Сыктывкарский судебный район",
        )
        self.assertEqual(
            komi_match_section("  димитровский судебный участок г. сыктывкара, Республики Коми "),
            "Димитровский судебный участок г. Сыктывкара",
        )
        self.assertEqual(
            section_genitive("Димитровский судебный участок г. Сыктывкара"),
            "Димитровского судебного участка г. Сыктывкара",
        )

    def test_cli_list_sections_works_without_gui(self):
        rc = ac.run_cli(["--list-sections"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
