# -*- coding: utf-8 -*-
"""Регрессионные тесты по итогам аудита (версии 3.8.1 и 3.8.2).

Запуск: python -m unittest test_amirs_regression -v (или: pytest test_amirs_regression.py)
Каждый тест соответствует дефекту из перечня аудита (номер указан в имени класса/теста).
"""
import contextlib
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile

import amirs_converter as ac
from test_amirs_converter import _build_test_xlsx, _read_docx_text

_TMP_ROOT = None


def setUpModule():
    """Изолируем файлы программы (журналы, настройки, шкала пошлины) во временной папке."""
    global _TMP_ROOT
    _TMP_ROOT = tempfile.mkdtemp(prefix="amirs_regr_")
    ac._app_data_dir = lambda: _TMP_ROOT  # noqa: E731
    if hasattr(ac, "_FEES_CACHE"):
        ac._FEES_CACHE.clear()


def tearDownModule():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


def _tmpdir(testcase):
    d = tempfile.mkdtemp(prefix="amirs_t_")
    testcase.addCleanup(shutil.rmtree, d, True)
    return d


def _w(dirpath, name, data):
    p = os.path.join(dirpath, name)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(p, mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as fh:
        fh.write(data)
    return p


HEADERS = ac.REQUIRED_COLUMNS + ac.OPTIONAL_COLUMNS + ac.SCREENING_COLUMNS
BASE_ROW = {
    "Взыскатель": "ООО Тепло", "Должник_ФИО": "Иванов Иван Иванович", "Дата_Рождения": "01.02.1980",
    "Место_Рождения": "г. Ухта", "Адрес_Регистрации": "г. Ухта, ул. Мира, 1", "Сумма_Долга": "12345.67",
    "СНИЛС": "112-233-445 95", "Категория_Требования": "оплата жилого помещения, коммунальных услуг, услуг связи",
    "Дата_Поступления": "10.11.2026", "Страна_Должника": "Россия", "Документы_Приложены": "да",
    "Заявление_Подписано": "да", "Доверенность_Есть": "да", "Признаки_Спора": "нет",
    "Оплачено_Пошлины": "2000",
}


def _row(**kw):
    d = dict(BASE_ROW)
    d.update(kw)
    return [d.get(h, "") for h in HEADERS]


# ============================== P0: целостность данных ==============================
class P0_DataIntegrity(unittest.TestCase):
    GOOD = dict(Взыскатель="ООО Тепло", Должник="Иванов Иван Иванович", Дата_рождения="01.02.1980",
                Место_рождения="Ухта", Адрес="Ухта, Мира 1", СНИЛС="112-233-445 95",
                Сумма_долга="1000", Пени="0")

    def _claim(self, **kw):
        d = dict(self.GOOD, Вердикт=ac.V_OK)
        d.update(kw)
        return d

    def test_d1_parallel_appends_not_lost(self):
        """№1: 8 потоков × 10 записей — ни одна не теряется, ID уникальны."""
        path = os.path.join(_tmpdir(self), "claims.csv")

        def worker(i):
            for _ in range(10):
                ac.append_claim_rows([self._claim(Должник=f"Т{i}")], path)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        rows = ac.read_claims_registry(path)
        self.assertEqual(len(rows), 80)
        self.assertEqual(len({r["ID"] for r in rows}), 80)

    def test_d2_purge_does_not_touch_unsent_namesake(self):
        """№2: после отправки одной заявки данные неотправленного однофамильца целы."""
        d = _tmpdir(self)
        path = os.path.join(d, "claims.csv")
        ids = ac.append_claim_rows([
            self._claim(),
            self._claim(Дата_рождения="05.05.1965", Адрес="", Сумма_долга="7777"),  # неполная: не уйдёт
        ], path)
        res = ac.send_claims_to_amirs("11MS001", os.path.join(d, "pk.xml"), path=path)
        self.assertEqual(res.sent_ids, [ids[0]])
        rows = {r["ID"]: r for r in ac.read_claims_registry(path)}
        self.assertEqual(rows[ids[1]]["Должник"], "Иванов Иван Иванович")
        self.assertEqual(rows[ids[1]]["Дата_рождения"], "05.05.1965")
        self.assertNotIn("удалены", rows[ids[1]]["Должник"].lower())

    def test_d3_negative_amount_rejected(self):
        """№3: отрицательная сумма не превращается в положительную."""
        self.assertIsNone(ac.parse_amount("-5"))
        self.assertIsNone(ac.parse_amount("-100"))
        d = _tmpdir(self)
        p = _build_test_xlsx(os.path.join(d, "r.xlsx"), HEADERS,
                             [_row(), _row(Должник_ФИО="Минусов Михаил Михайлович", Сумма_Долга="-100")])
        r = ac.process_registry(p, output_dir=d)
        self.assertEqual(r.success_count, 1)
        self.assertEqual(len(r.rejected), 1)
        only_neg = _build_test_xlsx(os.path.join(d, "n.xlsx"), HEADERS, [_row(Сумма_Долга="-100")])
        with self.assertRaises(ac.ConversionError):  # все строки забракованы — пакета нет
            ac.process_registry(only_neg, output_dir=d)

    def test_d4_amount_formats(self):
        """№4: форматы сумм (в т.ч. американский) разбираются верно, мусор отклоняется."""
        cases = {"1 234,56": "1234.56", "1,234.56": "1234.56", "12,345.67": "12345.67",
                 "1.234,56": "1234.56", "1,234,567.89": "1234567.89", "1 234,56 руб.": "1234.56",
                 "12 345,67 ₽": "12345.67", "1234.5": "1234.50"}
        for raw, want in cases.items():
            self.assertEqual(str(ac.parse_amount(raw)), want, raw)
        for bad in ("abc", "", "1..2", "1,,2", "5-"):
            self.assertIsNone(ac.parse_amount(bad), bad)

    def test_d4_claim_id_not_reused(self):
        path = os.path.join(_tmpdir(self), "claims.csv")
        ids = ac.append_claim_rows([self._claim(), self._claim(Должник="Петров Пётр")], path)
        ac.remove_claim_rows([ids[-1]], path)
        new_id = ac.append_claim_rows([self._claim(Должник="Сидоров Сидор")], path)[0]
        self.assertNotIn(new_id, ids)

    def test_d4_fee_scale_above_one_million(self):
        ac._FEES_CACHE.clear()
        self.assertEqual(ac.state_duty_order("500000"), "7500.00")
        self.assertEqual(ac.state_duty_order("1000000"), "12500.00")
        self.assertEqual(ac.state_duty_order("1500000"), "15000.00")
        self.assertEqual(ac.state_duty_order("3000000"), "22500.00")

    def test_d4_amount_plausibility_cap(self):
        d = _tmpdir(self)
        p = _build_test_xlsx(os.path.join(d, "r.xlsx"), HEADERS,
                             [_row(), _row(Должник_ФИО="Огромов Олег Олегович", Сумма_Долга="999999999999999")])
        r = ac.process_registry(p, output_dir=d)
        self.assertEqual(r.success_count, 1)
        self.assertEqual(len(r.rejected), 1)


# ============================== P1: проекты приказов ==============================
class P1_OrderPackage(unittest.TestCase):
    def _build(self):
        d = _tmpdir(self)
        rows = [
            _row(Должник_ФИО="Иванов Иван Иванович"),  # норма
            _row(Должник_ФИО="Большов Борис Борисович", Сумма_Долга="600000", Оплачено_Пошлины="9500"),
            _row(Должник_ФИО="Неподписанов Николай Николаевич", Заявление_Подписано="нет"),
            _row(Должник_ФИО="Недоплатов Пётр Петрович", Оплачено_Пошлины="500"),  # пошлина уплачена не полностью
            _row(Должник_ФИО="Неуказанов Николай Николаевич", Оплачено_Пошлины=""),  # сумма не указана, льготы нет
            _row(Должник_ФИО="Алиментов Алексей Алексеевич",
                 Категория_Требования="алименты на несовершеннолетних детей", Оплачено_Пошлины="",
                 Льгота_Пошлина="да"),  # освобождён
        ]
        p = _build_test_xlsx(os.path.join(d, "r.xlsx"), HEADERS, rows)
        st = ac.DocSettings()
        st.section = "Димитровский судебный участок г. Сыктывкара"
        st.judge = "Тестов Т.Т."
        res = ac.generate_document_package(p, st, output_dir=d)
        order_path = [f for f in res.files if "01_" in os.path.basename(f)][0]
        return res, _read_docx_text(order_path)

    def test_d5_refused_and_returned_rows_not_in_orders(self):
        res, text = self._build()
        self.assertIn("Иванова Ивана Ивановича", text)  # склонение
        self.assertIn("Алиментова Алексея Алексеевича", text)
        for name in ("Большова", "Неподписанова", "Недоплатова"):
            self.assertNotIn(name, text)
        self.assertEqual(res.debtors, 3)  # норма, «сумма не указана», освобождённый
        self.assertEqual(len(res.excluded_orders), 3)  # 600 тыс., без подписи, недоплата
        self.assertIn("Неуказанова Николая Николаевича", text)
        self.assertIn("ТРЕБУЕТСЯ ПРОВЕРКА ПО СТ. 125", text)  # неуказанная пошлина — пометка судье

    def test_d6_budget_duty_only_for_exempt_claimant(self):
        """№6: «в доход бюджета» — только у освобождённого заявителя, не у неплательщика."""
        _res, text = self._build()
        self.assertEqual(text.count("в доход местного бюджета"), 1)
        pos = text.index("в доход местного бюджета")
        self.assertIn("Алиментова", text[max(0, pos - 200):pos])
        self.assertNotIn("доход местного бюджета государственную пошлину, от уплаты которой взыскатель освобождён, "
                         "в размере", text[:text.index("Алиментова Алексея Алексеевича,")])


# ============================== P1: ZIP и вложения ==============================
class P1_ZipSafety(unittest.TestCase):
    TXT = "Расчёт задолженности по лицевому счёту 201601234567 за период 01.01.2025 по 31.03.2025"

    def test_d8_member_over_limit_skipped_not_extracted(self):
        d = _tmpdir(self)
        p = os.path.join(d, "bomb.zip")
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("big.txt", "0" * (ac._ZIP_MAX_MEMBER_BYTES + 1024 * 1024))
            z.writestr("ok.txt", self.TXT)
        t0 = time.time()
        text, note, ch = ac.extract_document_text(p)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(ch, "text")
        self.assertIn("расчёт", text.lower())
        self.assertIn("слишком велик", note)
        self.assertLess(len(text), 10_000)

    def test_d8_nested_archive_not_opened(self):
        d = _tmpdir(self)
        inner = os.path.join(d, "inner.zip")
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("a.txt", self.TXT)
        outer = os.path.join(d, "outer.zip")
        with zipfile.ZipFile(outer, "w") as z:
            z.write(inner, "inner.zip")
        text, note, ch = ac.extract_document_text(outer)
        self.assertEqual(text, "")
        self.assertIn("вложенный архив", note)

    def test_d8_member_count_capped(self):
        d = _tmpdir(self)
        p = os.path.join(d, "many.zip")
        with zipfile.ZipFile(p, "w") as z:
            for i in range(ac._ZIP_MAX_MEMBERS + 15):
                z.writestr(f"f{i:02d}.txt", f"Договор № {i} " + self.TXT)
        text, note, ch = ac.extract_document_text(p)
        self.assertEqual(text.count("Договор №"), ac._ZIP_MAX_MEMBERS)
        self.assertIn("первые", note)

    def test_d8_path_traversal_names_never_written_outside(self):
        d = _tmpdir(self)
        p = os.path.join(d, "slip.zip")
        marker = os.path.join(os.path.dirname(d), "evil_marker_amirs.txt")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("../evil_marker_amirs.txt", "Договор расчёт")
            z.writestr("/abs_evil_marker_amirs.txt", "Договор")
        text, _note, _ch = ac.extract_document_text(p)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.exists("/abs_evil_marker_amirs.txt"))
        self.assertIn("договор", text.lower())

    def test_corrupt_zip_is_fail_not_crash(self):
        d = _tmpdir(self)
        p = _w(d, "bad.zip", b"PK\x03\x04garbage")
        self.assertEqual(ac.extract_document_text(p)[2], "fail")


# ============================== P2: классификация и файлы подписи ==============================
class P2_Classification(unittest.TestCase):
    def test_d10_signature_files_are_not_claims(self):
        d = _tmpdir(self)
        junk = _w(d, "junk.sig", os.urandom(300)[1:])  # не ASN.1
        empty = _w(d, "empty.cer", b"")
        der = _w(d, "real.sig", b"\x30\x82\x01\x00" + os.urandom(300))
        pem = _w(d, "cert.crt", "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
        for p in (junk, empty):
            info = ac.analyze_attachment_file(p)
            self.assertEqual(info.status, ac.DOC_STATUS_FAIL, p)
        for p in (der, pem):
            info = ac.analyze_attachment_file(p)
            self.assertEqual(info.kind, ac.DOC_KIND_EDS)
            self.assertNotEqual(info.kind, ac.DOC_KIND_ZAYAVLENIE)
        res = ac.check_claim_documents(d, [[ac.DOC_KIND_RASCHET]])
        self.assertNotIn(ac.DOC_KIND_ZAYAVLENIE, res.found_kinds)
        self.assertIn("само заявление среди файлов не распознано", res.note)

    def test_d11_egrn_only_by_explicit_wording(self):
        k = lambda t: ac.classify_document_text(t)[0]  # noqa: E731
        self.assertEqual(k("Выписка из ЕГРН об объекте недвижимости. Кадастровый номер 11:05:0101001:1"),
                         ac.DOC_KIND_EGRN)
        self.assertEqual(k("Выписка из Единого государственного реестра недвижимости о правах"),
                         ac.DOC_KIND_EGRN)
        self.assertNotEqual(k("Квитанция за ЖКУ. Кадастровый номер 11:05:0101001:1"), ac.DOC_KIND_EGRN)
        self.assertNotEqual(k("Карточка лицевого счёта № 123"), ac.DOC_KIND_EGRN)
        self.assertNotIn(ac.DOC_KIND_EGRN, ac.classify_all_document_kinds("карточка лицевого счёта, кадастровый номер"))

    def test_egrn_alternative_to_contract_in_profile(self):
        d = _tmpdir(self)
        _w(d, "1_egrn.txt", "Выписка из ЕГРН об объекте недвижимости")
        _w(d, "2_raschet.txt", "Расчёт задолженности по оплате за период")
        _w(d, "3_pochta.txt", "Список почтовых отправлений. Уведомление о вручении")
        groups = ac.DEFAULT_DOC_PROFILES["оплата жилого помещения, коммунальных услуг, услуг связи"]
        res = ac.check_claim_documents(d, groups)
        self.assertEqual(res.missing, [])

    def test_rpo_pattern_matches_cyrillic(self):
        self.assertEqual(ac.classify_document_text("Отправление РПО 16700012345678 принято")[0], ac.DOC_KIND_POST)

    def test_single_image_ocr_no_name_error(self):
        text, note = ac._run_paddle_ocr_single("nonexistent.jpg", "")
        self.assertEqual(text, "")
        self.assertIn("PaddleOCR", note)


# ============================== Новые читалки вложений ==============================
class Readers(unittest.TestCase):
    CYR = "Расчёт задолженности по лицевому счёту 201601234567 за период с 01.01.2025 по 31.03.2025 составляет 12 345,67 руб."

    def _ex(self, name, data):
        d = _tmpdir(self)
        return ac.extract_document_text(_w(d, name, data))

    def test_text_formats(self):
        self.assertIn("расчёт", self._ex("a.txt", self.CYR)[0].lower())
        self.assertIn("расчёт", self._ex("b.txt", self.CYR.encode("cp1251"))[0].lower())
        t = self._ex("a.html", "<html><script>var x=1;</script><body><p>" + self.CYR + "</p></body></html>")[0]
        self.assertIn("расчёт", t.lower())
        self.assertNotIn("var x", t)
        self.assertIn("расчёт", self._ex("a.xml", "<r><a>" + self.CYR + "</a></r>")[0].lower())
        self.assertEqual(self._ex("e.txt", "")[2], "scan")

    def test_csv_xlsx_ods(self):
        self.assertIn("1000,50", self._ex("a.csv", "Период;Начислено\n01.2025;1000,50\n")[0])
        d = _tmpdir(self)
        p = _build_test_xlsx(os.path.join(d, "a.xlsx"), ["Период", "Начислено"], [["01.2025", "1000.5"]])
        self.assertIn("1000.5", ac.extract_document_text(p)[0])
        ods = os.path.join(d, "a.ods")
        with zipfile.ZipFile(ods, "w") as z:
            z.writestr("content.xml", '<?xml version="1.0"?><o xmlns:text="t"><text:p>Начислено 1000</text:p></o>')
        self.assertIn("Начислено 1000", ac.extract_document_text(ods)[0])

    def test_rtf_both_encodings(self):
        body = self.CYR.encode("cp1251")
        rtf = b"{\\rtf1\\ansi\\ansicpg1251 " + b"".join(
            b"\\'%02x" % c if c > 127 else bytes([c]) for c in body) + b"}"
        self.assertIn("задолженности", self._ex("a.rtf", rtf)[0])
        u = "{\\rtf1\\ansi " + "".join(
            "\\u%d?" % (ord(c) if ord(c) < 32768 else ord(c) - 65536) if ord(c) > 127 else c for c in self.CYR) + "}"
        self.assertIn("задолженности", self._ex("u.rtf", u.encode("ascii"))[0])

    def test_d13_doc_binary_text_vs_garbage(self):
        good = os.urandom(300) + ("Договор управления многоквартирным домом. " + self.CYR).encode("utf-16-le") + os.urandom(300)
        text, _n, ch = self._ex("a.doc", good)
        self.assertEqual(ch, "text")
        self.assertIn("договор", text.lower())
        for _ in range(5):  # случайные байты — не «текст»
            self.assertEqual(self._ex("r.doc", os.urandom(6000))[2], "scan")

    def test_unsupported_and_hostile(self):
        self.assertEqual(self._ex("a.xyz", b"abc")[2], "fail")
        t0 = time.time()
        self._ex("big.rtf", b"{\\rtf1 " + b"{" * 2_000_000 + b"}")
        self.assertLess(time.time() - t0, 20)


# ============================== Экспресс-разбор заявления ==============================
class ClaimTextParser(unittest.TestCase):
    TXT = ("Заявление о вынесении судебного приказа. Взыскатель: ООО «Тепло-Сервис» Адрес: г. Ухта, ул. Мира, 1 ИНН 1101234567\n"
           "Должник: Иванов Иван Иванович\nДата рождения: 01.02.1980 Место рождения: г. Ухта "
           "Адрес регистрации: г. Ухта, ул. Мира, д. 5, кв. 7 СНИЛС 112-233-445 95\n"
           "Лицевой счёт № 201601234567. Прошу взыскать задолженность по оплате жилого помещения и коммунальных услуг "
           "за период с 01.01.2025 по 31.03.2025 в размере 12 345,67 руб. основного долга, пени в размере 345,10 руб., "
           "госпошлину в размере 2 000 руб.")

    def test_d12_fields(self):
        f = ac.parse_claim_text_fields(self.TXT)
        self.assertEqual(f["ФИО должника"], "Иванов Иван Иванович")  # без «\nДата»
        self.assertIn("д. 5, кв. 7", f["Адрес должника"])  # адрес должника, не взыскателя
        self.assertNotIn("Мира, 1", f["Адрес должника"].replace("д. 5", ""))
        self.assertEqual(f["Период задолженности"], "с 01.01.2025 по 31.03.2025")
        self.assertEqual(str(f["Сумма долга, руб."]), "12345.67")
        self.assertEqual(str(f["Пени, руб."]), "345.10")
        self.assertEqual(f["Лицевой счёт"], "201601234567")
        self.assertEqual(f["Дата рождения (ДД.ММ.ГГГГ)"], "01.02.1980")

    def test_debt_amount_either_word_order(self):
        a = ac.parse_claim_text_fields("основного долга в размере 12 345,67 руб.")
        b = ac.parse_claim_text_fields("в размере 12 345,67 руб. основного долга")
        self.assertEqual(str(a["Сумма долга, руб."]), "12345.67")
        self.assertEqual(str(b["Сумма долга, руб."]), "12345.67")

    def test_claimant_address_not_used_when_debtor_has_none(self):
        f = ac.parse_claim_text_fields("Взыскатель: ООО А Адрес: г. Ухта, ул. Мира 1\nДолжник: Петров Пётр Петрович")
        self.assertNotIn("Адрес должника", f)

    def test_empty_and_hostile_input(self):
        self.assertEqual(ac.parse_claim_text_fields(""), {})
        t0 = time.time()
        ac.parse_claim_text_fields("Взыскатель: " + "а " * 100000 + "\nДолжник: " + "Иванов " * 50000)
        self.assertLess(time.time() - t0, 10)

    def test_cli_read_claim(self):
        d = _tmpdir(self)
        p = _w(d, "claim.txt", self.TXT)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ac.run_cli(["--read-claim", p])
        self.assertEqual(rc, 0)
        self.assertIn("ФИО должника: Иванов Иван Иванович", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(ac.run_cli(["--read-claim", os.path.join(d, "нет.txt")]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
