# -*- coding: utf-8 -*-
"""Сборщик портативной версии «Конвертер АМИРС» со встроенным PaddleOCR.

Что делает (6 шагов): проверка Python → PyInstaller → зависимости → портативные модели
PaddleOCR → сборка по .spec → портативная папка, ярлык и проверка запуска.

Параметры командной строки:
  --dry-run   только проверить окружение и тесты, ничего не устанавливать и не собирать
  --no-pause  не ждать нажатия Enter в конце (для автоматического запуска)
Переменные окружения:
  AMIRS_SKIP_TESTS=1    не запускать автотесты перед сборкой
  AMIRS_DEST_DIR=...    куда положить готовую портативную папку (по умолчанию — Рабочий стол)
  AMIRS_CONSOLE=1       отладочная сборка с окном консоли
  AMIRS_EMBED_MODELS=1  дополнительно вшить модели внутрь сборки (см. .spec)
  AMIRS_NO_PAUSE=1      то же, что --no-pause

Версии пакетов: если рядом лежит requirements-build.txt со строками «пакет==версия»,
ставятся именно они; фактические версии каждой сборки пишутся в build_versions.txt.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

APP_FILE = "amirs_converter.py"
APP_EXE_NAME = "Конвертер_АМИРС"
CORE_PACKAGES = ["pypdf", "pypdfium2", "Pillow"]
OCR_PACKAGES = ["paddlepaddle", "paddleocr"]
USER_FILES = ("amirs_settings.json", "amirs_docs.json", "amirs_fees.json")  # правятся пользователем
SHIPPED_FILES = ("amirs_docs.json", "amirs_fees.json", "Инструкция_PaddleOCR.txt", "Пример_пакета_АМИРС.xml")
TEST_MODULES = ("test_amirs_regression", "test_amirs_converter")


# ----------------------------------------------------------------- вспомогательное
def set_color(code: str) -> None:
    if os.name == "nt":
        os.system(f"color {code}")


def read_app_version(app_path: str) -> str:
    """Версия берётся из самой программы (APP_VERSION), а не пишется в сборщике вручную."""
    try:
        with open(app_path, encoding="utf-8") as fh:
            m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def ps_quote(value: str) -> str:
    """Экранирование строки для одинарных кавычек PowerShell (апостроф удваивается)."""
    return value.replace("'", "''")


def read_requirements(path: str):
    """Строки с пакетами из requirements-файла (без комментариев и пустых)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    except OSError:
        return []


def pause_if_interactive(force_no_pause: bool = False) -> None:
    if force_no_pause or os.environ.get("AMIRS_NO_PAUSE") == "1":
        return
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return
        input("Нажмите Enter для выхода...")
    except (EOFError, OSError):
        pass


def ensure_compatible_python() -> None:
    """Если скрипт запущен на Python 3.14+, перезапускает его через 3.12/3.11/3.10/3.13."""
    if sys.version_info < (3, 14) or os.environ.get("AMIRS_PY_SWITCHED") == "1":
        return
    if os.name == "nt" and shutil.which("py"):
        for ver in ("-3.12", "-3.11", "-3.10", "-3.13"):
            chk = subprocess.run(["py", ver, "-c", "import sys; print(sys.executable)"],
                                 capture_output=True, text=True)
            if chk.returncode == 0 and chk.stdout.strip():
                alt_py = chk.stdout.strip().splitlines()[-1]
                print(f"[АВТОВЫБОР] Python {sys.version.split()[0]} не поддерживает бинарные пакеты Paddle.")
                print(f"            Переключаюсь на совместимый Python ({ver}): {alt_py}\n")
                env = os.environ.copy()
                env["AMIRS_PY_SWITCHED"] = "1"
                res = subprocess.run([alt_py, "-X", "utf8", os.path.abspath(__file__)] + sys.argv[1:], env=env)
                sys.exit(res.returncode)


def run_tests_gate(py_exe: str, base_dir: str):
    """Автотесты перед сборкой: сломанная версия не должна превращаться в EXE.
    Возвращает (True/False/None, вывод); None — тестов рядом нет или они отключены."""
    if os.environ.get("AMIRS_SKIP_TESTS") == "1":
        return None, "пропущено (AMIRS_SKIP_TESTS=1)"
    mods = [m for m in TEST_MODULES if os.path.isfile(os.path.join(base_dir, m + ".py"))]
    if not mods:
        return None, "файлов тестов рядом нет"
    sandbox = tempfile.mkdtemp(prefix="amirs_build_tests_")  # тесты не должны трогать профиль пользователя
    env = os.environ.copy()
    env.update(LOCALAPPDATA=sandbox, APPDATA=sandbox, HOME=sandbox, USERPROFILE=sandbox)
    try:
        res = subprocess.run([py_exe, "-X", "utf8", "-m", "unittest", *mods], cwd=base_dir, env=env,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return False, "тесты не завершились за 15 минут"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    tail = "\n".join((res.stdout + res.stderr).strip().splitlines()[-6:])
    return res.returncode == 0, tail


def pip_install(py_exe: str, args) -> bool:
    return subprocess.run([py_exe, "-m", "pip", "install", "--disable-pip-version-check", *args]).returncode == 0


def record_versions(py_exe: str, base_dir: str) -> str:
    """Записывает фактические версии ключевых пакетов в build_versions.txt (для закрепления)."""
    wanted = {"pyinstaller", "paddlepaddle", "paddleocr", "pypdf", "pypdfium2", "pillow"}
    res = subprocess.run([py_exe, "-m", "pip", "freeze"], capture_output=True, text=True)
    lines = [ln for ln in res.stdout.splitlines() if ln.split("==")[0].strip().lower() in wanted]
    path = os.path.join(base_dir, "build_versions.txt")
    if lines:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# Python {sys.version.split()[0]}; перенесите нужные строки в requirements-build.txt\n")
            fh.write("\n".join(sorted(lines, key=str.lower)) + "\n")
    return path if lines else ""


def ensure_portable_paddle_models(py_exe: str, base_dir: str) -> str:
    """Готовит портативную папку paddle_models (det/rec/cls и кэш) для работы без интернета."""
    portable_models = os.path.join(base_dir, "paddle_models")
    os.makedirs(portable_models, exist_ok=True)
    prep_code = (
        "import os\n"
        "os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'\n"
        "from paddleocr import PaddleOCR\n"
        "ocr = PaddleOCR(lang='ru')\n"
        "home = os.path.expanduser('~')\n"
        "for sub in ('.paddleocr', '.paddlex'):\n"
        "    p = os.path.join(home, sub)\n"
        "    if os.path.isdir(p):\n"
        "        print('CACHE_DIR=' + p)\n"
    )
    try:
        res = subprocess.run([py_exe, "-c", prep_code], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        print(" [ВНИМАНИЕ] Загрузка моделей не завершилась за 30 минут.")
        return ""
    if res.returncode != 0:
        print(" [ВНИМАНИЕ] Модели PaddleOCR не подготовлены: "
              + (res.stderr.strip().splitlines() or ["причина неизвестна"])[-1])
        return portable_models if os.listdir(portable_models) else ""
    for line in res.stdout.splitlines():
        if line.startswith("CACHE_DIR="):
            src_cache = line.split("CACHE_DIR=", 1)[1].strip()
            dst_cache = os.path.join(portable_models, os.path.basename(src_cache))
            if os.path.isdir(src_cache) and not os.path.exists(dst_cache):
                try:
                    shutil.copytree(src_cache, dst_cache)
                except OSError as exc:
                    print(f" [ВНИМАНИЕ] Кэш {src_cache} не скопирован: {exc}")
    # Раскладываем det / rec / cls в корень. Обход — по ЗАРАНЕЕ собранному списку,
    # иначе os.walk заходит в только что созданные копии.
    for cur_root, _dirs, files in list(os.walk(portable_models)):
        if not any(f.endswith((".pdmodel", ".pdiparams", ".json", ".yml")) for f in files):
            continue
        low = cur_root.replace("\\", "/").lower()
        for role in ("det", "rec", "cls"):
            target = os.path.join(portable_models, role)
            if os.path.abspath(cur_root) == os.path.abspath(target) or os.path.exists(target):
                continue
            if f"/{role}/" in low or low.endswith(f"/{role}") or role in os.path.basename(cur_root).lower():
                try:
                    shutil.copytree(cur_root, target)
                except OSError as exc:
                    print(f" [ВНИМАНИЕ] Папка {cur_root} не скопирована как {role}: {exc}")
    if os.path.isdir(os.path.join(portable_models, "det")) and os.path.isdir(os.path.join(portable_models, "rec")):
        for r_dir, _d, f_list in os.walk(portable_models):
            for fn in f_list:
                if fn.endswith(".tar"):
                    try:
                        os.remove(os.path.join(r_dir, fn))
                    except OSError:
                        pass
    return portable_models if os.listdir(portable_models) else ""


def create_windows_shortcut(target_exe: str, shortcut_path: str, work_dir: str) -> bool:
    """Создаёт ярлык .lnk (Windows). Пути экранируются: апостроф в имени профиля не ломает команду."""
    if os.name != "nt":
        return False
    ps_cmd = ("$ws = New-Object -ComObject WScript.Shell; "
              f"$s = $ws.CreateShortcut('{ps_quote(shortcut_path)}'); "
              f"$s.TargetPath = '{ps_quote(target_exe)}'; "
              f"$s.WorkingDirectory = '{ps_quote(work_dir)}'; "
              "$s.Save()")
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0 and os.path.isfile(shortcut_path)
    except OSError:
        return False


def preserve_user_files(folder: str, names=USER_FILES) -> dict:
    """Перед пересборкой запоминает правленные пользователем файлы настроек и справочников."""
    kept = {}
    for name in names:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                kept[name] = fh.read()
    return kept


def restore_user_files(folder: str, kept: dict) -> None:
    for name, data in kept.items():
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)


def smoke_test_exe(exe_path: str) -> bool:
    """Запускает собранный EXE в режиме командной строки и проверяет код возврата."""
    env = os.environ.copy()
    env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    try:
        res = subprocess.run([exe_path, "--list-sections"], env=env, capture_output=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f" [ПРОВЕРКА] EXE не запустился: {exc}")
        return False
    if res.returncode != 0:
        print(f" [ПРОВЕРКА] EXE вернул код {res.returncode}")
    return res.returncode == 0


def find_desktop(base_dir: str) -> str:
    override = os.environ.get("AMIRS_DEST_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    for c in (os.path.join(user_profile, "Desktop"), os.path.join(user_profile, "OneDrive", "Desktop"),
              os.path.join(user_profile, "Рабочий стол"), base_dir):
        if os.path.isdir(c):
            return c
    return base_dir


# ----------------------------------------------------------------- основной сценарий
def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv
    ensure_compatible_python()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)
    version = read_app_version(os.path.join(base_dir, APP_FILE))
    if os.name == "nt":
        os.system(f"title Сборка Конвертера АМИРС v{version} (PaddleOCR)")
    set_color("1F")
    print("=" * 65)
    print(f" СБОРКА ПОРТАТИВНОЙ ВЕРСИИ КОНВЕРТЕРА АМИРС v{version}")
    print(" Распознавание сканов PDF, DOCX, JPG, PNG, TIFF, WEBP, BMP (PaddleOCR)")
    print("=" * 65 + "\n")

    if not os.path.isfile(os.path.join(base_dir, APP_FILE)):
        set_color("4F")
        print(f"[ОШИБКА] Рядом с этим файлом нет файла {APP_FILE}.")
        print("Распакуйте все файлы архива в одну папку и запустите сборку снова.")
        return 1
    py_exe = sys.executable
    print(f"[1/6] Python: {sys.version.split()[0]} ({py_exe}); версия программы: {version}\n")

    ok, tail = run_tests_gate(py_exe, base_dir)
    if ok is None:
        print(f" Автотесты: {tail}\n")
    elif ok:
        print(" Автотесты пройдены.\n")
    else:
        set_color("4F")
        print("[ОШИБКА] Автотесты не пройдены — сборка остановлена (чтобы не выпустить неисправный EXE).")
        print(tail)
        print("Исправьте причину или, осознанно, запустите с AMIRS_SKIP_TESTS=1.")
        return 1
    if dry_run:
        print("[DRY-RUN] Проверка окружения завершена; установка и сборка не выполнялись.")
        return 0

    print("[2/6] Проверяем PyInstaller...")
    reqs_file = os.path.join(base_dir, "requirements-build.txt")
    pinned = read_requirements(reqs_file)
    if subprocess.run([py_exe, "-m", "PyInstaller", "--version"], capture_output=True).returncode != 0:
        print(" Устанавливаю PyInstaller (нужен интернет)...")
        if not pip_install(py_exe, [x for x in pinned if x.lower().startswith("pyinstaller")]
                           or ["--upgrade", "pyinstaller"]):
            set_color("4F")
            print("[ОШИБКА] Не удалось установить PyInstaller.")
            return 1
    print(" PyInstaller готов.\n")

    print("[3/6] Устанавливаем PDF-модули, Pillow и PaddleOCR...")
    core_ok = True
    if pinned:
        print(f" Используются закреплённые версии из {os.path.basename(reqs_file)} ({len(pinned)} шт.).")
        core_ok = pip_install(py_exe, ["-r", reqs_file])
    else:
        print(" Версии не закреплены — будут установлены последние (см. build_versions.txt после сборки).")
    core_ok = core_ok and pip_install(py_exe, CORE_PACKAGES)  # уже установленные pip пропускает
    if core_ok and not pip_install(py_exe, OCR_PACKAGES):
        print(" [ВНИМАНИЕ] PaddleOCR не установлен: сканы будут получать статус «проверить визуально».")
    if not core_ok:
        set_color("4F")
        print("[ОШИБКА] Не установлены обязательные модули (pypdf, pypdfium2, Pillow).")
        return 1
    print(" Модули готовы.\n")

    print("[4/6] Портативные модели PaddleOCR (rus)...")
    portable_models = ensure_portable_paddle_models(py_exe, base_dir)
    if portable_models:
        print(f" Модели готовы: {portable_models}\n")
    else:
        print(" [ИНФО] Моделей нет: они загрузятся при первом распознавании (нужен интернет).\n")

    print("[5/6] Сборка по профилю Конвертер_АМИРС.spec...")
    spec_file = os.path.join(base_dir, f"{APP_EXE_NAME}.spec")
    if os.path.isfile(spec_file):
        cmd = [py_exe, "-m", "PyInstaller", "--noconfirm", "--clean", spec_file]
    else:
        cmd = [py_exe, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
               "--name", APP_EXE_NAME, "--hidden-import", "pypdf", "--hidden-import", "pypdfium2",
               "--hidden-import", "PIL", "--noupx"]
        for pkg in ("pypdfium2", "pypdfium2_raw", "paddleocr", "paddle", "pyclipper", "shapely",
                    "skimage", "imgaug", "lmdb"):
            if subprocess.run([py_exe, "-c", f"import {pkg}"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:
                cmd += ["--collect-all", pkg]
        cmd.append(APP_FILE)
    if subprocess.run(cmd).returncode != 0:
        set_color("4F")
        print("\n[ОШИБКА] Сборка не удалась.")
        return 1
    versions_path = record_versions(py_exe, base_dir)
    print("")

    print("[6/6] Портативная папка, ярлык, проверка запуска...")
    built_folder = os.path.join(base_dir, "dist", APP_EXE_NAME)
    built_exe = os.path.join(built_folder, APP_EXE_NAME + ".exe")
    if not os.path.isfile(built_exe):
        built_exe = os.path.join(base_dir, "dist", APP_EXE_NAME + ".exe")
        built_folder = ""
    if not os.path.isfile(built_exe):
        set_color("4F")
        print(f"[ОШИБКА] Не найден собранный {APP_EXE_NAME}.exe в папке dist.")
        return 1

    dest_dir = find_desktop(base_dir)
    bundle = os.path.join(dest_dir, APP_EXE_NAME + "_Portable")
    kept = preserve_user_files(bundle)  # правки пользователя переживают пересборку
    if os.path.exists(bundle):
        shutil.rmtree(bundle, ignore_errors=True)
    if built_folder and os.path.isdir(built_folder):
        shutil.copytree(built_folder, bundle)
    else:
        os.makedirs(bundle, exist_ok=True)
        shutil.copy2(built_exe, os.path.join(bundle, APP_EXE_NAME + ".exe"))
    for name in SHIPPED_FILES:  # справочники и инструкции; настройки (суд, судья) НЕ поставляются
        src = os.path.join(base_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(bundle, name))
    restore_user_files(bundle, kept)
    if kept:
        print(" Сохранены ваши файлы: " + ", ".join(sorted(kept)))
    if portable_models and os.path.isdir(portable_models):
        dst_models = os.path.join(bundle, "paddle_models")
        shutil.rmtree(dst_models, ignore_errors=True)
        shutil.copytree(portable_models, dst_models)

    portable_exe = os.path.join(bundle, APP_EXE_NAME + ".exe")
    shortcut = os.path.join(dest_dir, "Конвертер АМИРС.lnk")
    shortcut_ok = create_windows_shortcut(portable_exe, shortcut, bundle)
    smoke_ok = smoke_test_exe(portable_exe) if os.name == "nt" else None

    set_color("2F" if smoke_ok is not False else "6F")
    print("=" * 65)
    print(" ПОРТАТИВНАЯ ВЕРСИЯ ГОТОВА" if smoke_ok is not False
          else " СОБРАНО, НО ПРОВЕРКА ЗАПУСКА НЕ ПРОЙДЕНА — проверьте вручную")
    print("=" * 65 + "\n")
    if shortcut_ok:
        print(f"1) Ярлык: {shortcut}")
    print(f"2) Портативная папка (для ПК и флешки): {bundle}")
    print(f"3) Исполняемый файл: {portable_exe}")
    if versions_path:
        print(f"4) Версии пакетов этой сборки: {versions_path}")
    print("\nНастройки суда и судьи в сборку не входят — задайте их при первом запуске.")
    return 0 if smoke_ok is not False else 2


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:  # noqa: BLE001 — сборщик должен показать причину, а не закрыть окно
        set_color("4F")
        print(f"[КРИТИЧЕСКАЯ ОШИБКА] {type(exc).__name__}: {exc}")
        code = 1
    print("")
    pause_if_interactive("--no-pause" in sys.argv)
    sys.exit(code)
