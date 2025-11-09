import asyncio
import httpx
import time
import random
from pathlib import Path
import sys
import shutil
import webbrowser

# --- Roblox API config ---
VALIDATE_URL = "https://auth.roblox.com/v1/usernames/validate"
CONTEXT = "Signup"
BIRTHDAY_ISO = "1991-01-04T00:00:00.000Z"

# --- Files ---
USERNAMES_FILE = Path("usernames.txt")
TAKEN_OUT = Path("taken.txt")
AVAILABLE_OUT = Path("available.txt")
LOG_OUT = Path("responses.log")

# --- Rate/Concurrency (single IP) ---
WORKERS = 20
START_RPS = 8.0
MIN_RPS = 2.0
MAX_RPS = 15.0
RECOVERY_STEP = 0.5
JITTER_RANGE = (0.02, 0.10)

# Network error retry backoff (only for transport errors)
NET_BACKOFF_START = 1.0     # seconds
NET_BACKOFF_MAX   = 5.0     # seconds

TIMEOUT = httpx.Timeout(10.0, connect=3.0)

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/",
    "Accept-Language": "en-GB,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

ROBLOX_HOME = "https://www.roblox.com/"

# --- Stop flag when we find an available ---
FOUND_EVENT = asyncio.Event()
FOUND_USERNAME: str | None = None


def load_usernames(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"{path} not found.")
    names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    random.shuffle(names)
    return names


class AdaptiveRateLimiter:
    def __init__(self, start_rps: float, min_rps: float, max_rps: float, recovery_step: float):
        self._lock = asyncio.Lock()
        self.current_rps = start_rps
        self.min_rps = min_rps
        self.max_rps = max_rps
        self.recovery_step = recovery_step
        self._next_time = time.perf_counter()

    async def acquire(self):
        async with self._lock:
            now = time.perf_counter()
            interval = 1.0 / max(self.current_rps, 0.001)
            if now < self._next_time:
                await asyncio.sleep(self._next_time - now)
                now = time.perf_counter()
            self._next_time = now + interval

    async def penalize(self, retry_after: float | None):
        async with self._lock:
            if retry_after and retry_after > 0:
                self._next_time = max(self._next_time, time.perf_counter() + retry_after)
            self.current_rps = max(self.min_rps, self.current_rps / 2.0)

    async def reward(self):
        async with self._lock:
            self.current_rps = min(self.max_rps, self.current_rps + self.recovery_step)


def _open_chrome_or_default(url: str):
    try:
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        chrome_path = next((p for p in chrome_paths if Path(p).exists()), None)
        if chrome_path:
            webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(chrome_path))
            webbrowser.get("chrome").open(url, new=2)
            return
        for candidate in ("google-chrome", "chrome", "chromium", "chromium-browser"):
            if shutil.which(candidate):
                webbrowser.get(using=candidate).open(url, new=2)
                return
        webbrowser.open(url, new=2)
    except Exception:
        try:
            webbrowser.open(url, new=2)
        except Exception:
            pass


def _beep():
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1200, 250)
    except Exception:
        sys.stdout.write("\a\a\a")
        sys.stdout.flush()


def notify_available(username: str):
    print(f"\n>>> {username} is AVAILABLE — opening Roblox and beeping! <<<")
    _open_chrome_or_default(ROBLOX_HOME)
    _beep()


async def fetch_csrf(client: httpx.AsyncClient):
    try:
        r = await client.post(
            VALIDATE_URL,
            headers=HEADERS_BASE,
            json={"username": "token_probe", "context": CONTEXT, "birthday": BIRTHDAY_ISO},
        )
        return r.headers.get("x-csrf-token") or r.headers.get("X-CSRF-Token") or ""
    except Exception:
        return ""


async def worker(name_queue: asyncio.Queue, client: httpx.AsyncClient, headers_base: dict,
                 limiter: AdaptiveRateLimiter, results: list[tuple[str,str,str]], log_buf: list[str]):
    headers = dict(headers_base)
    global FOUND_USERNAME

    while True:
        if FOUND_EVENT.is_set():
            return
        try:
            username = name_queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        if FOUND_EVENT.is_set():
            name_queue.task_done()
            return

        # We will keep retrying THIS username on network errors until a valid response arrives.
        net_backoff = NET_BACKOFF_START
        printed_network_error = False

        while True:
            if FOUND_EVENT.is_set():
                name_queue.task_done()
                return

            await limiter.acquire()
            await asyncio.sleep(random.uniform(*JITTER_RANGE))

            try:
                r = await client.post(
                    VALIDATE_URL,
                    headers=headers,
                    json={"username": username, "context": CONTEXT, "birthday": BIRTHDAY_ISO},
                )

                # 429: adaptive backoff + single immediate retry (still for THIS username)
                if r.status_code == 429:
                    ra_hdr = r.headers.get("Retry-After")
                    retry_after = None
                    if ra_hdr:
                        try:
                            retry_after = float(ra_hdr)
                        except ValueError:
                            retry_after = None
                    if not printed_network_error:
                        # keep output minimal & consistent
                        print(f"Rate limited on {username}. Backing off…")
                    await limiter.penalize(retry_after)
                    await asyncio.sleep(random.uniform(0.8, 1.6))
                    continue  # loop & retry same username

                # 401/403: get CSRF and retry same username
                if r.status_code in (401, 403):
                    token = r.headers.get("x-csrf-token") or r.headers.get("X-CSRF-Token")
                    if token:
                        headers["X-CSRF-Token"] = token
                        # rate-limit again before retry
                        await limiter.acquire()
                        await asyncio.sleep(random.uniform(*JITTER_RANGE))
                        r = await client.post(
                            VALIDATE_URL,
                            headers=headers,
                            json={"username": username, "context": CONTEXT, "birthday": BIRTHDAY_ISO},
                        )

            except httpx.RequestError as e:
                # Transport/network problem — print only once per username, then hold/retry
                if not printed_network_error:
                    print(f"Checking: {username} -> network error — retrying…")
                    printed_network_error = True
                log_buf.append(f"{username} -> network error: {e}\n")
                await limiter.penalize(0.2)
                await asyncio.sleep(net_backoff + random.uniform(0.0, 0.3))
                # exponential backoff up to cap
                net_backoff = min(NET_BACKOFF_MAX, net_backoff * 1.5)
                continue  # retry SAME username

            # Parse response
            try:
                data = r.json()
            except Exception:
                data = {}
            log_buf.append(f"{username} -> {r.status_code} {data}\n")

            # Not a good/parseable response — penalize and retry same username
            if r.status_code != 200 or "code" not in data:
                if not printed_network_error:
                    print(f"Checking: {username} -> unexpected ({r.status_code}) — retrying…")
                    printed_network_error = True
                await limiter.penalize(0.5)
                await asyncio.sleep(net_backoff + random.uniform(0.0, 0.3))
                net_backoff = min(NET_BACKOFF_MAX, net_backoff * 1.5)
                continue  # retry SAME username

            # Valid JSON result — mark success and move on
            code = data.get("code")
            message = (data.get("message") or "").strip()
            await limiter.reward()

            if code == 0 and "valid" in message.lower():
                if not FOUND_EVENT.is_set():
                    FOUND_USERNAME = username
                    AVAILABLE_OUT.write_text(f"{username}\n", encoding="utf-8")
                    print(f"{username} is AVAILABLE ✅")
                    notify_available(username)
                    FOUND_EVENT.set()
                # end worker — we’re done
                name_queue.task_done()
                return
            else:
                results.append((username, "taken", message or "not available"))
                # Only print the normal line if we didn't already print a one-time network message.
                if printed_network_error:
                    # after successful resolution, show final outcome
                    print(f"Checking: {username} -> not available")
                else:
                    print(f"Checking: {username} -> not available")
                name_queue.task_done()
                break  # proceed to next username

        if FOUND_EVENT.is_set():
            return


async def main():
    # Fresh files
    TAKEN_OUT.write_text("", encoding="utf-8")
    AVAILABLE_OUT.write_text("", encoding="utf-8")
    LOG_OUT.write_text("", encoding="utf-8")

    usernames = load_usernames(USERNAMES_FILE)
    print(f"Loaded {len(usernames)} usernames (randomized). Starting at ~{START_RPS} req/s.")

    start = time.time()
    name_queue: asyncio.Queue[str] = asyncio.Queue()
    for u in usernames:
        name_queue.put_nowait(u)

    limiter = AdaptiveRateLimiter(START_RPS, MIN_RPS, MAX_RPS, RECOVERY_STEP)
    results: list[tuple[str,str,str]] = []
    log_buf: list[str] = []

    async with httpx.AsyncClient(http2=True, timeout=TIMEOUT) as client:
        csrf = await fetch_csrf(client)
        headers = dict(HEADERS_BASE)
        if csrf:
            headers["X-CSRF-Token"] = csrf

        workers = [
            asyncio.create_task(worker(name_queue, client, headers, limiter, results, log_buf))
            for _ in range(WORKERS)
        ]
        await asyncio.gather(*workers, return_exceptions=True)

    if results:
        taken = [u for u, s, _ in results if s == "taken"]
        if taken:
            TAKEN_OUT.write_text("\n".join(taken), encoding="utf-8")
    if log_buf:
        LOG_OUT.write_text("".join(log_buf), encoding="utf-8")

    elapsed = time.time() - start
    if FOUND_EVENT.is_set():
        print(f"\nStopped early. Found available: {FOUND_USERNAME}")
    else:
        print(f"\nAll taken after {elapsed:.2f}s — Checked {len(results)} usernames.")
    print(f"Logs: {LOG_OUT} | Taken: {TAKEN_OUT} | Available: {AVAILABLE_OUT}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
