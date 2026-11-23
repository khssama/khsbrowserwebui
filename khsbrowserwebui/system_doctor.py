"""System doctor: an autonomous agent that checks, fixes, and verifies
everything khsbrowserwebui needs to run. It keeps trying until it works
or reports exactly what it could not fix."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .capture import ffmpeg_supports_x11grab
from .log import log, setup

MAX_ATTEMPTS = 5
RETRY_DELAY = 3


class SystemDoctor:
    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
        setup(debug=debug)
        self.attempts = 0
        self.fixes_applied: list[str] = []

    # ------------------------------------------------------------------ run
    def diagnose_and_fix(self) -> bool:
        """Main agent loop: check, fix, verify, repeat until working."""
        print("=" * 60)
        print("  KHS Browser WebUI - System Doctor (auto-fix agent)")
        print("=" * 60)
        print()

        while self.attempts < MAX_ATTEMPTS:
            self.attempts += 1
            print(f"--- Attempt {self.attempts}/{MAX_ATTEMPTS} ---")

            issues = self._check_everything()

            if not issues:
                print()
                print("✓ All checks passed. System is ready.")
                return True

            print()
            print(f"Found {len(issues)} issue(s). Attempting to fix...")

            fixed = self._fix_issues(issues)

            if not fixed:
                print("✗ Could not apply any fixes automatically.")
                break

            # Verify fixes worked before looping
            time.sleep(1)

        print()
        print(f"✗ Doctor gave up after {MAX_ATTEMPTS} attempts.")
        print("Remaining issues (fix manually):")
        for issue in self._check_everything():
            print(f"  - {issue}")
        return False

    # ---------------------------------------------------------- check all
    def _check_everything(self) -> list[str]:
        issues: list[str] = []

        # Python
        issues.extend(self._check_python_deps())

        # System binaries
        for name, cmd, install in [
            ("Xvfb", "Xvfb", "xvfb"),
            ("ffmpeg", "ffmpeg", "ffmpeg"),
            ("Chromium", "chromium-browser", "chromium-browser"),
            ("google-chrome", "google-chrome", "google-chrome-stable"),
        ]:
            if not shutil.which(cmd):
                issues.append(f"missing binary: {name} (install: {install})")

        # ffmpeg x11grab
        if shutil.which("ffmpeg") and not ffmpeg_supports_x11grab():
            issues.append("ffmpeg lacks x11grab support (reinstall ffmpeg)")

        # Browser launch test
        browser = self._find_any_browser()
        if browser:
            if not self._test_browser_launch(browser):
                issues.append(f"browser launches but crashes: {browser}")
        else:
            # Already reported as missing binary above
            pass

        return issues

    # ---------------------------------------------------------- check deps
    def _check_python_deps(self) -> list[str]:
        issues: list[str] = []
        for mod, pip_name in [("aiohttp", "aiohttp"), ("Xlib", "python-xlib")]:
            try:
                __import__(mod)
            except ImportError:
                issues.append(f"missing python module: {mod} (pip install {pip_name})")
        return issues

    # ---------------------------------------------------------- fix all
    def _fix_issues(self, issues: list[str]) -> bool:
        applied_any = False

        # 1. Install Python modules
        python_missing = [i for i in issues if "missing python module:" in i]
        if python_missing:
            modules = [
                i.split("missing python module: ")[1].split(" (pip install ")[0]
                for i in python_missing
            ]
            if self._pip_install(modules):
                applied_any = True
                self.fixes_applied.extend([f"pip install {m}" for m in modules])

        # 2. Detect OS and package manager
        pkg_manager = self._detect_package_manager()
        if not pkg_manager:
            log.warning("no supported package manager found; skipping system installs")
        else:
            # 3. Install missing system binaries
            missing_bins = [
                i for i in issues if "missing binary:" in i
            ]
            if missing_bins and pkg_manager:
                packages = self._map_binary_to_package(missing_bins)
                if packages:
                    if self._apt_install(packages, pkg_manager):
                        applied_any = True
                        self.fixes_applied.extend(
                            [f"{pkg_manager} install {p}" for p in packages]
                        )

            # 4. Fix ffmpeg x11grab
            ffmpeg_issues = [i for i in issues if "ffmpeg lacks x11grab" in i]
            if ffmpeg_issues and pkg_manager:
                if self._apt_install(["ffmpeg"], pkg_manager):
                    applied_any = True
                    self.fixes_applied.append(f"{pkg_manager} install ffmpeg")

        return applied_any

    # ---------------------------------------------------------- pip
    def _pip_install(self, modules: list[str]) -> bool:
        log.info("installing python modules: %s", ", ".join(modules))
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", *modules],
                check=True,
                timeout=120,
            )
            log.info("python modules installed successfully")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.error("pip install failed: %s", exc)
            return False

    # ---------------------------------------------------------- pkg manager
    def _detect_package_manager(self) -> str | None:
        if shutil.which("apt-get"):
            return "apt-get"
        if shutil.which("dnf"):
            return "dnf"
        if shutil.which("yum"):
            return "yum"
        if shutil.which("pacman"):
            return "pacman"
        if shutil.which("apk"):
            return "apk"
        return None

    def _map_binary_to_package(self, missing: list[str]) -> list[str]:
        """Map missing binary names to package names for the detected PM."""
        pm = self._detect_package_manager()
        mapping = {
            "apt-get": {
                "Xvfb": "xvfb",
                "ffmpeg": "ffmpeg",
                "chromium-browser": "chromium-browser",
                "google-chrome": "google-chrome-stable",
            },
            "dnf": {
                "Xvfb": "xorg-x11-server-Xvfb",
                "ffmpeg": "ffmpeg",
                "chromium-browser": "chromium",
                "google-chrome": "google-chrome-stable",
            },
            "yum": {
                "Xvfb": "xorg-x11-server-Xvfb",
                "ffmpeg": "ffmpeg",
                "chromium-browser": "chromium",
                "google-chrome": "google-chrome-stable",
            },
            "pacman": {
                "Xvfb": "xorg-server-xvfb",
                "ffmpeg": "ffmpeg",
                "chromium-browser": "chromium",
                "google-chrome": "google-chrome-stable",
            },
            "apk": {
                "Xvfb": "xvfb-run",
                "ffmpeg": "ffmpeg",
                "chromium-browser": "chromium",
                "google-chrome": "google-chrome-stable",
            },
        }
        pm_map = mapping.get(pm, {})
        packages: list[str] = []
        seen: set[str] = set()
        for issue in missing:
            # parse binary name from "missing binary: Name (install: pkg)"
            try:
                bin_name = issue.split("missing binary: ")[1].split(" ")[0]
            except IndexError:
                continue
            pkg = pm_map.get(bin_name, bin_name)
            if pkg not in seen:
                packages.append(pkg)
                seen.add(pkg)
        return packages

    def _apt_install(self, packages: list[str], pkg_manager: str) -> bool:
        log.info("installing system packages: %s", ", ".join(packages))
        try:
            subprocess.run(
                [pkg_manager, "update", "-qq"],
                check=False,
                timeout=60,
                capture_output=True,
            )
            subprocess.run(
                [pkg_manager, "install", "-y", "-qq", *packages],
                check=True,
                timeout=180,
                capture_output=True,
            )
            log.info("system packages installed successfully")
            return True
        except subprocess.CalledProcessError as exc:
            log.error("system install failed: %s", exc)
            return False
        except subprocess.TimeoutExpired as exc:
            log.error("system install timed out: %s", exc)
            return False

    # ---------------------------------------------------------- browser test
    def _find_any_browser(self) -> str | None:
        for name in ("google-chrome", "google-chrome-stable", "chrome",
                     "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                return found
        return None

    def _test_browser_launch(self, browser: str) -> bool:
        """Try to launch the browser briefly to see if it stays alive."""
        import tempfile

        profile = Path(tempfile.mkdtemp(prefix="khs-doctor-profile-"))
        display = ":99"

        # Clean Xvfb socket
        sock = Path(f"/tmp/.X11-unix/X{display.lstrip(':')}")
        if sock.exists():
            try:
                sock.unlink()
            except Exception:
                pass

        # Start Xvfb
        xvfb_proc = None
        try:
            xvfb_proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x720x24",
                 "-ac", "-nolisten", "tcp", "+extension", "RANDR"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(50):
                if sock.exists():
                    break
                if xvfb_proc.poll() is not None:
                    break
                time.sleep(0.1)
        except Exception:
            pass

        try:
            env = dict(os.environ)
            env["DISPLAY"] = display
            cmd = [
                browser,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-zygote",
                f"--user-data-dir={profile}",
                "--window-size=1280,720",
                f"--display={display}",
                "about:blank",
            ]
            proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            time.sleep(3)
            alive = proc.poll() is None
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
            return alive
        finally:
            if xvfb_proc:
                xvfb_proc.terminate()
                try:
                    xvfb_proc.wait(timeout=3)
                except Exception:
                    xvfb_proc.kill()
            import shutil as _shutil
            _shutil.rmtree(profile, ignore_errors=True)


def run_doctor(debug: bool = False) -> int:
    doctor = SystemDoctor(debug=debug)
    success = doctor.diagnose_and_fix()
    return 0 if success else 1
