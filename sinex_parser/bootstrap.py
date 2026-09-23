import ctypes
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"
UV_DIR = ROOT / ".uv"
REQUIREMENTS = ROOT / "requirements.txt"
MAIN = ROOT / "main.py"
PACKAGE_DIR = ROOT / "sinex_parser"
STAMP = VENV_DIR / ".deps_stamp"
PYTHON_VERSION = "3.12"
MINIMUM_PYTHON = (3, 12)
STAMP_SCHEMA = 1
MARKER = "SINEX_TRF_STUDIO_READY"
UV_VERSION = "0.12.17"
UV_SOURCES = (
    "https://releases.astral.sh/github/uv/releases/download/" + UV_VERSION + "/",
    "https://github.com/astral-sh/uv/releases/latest/download/",
)
UV_USER_AGENT = "SINEX-TRF-Studio"
MAX_WINDOWS_PATH = 240
EXPLAINED = 2
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
MISSING_LIBRARY = "cannot open shared object file"
LINUX_PACKAGES = (
    "libnss3 libnspr4 libasound2 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 "
    "libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 "
    "libxkbcommon-x11-0"
)


def ansi_codepage():
    return "cp" + str(ctypes.windll.kernel32.GetACP())


def path_is_supported():
    if os.name != "nt":
        return True
    try:
        str(ROOT).encode(ansi_codepage())
    except (UnicodeEncodeError, LookupError, AttributeError, OSError):
        return False
    return True


def path_is_short_enough():
    return os.name != "nt" or len(str(ROOT)) < MAX_WINDOWS_PATH


def display(command):
    return " ".join(shlex.quote(part) for part in command)


def uv_filename():
    return "uv.exe" if os.name == "nt" else "uv"


def uv_artifact_name():
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if os.name == "nt":
        return "uv-" + ("aarch64" if arm else "x86_64") + "-pc-windows-msvc.zip"
    if sys.platform == "darwin":
        return "uv-" + ("aarch64" if arm else "x86_64") + "-apple-darwin.tar.gz"
    return "uv-" + ("aarch64" if arm else "x86_64") + "-unknown-linux-gnu.tar.gz"


def venv_python(venv_dir=VENV_DIR):
    if os.name == "nt":
        return Path(venv_dir) / "Scripts" / "python.exe"
    return Path(venv_dir) / "bin" / "python"


def venv_pythonw(venv_dir=VENV_DIR):
    if os.name == "nt":
        return Path(venv_dir) / "Scripts" / "pythonw.exe"
    return Path(venv_dir) / "bin" / "python"


def run(command):
    print("+ " + display(command), flush=True)
    try:
        return subprocess.run(command).returncode
    except FileNotFoundError:
        print("Command not found: " + command[0])
        return 127


def interpreter_version(python):
    probe = "import sys; print('%d.%d' % sys.version_info[:2])"
    if not Path(python).is_file():
        return None
    try:
        result = subprocess.run(
            [str(python), "-c", probe],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def requirements_hash():
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def environment_ready():
    python = venv_python()
    if not python.is_file() or not STAMP.is_file():
        return False
    try:
        stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        stamp.get("schema") == STAMP_SCHEMA
        and stamp.get("python") == interpreter_version(python)
        and stamp.get("requirements") == requirements_hash()
    )


def find_uv():
    local = UV_DIR / uv_filename()
    if local.is_file():
        return [str(local)]
    on_path = shutil.which("uv")
    if on_path:
        return [on_path]
    python = venv_python()
    if python.is_file():
        try:
            probe = subprocess.run(
                [str(python), "-c", "import uv"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None
        if probe.returncode == 0:
            return [str(python), "-m", "uv"]
    return None


def download_file(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": UV_USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        with open(destination, "wb") as handle:
            shutil.copyfileobj(response, handle)


def bootstrap_uv():
    print("Setting up the installer. This runs once and stays in the project folder.")
    name = uv_artifact_name()
    workspace = Path(tempfile.mkdtemp(prefix="sinex-uv-"))
    archive = workspace / name
    try:
        return fetch_uv(name, archive)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def fetch_uv(name, archive):
    for source in UV_SOURCES:
        try:
            download_file(source + name, archive)
        except OSError as error:
            print("The download from " + source + " failed: " + str(error))
            continue
        break
    else:
        print("The installer could not be downloaded.")
        return None
    try:
        UV_DIR.mkdir(parents=True, exist_ok=True)
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(UV_DIR)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                try:
                    bundle.extractall(UV_DIR, filter="data")
                except TypeError:
                    bundle.extractall(UV_DIR)
        found = list(UV_DIR.rglob(uv_filename()))
        if not found:
            print("The installer archive did not contain the program.")
            return None
        source = found[0]
        if source.parent != UV_DIR:
            for item in source.parent.iterdir():
                shutil.move(str(item), str(UV_DIR / item.name))
            shutil.rmtree(source.parent, ignore_errors=True)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        print("The installer could not be unpacked: " + str(error))
        return None
    return find_uv()


def create_environment_commands(uv, reinstall):
    python = venv_python()
    healthy = interpreter_version(python) == PYTHON_VERSION
    recreate = reinstall or (VENV_DIR.exists() and not healthy)
    commands = []
    if recreate or not python.is_file():
        if uv:
            command = [
                *uv,
                "venv",
                "--python",
                PYTHON_VERSION,
                "--python-preference",
                "only-managed",
            ]
        else:
            command = [sys.executable, "-m", "venv"]
        if recreate:
            command.append("--clear")
        commands.append(command + [str(VENV_DIR)])
    if uv:
        commands.append(
            [*uv, "pip", "install", "-r", str(REQUIREMENTS), "--python", str(python)]
        )
    else:
        commands.append(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "uv",
            ]
        )
        commands.append(
            [
                str(python),
                "-m",
                "uv",
                "pip",
                "install",
                "-r",
                str(REQUIREMENTS),
                "--python",
                str(python),
            ]
        )
    return commands


def verify_install(python):
    probe = (
        "import sinex_parser, PyQt6.QtWidgets, PyQt6.QtWebEngineWidgets, "
        "numpy, pandas, matplotlib, seaborn, pyqtgraph, folium, openpyxl, plyer; "
        "print('SINEX TRF Studio', sinex_parser.__version__)"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", probe],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        print("The environment interpreter could not run: " + str(error))
        return False
    if result.returncode == 0:
        print(result.stdout.strip())
        return True
    print("The environment was built but the application cannot be imported.")
    print("The last lines of the error were:")
    for line in result.stderr.strip().splitlines()[-8:]:
        print("  " + line)
    if os.name != "nt" and MISSING_LIBRARY in result.stderr:
        print()
        print("A system library is missing. On Debian and Ubuntu, install it with:")
        print("  sudo apt install " + LINUX_PACKAGES)
        print("Other distributions carry the same libraries under their own names.")
    return False


def install_environment(uv, reinstall):
    for command in create_environment_commands(uv, reinstall):
        if run(command) != 0:
            print()
            print("The step above failed. Check the internet connection, then run this script again.")
            return False
    if not verify_install(venv_python()):
        return False
    stamp = {
        "schema": STAMP_SCHEMA,
        "python": interpreter_version(venv_python()),
        "requirements": requirements_hash(),
    }
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print("The environment is ready.")
    return True


def check_report():
    uv = find_uv()
    python = venv_python()
    print("python: " + sys.version.split()[0] + " (" + sys.executable + ")")
    if python.is_file():
        print("venv python: " + str(interpreter_version(python)) + " (" + str(python) + ")")
    else:
        print("venv python: missing (" + str(python) + ")")
    print("environment: " + ("ready" if environment_ready() else "not ready"))
    print("uv: " + (" ".join(uv) if uv else "not found"))
    return 0


def print_help():
    print("Usage: python main.py [--check] [--plan] [--reinstall]")
    print("  --check      report the environment and exit")
    print("  --plan       print the install commands and exit")
    print("  --reinstall  rebuild the environment, then open the application")


def plan(reinstall):
    uv = find_uv()
    if uv is None:
        for source in UV_SOURCES:
            print("download " + source + uv_artifact_name())
        uv = [str(UV_DIR / uv_filename())]
    for command in create_environment_commands(uv, reinstall):
        print(display(command))
    return 0


def relaunch():
    env = os.environ.copy()
    env[MARKER] = "1"
    if os.name == "nt":
        python = venv_pythonw()
        if not python.is_file():
            python = venv_python()
        try:
            subprocess.Popen(
                [str(python), str(MAIN)],
                cwd=str(ROOT),
                env=env,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as error:
            print("The application could not start: " + str(error))
            return 1
        return 0
    try:
        os.execve(str(venv_python()), [str(venv_python()), str(MAIN)], env)
    except OSError as error:
        print("The application could not start: " + str(error))
        return 1


def prepare(argv):
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return None

    if "--help" in argv or "-h" in argv:
        print_help()
        return 0

    if "--check" in argv:
        return check_report()

    reinstall = "--reinstall" in argv

    if "--plan" in argv:
        return plan(reinstall)

    if sys.version_info < MINIMUM_PYTHON:
        wanted = ".".join(str(part) for part in MINIMUM_PYTHON)
        found = ".".join(str(part) for part in sys.version_info[:3])
        print("This Python is " + found + ". SINEX TRF Studio needs " + wanted + " or later.")
        print("On Windows, run run_windows.bat instead. It sets up Python for you.")
        return EXPLAINED

    if not REQUIREMENTS.is_file() or not MAIN.is_file() or not PACKAGE_DIR.is_dir():
        print("Run main.py from the project folder. main.py or requirements.txt is missing.")
        return EXPLAINED

    if not path_is_short_enough():
        print("The folder path is too long.")
        print("Move the folder closer to the drive root, then start it again.")
        return EXPLAINED

    if not path_is_supported():
        print("The folder path contains characters this system does not support.")
        print("Move the folder to a path with plain English characters, then start it again.")
        return EXPLAINED

    if reinstall or not environment_ready():
        uv = find_uv()
        if uv is None:
            uv = bootstrap_uv()
        if uv is None:
            print("The installer could not be downloaded. Trying pip instead.")
        if not install_environment(uv, reinstall):
            return 1

    if os.environ.get(MARKER):
        return None

    return relaunch()
