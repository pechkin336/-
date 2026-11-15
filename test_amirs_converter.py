# -*- coding: utf-8 -*-
"""Модульные и интеграционные тесты для amirs_converter.py (версии 3.8.0–3.8.2)."""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

import amirs_converter as ac


def _build_test_xlsx(path: str, headers: list, data_rows: list) -> str:
    """Создаёт минимальную валидную книгу .xlsx без сторонних библиотек."""
    rows = [[(ac._S_HEAD, h) for h in headers]]
    for r in data_rows:
        rows.append([(ac._S_DATA, str(v) if v is not None else "") for v in r])
    sheet_xml = ac._tpl_worksheet(rows, col_widths=[22] * len(headers), freeze_rows=1)

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    ).encode("utf-8")
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    ).encode("utf-8")
    wb = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{ac.NS[1:-1]}" xmlns:r="{ac.NS_R[1:-1]}">'
        '<sheets><sheet name="Реестр" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    ).encode("utf-8")
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '</Relationships>'
    ).encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", ac._TPL_STYLES.encode("utf-8"))
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return path


def _read_docx_text(docx_path: str) -> str:
    """Извлекает весь текст из .docx."""
    with zipfile.ZipFile(docx_path, "r") as z:
        root = ET.fromstring(z.read("word/document.xml"))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for p in root.iter(ns + "p"):
        txt = "".join(t.text or "" for t in p.iter(ns + "t"))
        if txt:
            paragraphs.append(txt)
    return "\n".join(paragraphs)


class TestParseAmount(unittest.TestCase):
    """Тесты разбора денежных сумм в различных форматах и порогов правдоподобия."""

    def test_russian_formats(self):
        self.assertEqual(ac.parse_amount("1 234,56"), "1234.56")
        self.assertEqual(ac.parse_amount("1\u00A0234,56"), "1234.56")
        self.assertEqual(ac.parse_amount("1234,56"), "1234.56")
        self.assertEqual(ac.parse_amount("0,50"), "0.50")
        self.assertEqual(ac.parse_amount("1 234 567,89"), "1234567.89")

    def test_american_and_mixed_formats(self):
        self.assertEqual(ac.parse_amount("1,234.56"), "1234.56")
        self.assertEqual(ac.parse_amount("12,345.67"), "12345.67")
        self.assertEqual(ac.parse_amount("1,234,567.89"), "1234567.89")
        self.assertEqual(ac.parse_amount("1.234,56"), "1234.56")
        self.assertEqual(ac.parse_amount("1.234.567,89"), "1234567.89")

    def test_currency_suffixes_and_scientific(self):
        self.assertEqual(ac.parse_amount("1 234,56 руб"), "1234.56")
        self.assertEqual(ac.parse_amount("3 500,50 руб."), "3500.50")
        self.assertEqual(ac.parse_amount("12,345.67 р."), "12345.67")
        self.assertEqual(ac.parse_amount("500 ₽"), "500.00")
        self.assertEqual(ac.parse_amount("1.5E+3"), "1500.00")

    def test_ambiguous_three_decimals_warning(self):
        amt1, warn1 = ac.parse_amount_detailed("1,234")
        self.assertEqual(amt1, "1.23")
        self.assertIn("неоднозначный", warn1)

        amt2, warn2 = ac.parse_amount_detailed("1.234")
        self.assertEqual(amt2, "1.23")
        self.assertIn("неоднозначный", warn2)

        amt3, warn3 = ac.parse_amount_detailed("0.125")
        self.assertEqual(amt3, "0.13")
        self.assertEqual(warn3, "")

    def test_plausibility_thresholds_and_invalid(self):
        amt_large, warn_large = ac.parse_amount_detailed("15 000 000")
        self.assertEqual(amt_large, "15000000.00")
        self.assertIn("10 000 000", warn_large)

        amt_huge, warn_huge = ac.parse_amount_detailed("999 999 999 999 999")
        self.assertIsNone(amt_huge)
        self.assertIn("порог правдоподобия", warn_huge)

        self.assertIsNone(ac.parse_amount("-100.50"))
        self.assertIsNone(ac.parse_amount("—100"))
        self.assertIsNone(ac.parse_amount("да"))
        self.assertIsNone(ac.parse_amount(""))
        self.assertIsNone(ac.parse_amount(None))


class TestFioDeclension(unittest.TestCase):
    """Тесты склонения ФИО должника в родительный и дательный падежи."""

    def test_standard_male_and_female(self):
        self.assertEqual(ac.decline_fio("Иванов Иван Иванович", "gent"),
                         "Иванова Ивана Ивановича")
        self.assertEqual(ac.decline_fio("Иванов Иван Иванович", "datv"),
                         "Иванову Ивану Ивановичу")

        self.assertEqual(ac.decline_fio("Петрова Анна Сергеевна", "gent"),
                         "Петровой Анны Сергеевны")
        self.assertEqual(ac.decline_fio("Петрова Анна Сергеевна", "datv"),
                         "Петровой Анне Сергеевне")

    def test_special_names_and_surnames(self):
        self.assertEqual(ac.decline_fio("Смирнова Мария Ильинична", "gent"),
                         "Смирновой Марии Ильиничны")
        self.assertEqual(ac.decline_fio("Смирнова Мария Ильинична", "datv"),
                         "Смирновой Марии Ильиничне")

        self.assertEqual(ac.decline_fio("Ковалевский Дмитрий Олегович", "gent"),
                         "Ковалевского Дмитрия Олеговича")
        self.assertEqual(ac.decline_fio("Ковалевская Софья Васильевна", "gent"),
                         "Ковалевской Софьи Васильевны")

        self.assertEqual(ac.decline_fio("Шевченко Тарас Григорьевич", "gent"),
                         "Шевченко Тараса Григорьевича")
        self.assertEqual(ac.decline_fio("Черных Ольга Викторовна", "gent"),
                         "Черных Ольги Викторовны")
        self.assertEqual(ac.decline_fio("Мельник Елена Петровна", "gent"),
                         "Мельник Елены Петровны")
        self.assertEqual(ac.decline_fio("Мельник Андрей Петрович", "gent"),
                         "Мельника Андрея Петровича")

        self.assertEqual(ac.decline_fio("Сидоров Никита Павлович", "gent"),
                         "Сидорова Никиты Павловича")
        self.assertEqual(ac.decline_fio("Кузьмин Илья Фёдорович", "datv"),
                         "Кузьмину Илье Фёдоровичу")
        self.assertEqual(ac.decline_fio("Орлов Павел Львович", "gent"),
                         "Орлова Павла Львовича")

        self.assertEqual(ac.decline_fio("Салтыков-Щедрин Михаил Евграфович", "gent"),
                         "Салтыкова-Щедрина Михаила Евграфовича")
        self.assertEqual(ac.decline_fio("Иванов И.И.", "gent"),
                         "Иванова И.И.")
        self.assertEqual(ac.decline_fio("Борец Илья Петрович", "gent"),
                         "Борца Ильи Петровича")

    def test_acting_judge_preamble(self):
        st = ac.DocSettings(
            section="Димитровский судебный участок г. Сыктывкара",
            judge="Петров П.П.",
            is_acting=True,
            acting_section="Куратовский судебный участок г. Сыктывкара",
        )
        self.assertEqual(
            st.judge_intro_clause,
            "Мировой судья Куратовского судебного участка г. Сыктывкара Республики Коми Петров П.П., "
            "исполняющий обязанности мирового судьи Димитровского судебного участка г. Сыктывкара Республики Коми",
        )


class TestRegistryAndWordIntegration(unittest.TestCase):
    """Интеграционные тесты XML-конвертации, скрининга и генерации документов Word."""

    def test_xml_american_amount_and_soft_paid_duty(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = os.path.join(tmp, "reg.xlsx")
            headers = [
                "Взыскатель", "Должник_ФИО", "Дата_Рождения", "Место_Рождения",
                "Адрес_Регистрации", "Сумма_Долга", "СНИЛС", "Оплачено_Пошлины"
            ]
            rows = [
                ["ООО «ЖКХ»", "Иванов Иван Иванович", "01.01.1980", "г. Сыктывкар",
                 "г. Сыктывкар, ул. Ленина, д. 1", "1,234.56", "112-233-445 95", "да"],
                ["ООО «ЖКХ»", "Сидоров Пётр Петрович", "02.02.1985", "г. Ухта",
                 "г. Ухта, ул. Мира, д. 2", "999 999 999 999 999", "112-233-445 95", "500"],
            ]
            _build_test_xlsx(xlsx_path, headers, rows)

            res = ac.process_registry(xlsx_path, court_code="11MS001", output_dir=tmp, journal=False)
            self.assertEqual(res.success_count, 1)
            self.assertEqual(len(res.rejected), 1)
            self.assertIn("порог правдоподобия", res.rejected[0]["Причина"])

            tree = ET.parse(res.xml_path)
            debt_text = tree.find(".//PrincipalDebt").text
            duty_text = tree.find(".//StateDuty").text
            self.assertEqual(debt_text, "1234.56")
            self.assertEqual(duty_text, "0.00")

            self.assertTrue(os.path.isfile(res.warnings_log_path))
            with open(res.warnings_log_path, "r", encoding="utf-8-sig") as fh:
                warn_content = fh.read()
            self.assertIn("Оплачено_Пошлины", warn_content)

    def test_word_package_declension_and_screening_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = os.path.join(tmp, "orders.xlsx")
            headers = [
                "Взыскатель", "Должник_ФИО", "Дата_Рождения", "Место_Рождения",
                "Адрес_Регистрации", "Сумма_Долга", "СНИЛС",
                "Категория_Требования", "Документы_Приложены", "Заявление_Подписано",
                "Оплачено_Пошлины", "Признаки_Спора"
            ]
            rows = [
                ["ТСЖ «Север»", "Иванов Иван Иванович", "10.05.1982", "г. Сыктывкар",
                 "г. Сыктывкар, ул. Кирова, д. 10", "15000,00", "112-233-445 95",
                 "оплата жилого помещения, коммунальных услуг, услуг связи", "да", "да", "2000", "нет"],
                ["ООО «Кредит»", "Петров Пётр Петрович", "12.06.1975", "г. Воркута",
                 "г. Воркута, ул. Горняков, д. 5", "600000,00", "112-233-445 95",
                 "сделка в простой письменной форме", "да", "да", "8750", "нет"],
                ["ООО «Связь»", "Сидорова Анна Ивановна", "15.08.1990", "г. Инта",
                 "г. Инта, ул. Полярная, д. 3", "5000,00", "112-233-445 95",
                 "оплата жилого помещения, коммунальных услуг, услуг связи", "нет", "да", "2000", "нет"],
                ["УК «Уют»", "Кузнецова Мария Сергеевна", "20.11.1988", "г. Печора",
                 "г. Печора, ул. Речная, д. 7", "22000,00", "112-233-445 95",
                 "оплата жилого помещения, коммунальных услуг, услуг связи", "да", "да", "2000", "да"],
            ]
            _build_test_xlsx(xlsx_path, headers, rows)

            st = ac.DocSettings(section="Димитровский судебный участок г. Сыктывкара", judge="Смирнова Е.В.")
            pkg = ac.generate_document_package(xlsx_path, st, output_dir=tmp, journal=False)

            self.assertEqual(pkg.debtors, 2)
            self.assertEqual(len(pkg.excluded_orders), 2)
            self.assertEqual(len(pkg.order_warnings), 1)

            orders_docx = os.path.join(pkg.folder, "01_Проекты_судебных_приказов.docx")
            orders_text = _read_docx_text(orders_docx)

            self.assertIn("Взыскать с Иванова Ивана Ивановича", orders_text)
            self.assertIn("Взыскать с Кузнецовой Марии Сергеевны", orders_text)
            self.assertNotIn("Петрова Петра Петровича", orders_text)
            self.assertNotIn("Сидоровой Анны Ивановны", orders_text)
            self.assertIn("ВНИМАНИЕ СУДЬЕ — ТРЕБУЕТСЯ ПРОВЕРКА ПО СТ. 125 ГПК РФ", orders_text)

            env_docx = os.path.join(pkg.folder, "04_Конверты_ярлыки.docx")
            env_text = _read_docx_text(env_docx)
            self.assertIn("Кому: Иванову Ивану Ивановичу", env_text)
            self.assertIn("Кому: Кузнецовой Марии Сергеевне", env_text)

            readme_path = os.path.join(pkg.folder, "00_ПАМЯТКА.txt")
            with open(readme_path, "r", encoding="utf-8-sig") as fh:
                readme = fh.read()
            self.assertIn("ИСКЛЮЧЕНЫ ИЗ ПРОЕКТОВ ПРИКАЗОВ", readme)
            self.assertIn("Петров Пётр Петрович", readme)
            self.assertIn("Сидорова Анна Ивановна", readme)


class TestFeesAndMonotonicRegistry(unittest.TestCase):
    """Тесты шкалы госпошлины > 1 млн руб. и монотонного счётчика ID заявок."""

    def test_state_duty_above_1m(self):
        self.assertEqual(ac.state_duty_order("1500000"), "15000.00")
        self.assertEqual(ac.state_duty_order("1000000"), "12500.00")
        self.assertEqual(ac.state_duty_order("500000"), "7500.00")

    def test_monotonic_claim_ids_after_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = os.path.join(tmp, "claims.csv")
            ids1 = ac.append_claim_rows([
                {"Должник": "Первый"},
                {"Должник": "Второй"},
                {"Должник": "Третий"},
            ], path=reg_path)
            self.assertEqual(ids1, ["З-000001", "З-000002", "З-000003"])

            removed = ac.remove_claim_rows(["З-000003"], path=reg_path)
            self.assertEqual(removed, 1)

            ids2 = ac.append_claim_rows([{"Должник": "Четвёртый"}], path=reg_path)
            self.assertEqual(ids2, ["З-000004"])

    def test_screening_soft_handling_for_yes_in_paid_duty(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = os.path.join(tmp, "screen_yes.xlsx")
            headers = [
                "Взыскатель", "Должник_ФИО", "Дата_Рождения", "Место_Рождения",
                "Адрес_Регистрации", "Сумма_Долга", "СНИЛС",
                "Категория_Требования", "Документы_Приложены", "Заявление_Подписано",
                "Оплачено_Пошлины"
            ]
            rows = [
                ["ООО «УК»", "Иванов Иван Иванович", "01.01.1980", "г. Сыктывкар",
                 "г. Сыктывкар, ул. Мира, д. 1", "10000", "112-233-445 95",
                 "оплата жилого помещения, коммунальных услуг, услуг связи", "да", "да", "да"],
            ]
            _build_test_xlsx(xlsx_path, headers, rows)
            st = ac.DocSettings(section="Димитровский судебный участок г. Сыктывкара")
            scr = ac.generate_screening_package(xlsx_path, st, output_dir=tmp, journal=False)
            self.assertEqual(scr.return_count, 0)
            self.assertEqual(scr.manual_count, 1)
            manual_texts = [f.text for f in scr.entries[0].findings if f.kind == "manual"]
            self.assertTrue(any("укажите сумму в рублях" in t for t in manual_texts), manual_texts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
