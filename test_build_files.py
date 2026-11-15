# -*- coding: utf-8 -*-
"""Проверки файлов сборки: build_exe.py, Собрать_EXE.bat, Конвертер_АМИРС.spec.
Запуск: python -m unittest test_build_files -v
(Реальная сборка и запуск .bat требуют Windows; здесь проверяются логика и статические правила.)"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import build_exe as be

HERE = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(HERE, "Собрать_EXE.bat")
SPEC = os.path.join(HERE, "Конвертер_АМИРС.spec")


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


class BatFile(unittest.TestCase):
    def test_errorlevel_never_percent_expanded_inside_blocks(self):
        """Классическая ловушка cmd: %errorlevel% внутри ( ... ) раскрывается до выполнения команд."""
        depth = 0
        for n, line in enumerate(_read(BAT).splitlines(), 1):
            code = line.strip()
            if code.lower().startswith("rem "):
                continue
            if depth > 0:
                self.assertNotIn("%errorlevel%", code.lower(), f"строка {n}: {line}")
            depth += code.count("(") - code.count(")")
            self.assertGreaterEqual(depth, 0, f"строка {n}: лишняя ')'")
        self.assertEqual(depth, 0, "несбалансированные скобки")

    def test_exit_code_is_propagated_and_all_gotos_have_labels(self):
        text = _read(BAT)
        self.assertRegex(text, r"(?im)^:DONE\s*\r?\n\s*exit /b %errorlevel%")
        labels = set(re.findall(r"(?m)^:(\w+)", text))
        for target in re.findall(r"(?i)goto\s+(\w+)", text):
            self.assertIn(target, labels)

    def test_ascii_only_and_crlf(self):
        with open(BAT, "rb") as fh:
            raw = fh.read()
        self.assertTrue(all(b < 128 for b in raw), "в .bat остался не-ASCII текст")
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_passes_arguments_to_builder(self):
        self.assertGreaterEqual(_read(BAT).count("build_exe.py %*"), 3)


class SpecFile(unittest.TestCase):
    def test_spec_compiles_and_has_safe_defaults(self):
        text = _read(SPEC)
        compile(text, SPEC, "exec")  # синтаксис
        self.assertNotIn("upx=True", text)
        self.assertEqual(text.count("upx=False"), 2)
        self.assertIn("AMIRS_CONSOLE", text)  # отладочная сборка с консолью
        self.assertNotRegex(text, r"except Exception:\s*\n\s*pass")  # пропуск пакета не должен быть немым


class Builder(unittest.TestCase):
    def test_version_is_read_from_app_not_hardcoded(self):
        self.assertNotRegex(_read(os.path.join(HERE, "build_exe.py")), r"v3\.7")
        app = os.path.join(HERE, "amirs_converter.py")
        if os.path.isfile(app):
            self.assertRegex(be.read_app_version(app), r"^\d+\.\d+\.\d+$")
        self.assertEqual(be.read_app_version("/нет/такого/файла.py"), "?")

    def test_powershell_quote_escapes_apostrophe(self):
        self.assertEqual(be.ps_quote("C:\\Users\\O'Brien\\a.lnk"), "C:\\Users\\O''Brien\\a.lnk")

    def test_requirements_parser_ignores_comments(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "r.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# комментарий\n\npaddleocr==2.7.3\n pypdf==5.9.0 \n# pillow==1\n")
        self.assertEqual(be.read_requirements(p), ["paddleocr==2.7.3", "pypdf==5.9.0"])
        self.assertEqual(be.read_requirements(os.path.join(d, "нет.txt")), [])
        shipped = os.path.join(HERE, "requirements-build.txt")
        if os.path.isfile(shipped):
            self.assertEqual(be.read_requirements(shipped), [])  # шаблон поставляется без пинов

    def test_user_files_survive_rebuild(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "amirs_settings.json"), "w", encoding="utf-8") as fh:
            fh.write('{"judge": "Мой судья"}')
        with open(os.path.join(d, "amirs_fees.json"), "wb") as fh:
            fh.write(b'{"x": 1}')
        kept = be.preserve_user_files(d)
        self.assertEqual(sorted(kept), ["amirs_fees.json", "amirs_settings.json"])
        shutil.rmtree(d)
        os.makedirs(d)
        with open(os.path.join(d, "amirs_fees.json"), "wb") as fh:
            fh.write(b'{"shipped": true}')
        be.restore_user_files(d, kept)
        with open(os.path.join(d, "amirs_fees.json"), "rb") as fh:
            self.assertEqual(fh.read(), b'{"x": 1}')
        self.assertIn("Мой судья", _read(os.path.join(d, "amirs_settings.json")))

    def test_settings_with_judge_are_not_shipped(self):
        self.assertNotIn("amirs_settings.json", be.SHIPPED_FILES)

    def test_pause_does_not_crash_without_stdin(self):
        old = sys.stdin
        try:
            sys.stdin = None
            be.pause_if_interactive()
            be.pause_if_interactive(True)
        finally:
            sys.stdin = old

    def test_dry_run_in_clean_folder_runs_tests_gate(self):
        """Копирует комплект во временную папку и запускает сборщик в режиме --dry-run."""
        need = ("build_exe.py", "amirs_converter.py", "test_amirs_regression.py", "test_amirs_converter.py")
        if not all(os.path.isfile(os.path.join(HERE, f)) for f in need):
            self.skipTest("рядом нет amirs_converter.py и тестов")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for f in need:
            shutil.copy2(os.path.join(HERE, f), d)
        env = dict(os.environ, AMIRS_NO_PAUSE="1")
        if env.get("PYTHONPATH"):  # относительные пути (если заданы) делаем абсолютными
            env["PYTHONPATH"] = os.pathsep.join(os.path.abspath(os.path.join(HERE, p))
                                                for p in env["PYTHONPATH"].split(os.pathsep))
        res = subprocess.run([sys.executable, "-X", "utf8", "build_exe.py", "--dry-run", "--no-pause"],
                             cwd=d, env=env, capture_output=True, text=True, encoding="utf-8", timeout=900)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("Автотесты пройдены", res.stdout)
        self.assertIn("DRY-RUN", res.stdout)

    def test_failed_tests_block_the_build(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copy2(os.path.join(HERE, "build_exe.py"), d)
        with open(os.path.join(d, "amirs_converter.py"), "w", encoding="utf-8") as fh:
            fh.write('APP_VERSION = "9.9.9"\n')
        with open(os.path.join(d, "test_amirs_converter.py"), "w", encoding="utf-8") as fh:
            fh.write("import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(False)\n")
        res = subprocess.run([sys.executable, "-X", "utf8", "build_exe.py", "--dry-run", "--no-pause"],
                             cwd=d, capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(res.returncode, 1)
        self.assertIn("сборка остановлена", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
