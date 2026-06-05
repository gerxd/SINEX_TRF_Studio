import os
import platform
import shlex
import subprocess
import sys
import venv


def run_cmd(cmd, env=None):
    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def has_pip(python_executable):
    result = subprocess.run(
        [python_executable, "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def ensure_pip(python_executable):
    if has_pip(python_executable):
        return
    run_cmd([python_executable, "-m", "ensurepip", "--upgrade"])


def venv_python_path(venv_dir):
    if platform.system().lower() == "windows":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def activated_env(venv_dir):
    env = os.environ.copy()
    scripts_dir = "Scripts" if platform.system().lower() == "windows" else "bin"
    venv_bin = os.path.join(venv_dir, scripts_dir)
    env["VIRTUAL_ENV"] = venv_dir
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONHOME", None)
    return env


def detect_os_kind():
    system_name = platform.system().lower()
    if system_name == "windows":
        return "windows"
    if system_name == "linux" or system_name == "darwin":
        return "posix"
    raise RuntimeError(
        f"Unsupported operating system: {platform.system()}. "
        "This setup script supports Windows and Linux/macOS."
    )


def activation_command(shell, os_kind):
    shell_name = shell.lower()
    if os_kind == "windows":
        activate_ps = os.path.join(".venv", "Scripts", "Activate.ps1")
        activate_cmd = os.path.join(".venv", "Scripts", "activate.bat")
        if shell_name in {"powershell", "pwsh"}:
            return f"& .\\{activate_ps}"
        if shell_name in {"cmd", "batch"}:
            return f".\\{activate_cmd}"
        raise RuntimeError("Unsupported shell for Windows. Use powershell/pwsh/cmd.")

    activate_sh = os.path.join(".venv", "bin", "activate")
    if shell_name in {"bash", "zsh", "sh"}:
        return f"source {activate_sh}"
    raise RuntimeError("Unsupported shell for Linux/macOS. Use bash/zsh/sh.")


def parse_args():
    args = sys.argv[1:]
    options = {
        "print_activate": False,
        "shell": None,
        "no_open_shell": False,
    }

    if "--print-activate" in args:
        options["print_activate"] = True
        index = args.index("--print-activate")
        if index + 1 < len(args) and not args[index + 1].startswith("--"):
            options["shell"] = args[index + 1]

    if "--no-open-shell" in args:
        options["no_open_shell"] = True

    return options


def launch_activated_shell(project_dir, venv_dir, os_kind):
    if os_kind == "windows":
        activate_ps1 = os.path.join(venv_dir, "Scripts", "Activate.ps1")
        cmd = [
            "powershell",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Set-Location -LiteralPath '{project_dir}'; . '{activate_ps1}'; "
            "Write-Host 'Virtual environment activated in this shell.'",
        ]
        subprocess.Popen(cmd, cwd=project_dir)
        return True

    activate_sh = os.path.join(venv_dir, "bin", "activate")
    shell_command = (
        f"cd {shlex.quote(project_dir)} && "
        f"source {shlex.quote(activate_sh)} && "
        "echo 'Virtual environment activated in this shell.' && "
        "exec bash -i"
    )

    terminal_candidates = [
        ["x-terminal-emulator", "-e", "bash", "-ic", shell_command],
        ["gnome-terminal", "--", "bash", "-ic", shell_command],
        ["konsole", "-e", "bash", "-ic", shell_command],
        ["xfce4-terminal", "-e", f"bash -ic \"{shell_command}\""],
        ["xterm", "-e", "bash", "-ic", shell_command],
    ]

    for cmd in terminal_candidates:
        try:
            subprocess.Popen(cmd, cwd=project_dir)
            return True
        except FileNotFoundError:
            continue

    return False


def main():
    options = parse_args()
    os_kind = detect_os_kind()
    project_dir = os.path.dirname(os.path.abspath(__file__))
    requirements_file = os.path.join(project_dir, "requirements.txt")
    venv_dir = os.path.join(project_dir, ".venv")

    default_shell = "powershell" if os_kind == "windows" else "bash"
    shell = options["shell"] or default_shell

    if options["print_activate"]:
        print(activation_command(shell, os_kind))
        return 0

    print(f"Detected operating system: {platform.system()}")

    if not os.path.exists(requirements_file):
        print(f"requirements.txt not found at: {requirements_file}")
        return 1

    print("Ensuring pip is available for current Python...")
    ensure_pip(sys.executable)

    if not os.path.exists(venv_python_path(venv_dir)):
        print(f"Creating virtual environment at: {venv_dir}")
        builder = venv.EnvBuilder(with_pip=True)
        builder.create(venv_dir)
    else:
        print(f"Using existing virtual environment at: {venv_dir}")

    py_venv = venv_python_path(venv_dir)
    print("Ensuring pip is available inside virtual environment...")
    ensure_pip(py_venv)

    env = activated_env(venv_dir)

    print("Upgrading pip in virtual environment...")
    run_cmd([py_venv, "-m", "pip", "install", "--upgrade", "pip"], env=env)

    print("Installing dependencies from requirements.txt...")
    run_cmd([py_venv, "-m", "pip", "install", "-r", requirements_file], env=env)

    activate_cmd = activation_command(shell, os_kind)

    print("\nSetup complete.")
    print(f"To activate the environment in your shell, run: {activate_cmd}")

    if not options["no_open_shell"]:
        opened = launch_activated_shell(project_dir, venv_dir, os_kind)
        if opened:
            print("Opened a new terminal with the virtual environment activated.")
        else:
            print("Could not open a terminal automatically on this system.")

    return 0


if __name__ == "__main__":
    sys.exit(main())