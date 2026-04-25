#!/usr/bin/env python3
"""APIMart `gemini-3-pro-image-preview` generation wrapper.

The APIMart image endpoint is asynchronous: submit returns a task_id, then poll
`/v1/tasks/{task_id}` until status is `completed` or `failed`. Images come back
as URLs that expire in 24h, so we download them immediately.

Single-image mode:
  python image_gen.py \\
    --prompt "A flat editorial illustration ..." \\
    --output /tmp/cover_raw.png \\
    --size 16:9 --resolution 2K

Batch mode (runs in parallel):
  python image_gen.py \\
    --prompts-json /tmp/body_prompts.json \\
    --output-dir /tmp/body/

prompts-json format:
  [
    {"prompt": "...", "size": "16:9", "resolution": "1K", "filename": "img_1.png"},
    {"prompt": "...", "size": "1:1",  "resolution": "1K", "filename": "img_2.png"},
    ...
  ]

Credentials:
  APIMART_API_KEY env var, or ~/.wechat_publisher/credentials.json key `apimart_api_key`.
"""
import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.stderr.write("error: requests not installed. Run: pip install requests\n")
    sys.exit(1)


ENDPOINT_GENERATE = "https://api.apimart.ai/v1/images/generations"
ENDPOINT_TASK = "https://api.apimart.ai/v1/tasks"
MODEL = "gemini-3-pro-image-preview"
POLL_INTERVAL = 5  # seconds
POLL_TIMEOUT = 300  # seconds


def load_api_key():
    key = os.environ.get("APIMART_API_KEY")
    if key:
        return key
    creds_path = Path.home() / ".wechat_publisher" / "credentials.json"
    if creds_path.exists():
        try:
            data = json.loads(creds_path.read_text())
            k = data.get("apimart_api_key")
            if k:
                return k
        except Exception:
            pass
    sys.stderr.write(
        "error: APIMART_API_KEY not set and not found in ~/.wechat_publisher/credentials.json\n"
    )
    sys.exit(2)


def submit(api_key, prompt, size="16:9", resolution="1K"):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "size": size,
        "n": 1,
        "resolution": resolution,
    }
    r = requests.post(ENDPOINT_GENERATE, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") and data["code"] != 200:
        raise RuntimeError(f"APIMart submit failed: {data}")
    items = data.get("data") or []
    if not items or not items[0].get("task_id"):
        raise RuntimeError(f"APIMart submit returned no task_id: {data}")
    return items[0]["task_id"]


def poll(api_key, task_id):
    """Block until the task completes; return the image URL."""
    headers = {"Authorization": f"Bearer {api_key}"}
    start = time.time()
    while True:
        r = requests.get(
            f"{ENDPOINT_TASK}/{task_id}",
            headers=headers,
            params={"language": "zh"},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data", {})
        status = data.get("status")

        if status == "completed":
            images = data.get("result", {}).get("images", [])
            if not images:
                raise RuntimeError(f"task {task_id} completed but returned no images")
            url_field = images[0].get("url")
            # `url` field may be either a string or a list of strings depending on model.
            if isinstance(url_field, list):
                return url_field[0]
            return url_field

        if status in ("failed", "cancelled"):
            err = data.get("error") or {}
            raise RuntimeError(
                f"task {task_id} {status}: {err.get('message', 'no message')}"
            )

        if time.time() - start > POLL_TIMEOUT:
            raise TimeoutError(f"task {task_id} polling timed out after {POLL_TIMEOUT}s")

        sys.stderr.write(
            f"  [task {task_id[:8]}…] status={status} progress={data.get('progress', 0)}%\n"
        )
        time.sleep(POLL_INTERVAL)


def download(url, output_path):
    r = requests.get(url, timeout=60, stream=True)
    r.raise_for_status()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def generate_one(api_key, prompt, size, resolution, output_path):
    sys.stderr.write(f"→ submit: size={size} res={resolution} out={output_path}\n")
    task_id = submit(api_key, prompt, size, resolution)
    sys.stderr.write(f"  task_id={task_id}\n")
    url = poll(api_key, task_id)
    sys.stderr.write(f"  completed, downloading {url}\n")
    download(url, output_path)
    sys.stderr.write(f"  saved → {output_path}\n")
    return output_path


def batch(api_key, prompts, output_dir):
    """Submit all prompts in parallel, poll and download in parallel."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    results = {}

    def work(idx, spec):
        name = spec.get("filename") or f"img_{idx + 1}.png"
        path = Path(output_dir) / name
        generate_one(
            api_key,
            spec["prompt"],
            spec.get("size", "16:9"),
            spec.get("resolution", "1K"),
            str(path),
        )
        return idx, str(path)

    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(prompts), 4)) as ex:
        futures = [ex.submit(work, i, p) for i, p in enumerate(prompts)]
        for fut in concurrent.futures.as_completed(futures):
            try:
                idx, path = fut.result()
                results[idx] = path
            except Exception as e:
                errors.append(str(e))

    if errors:
        sys.stderr.write("\n".join("  ERROR: " + e for e in errors) + "\n")
        sys.exit(3)

    ordered = [results[i] for i in sorted(results)]
    print(json.dumps(ordered, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Single-image mode
    ap.add_argument("--prompt")
    ap.add_argument("--output")
    ap.add_argument("--size", default="16:9")
    ap.add_argument("--resolution", default="1K")
    # Batch mode
    ap.add_argument("--prompts-json")
    ap.add_argument("--output-dir")

    args = ap.parse_args()
    api_key = load_api_key()

    if args.prompts_json and args.output_dir:
        with open(args.prompts_json) as f:
            prompts = json.load(f)
        if not isinstance(prompts, list) or not prompts:
            sys.stderr.write("error: prompts-json must be a non-empty JSON array\n")
            sys.exit(1)
        batch(api_key, prompts, args.output_dir)
    elif args.prompt and args.output:
        generate_one(api_key, args.prompt, args.size, args.resolution, args.output)
    else:
        ap.error(
            "specify either (--prompt + --output) for single, "
            "or (--prompts-json + --output-dir) for batch"
        )


if __name__ == "__main__":
    main()
