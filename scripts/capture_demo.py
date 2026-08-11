#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

from PIL import Image
from playwright.sync_api import Page, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture real zero-credential demo screenshots and a short GIF.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "docs" / "assets" / "demo")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hr-onboarding-demo-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        port = args.port or _available_port()
        process, log_handle = _start_demo(port, temporary_root)
        try:
            _wait_for_health(port, process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
                    _capture(page, port, output_dir, temporary_root)
                finally:
                    browser.close()
        except Exception:
            log_handle.flush()
            print((temporary_root / "server.log").read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log_handle.close()

    print(f"Captured demo media in {output_dir}")
    return 0


def _available_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_demo(port: int, temporary_root: Path) -> tuple[subprocess.Popen[bytes], object]:
    environment = os.environ.copy()
    environment.update(
        {
            "DEMO_MODE": "true",
            "ENVIRONMENT": "local",
            "ALLOW_EXTERNAL_AI": "false",
            "LLM_PROVIDER": "mock",
            "OCR_PROVIDER": "mock",
            "QA_LLM_PROVIDER": "mock",
            "DATABASE_URL": f"sqlite:///{temporary_root / 'demo.db'}",
            "UPLOAD_ROOT": str(temporary_root / "uploads"),
            "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    log_handle = (temporary_root / "server.log").open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return process, log_handle


def _wait_for_health(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    health_url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"demo server exited with status {process.returncode}")
        try:
            with urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError("demo server did not become healthy within 30 seconds")


def _capture(page: Page, port: int, output_dir: Path, temporary_root: Path) -> None:
    base_url = f"http://127.0.0.1:{port}"
    frames: list[Path] = []

    _open_and_validate(page, f"{base_url}/ui/hr/workspace", "HR 入职协同工作台")
    hr_path = output_dir / "hr-workspace.png"
    page.screenshot(path=str(hr_path), full_page=False)
    frames.append(_frame(page, temporary_root, "01-hr-top.png"))
    page.evaluate("window.scrollTo(0, Math.min(620, document.body.scrollHeight))")
    page.wait_for_timeout(400)
    frames.append(_frame(page, temporary_root, "02-hr-table.png"))

    _open_and_validate(page, f"{base_url}/ui/candidate/1/workspace", "入职进度与材料")
    candidate_path = output_dir / "candidate-workspace.png"
    page.screenshot(path=str(candidate_path), full_page=False)
    frames.append(_frame(page, temporary_root, "03-candidate-top.png"))
    page.evaluate("window.scrollTo(0, Math.min(720, document.body.scrollHeight))")
    page.wait_for_timeout(400)
    frames.append(_frame(page, temporary_root, "04-candidate-flow.png"))

    _write_gif(frames, output_dir / "hr-onboarding-demo.gif")


def _open_and_validate(page: Page, url: str, expected_heading_text: str) -> None:
    response = page.goto(url, wait_until="networkidle")
    if response is None or not response.ok:
        status = response.status if response else "no response"
        raise RuntimeError(f"demo page failed: {url} ({status})")
    heading = page.locator("h1").first.text_content() or ""
    if expected_heading_text not in heading:
        raise RuntimeError(f"unexpected demo heading at {url}: {heading!r}")
    body = page.locator("body").inner_text()
    if "Internal Server Error" in body or "Traceback" in body:
        raise RuntimeError(f"demo page contains an application error: {url}")


def _frame(page: Page, temporary_root: Path, name: str) -> Path:
    path = temporary_root / name
    page.screenshot(path=str(path), full_page=False)
    return path


def _write_gif(frame_paths: list[Path], destination: Path) -> None:
    frames: list[Image.Image] = []
    for frame_path in frame_paths:
        with Image.open(frame_path) as source:
            image = source.convert("RGB")
            image.thumbnail((1200, 800), Image.Resampling.LANCZOS)
            frames.append(image.copy())
    if not frames:
        raise RuntimeError("no demo frames were captured")
    frames[0].save(
        destination,
        save_all=True,
        append_images=frames[1:],
        duration=[1700, 1400, 1700, 1400],
        loop=0,
        optimize=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
