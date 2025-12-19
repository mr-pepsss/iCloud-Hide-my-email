# -*- coding: utf-8 -*-
# iCloud+ Hide My Email — устойчивый автосоздатель алиасов

import os, re, sys, time
from datetime import datetime
from typing import Set, Tuple, Literal, Any
from playwright.sync_api import sync_playwright, Page, FrameLocator

# -------- .env --------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ICLOUD_URL    = os.getenv("ICLOUD_URL", "https://www.icloud.com/icloudplus/")
PERSIST_DIR   = os.getenv("PERSIST_DIR", "./icloud_profile")
HEADLESS      = os.getenv("HEADLESS", "false").lower() in ("1","true","yes","y")
BATCH_SIZE    = int(os.getenv("BATCH_SIZE", "5"))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "3600"))
ALIASES_FILE  = os.getenv("ALIASES_FILE", "aliases.txt")
DEFAULT_TIMEOUT = int(os.getenv("DEFAULT_TIMEOUT_MS", "30000"))
WAIT_AFTER_CLICK_MS = int(os.getenv("WAIT_AFTER_CLICK_MS", "400"))
START_NUMBER  = int(os.getenv("START_NUMBER", "1"))
QUICK_CHECK_MS = int(os.getenv("QUICK_CHECK_MS", "2000"))

FIXED_UA   = os.getenv("FIXED_UA", "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")
LOCALE     = os.getenv("LOCALE", "ru-RU")
TIMEZONE   = os.getenv("TIMEZONE", "America/Edmonton")

# селекторы
SEL_HIDE_MY_EMAIL   = os.getenv("SEL_HIDE_MY_EMAIL")
SEL_PANEL_ROOT      = os.getenv("SEL_PANEL_ROOT")
SEL_PANEL_ADD       = os.getenv("SEL_PANEL_ADD")
SEL_PANEL_LABEL     = os.getenv("SEL_PANEL_LABEL")
SEL_PANEL_GEN       = os.getenv("SEL_PANEL_GEN")
SEL_PANEL_CREATE    = os.getenv("SEL_PANEL_CREATE")
SEL_PANEL_ERROR_ICON= os.getenv("SEL_PANEL_ERROR_ICON")

HME_IFRAME_CSS      = os.getenv("HME_IFRAME_CSS", "iframe[data-name='hidemyemail']")
IF_ADD              = os.getenv("IF_ADD", 'button[title="Add"]')
IF_LABEL            = os.getenv("IF_LABEL", 'input[name="hme-label"]')
IF_GEN              = os.getenv("IF_GEN", "div.GeneratedEmail-hme")
IF_ADD_NAME_REGEX   = os.getenv("IF_ADD_NAME_REGEX", r"(Add|Добавить|\+)")

HME_AFTER_CLICK_SLEEP_MS = int(os.getenv("HME_AFTER_CLICK_SLEEP_MS", "1200"))

# -------- исключение и паттерны лимита --------
class RateLimited(Exception):
    pass

RATE_LIMIT_RE = re.compile(
    r"(limit|too\s+many|try\s+again\s+later|come\s+back\s+in|hour|reached\s+the\s+limit)"
    r"|"
    r"(лимит|достигнут[оа]\s+предельн|невозможн[оа]\s+создать\s+больш|повторит[еь]\s+позже|в\s+настоящий\s+момент\s+невозможно)",
    re.I
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@icloud\.com", re.I)
ADD_NAME_RE = re.compile(IF_ADD_NAME_REGEX, re.I)

# -------- helpers --------
def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def pause_page(page: Page, ms: int = None):
    try: page.wait_for_timeout(ms or WAIT_AFTER_CLICK_MS)
    except: pass

def page_text(page: Page, timeout: int = 2000) -> str:
    try: return page.locator("body").inner_text(timeout=timeout)
    except: return page.content()

def looks_like_rate_limit(text: str) -> bool:
    return bool(text and RATE_LIMIT_RE.search(text))

def read_file_existing(path: str) -> Tuple[Set[int], Set[str]]:
    nums, emails = set(), set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if ":" not in line: continue
                left, right = line.strip().split(":", 1)
                try: nums.add(int(left.strip()))
                except: pass
                m = EMAIL_RE.search(right)
                if m: emails.add(m.group(0))
    return nums, emails

def append_mapping(path: str, number: int, email: str):
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{number}:{email}\n")

def compute_next_number(file_numbers: Set[int], baseline: int) -> int:
    base = baseline if baseline >= 1 else 1
    return max(base, (max(file_numbers) + 1) if file_numbers else base)

def L(scope: Any, selector: str):
    return scope.locator(selector)

def has_text(scope: Any, pattern: re.Pattern) -> bool:
    try:
        loc = scope.get_by_text(pattern)
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False

def sleep_with_countdown(total_seconds: int, label: str = "Сплю"):
    remaining = int(total_seconds)
    last_len = 0
    start = time.time()
    while remaining > 0:
        mins, secs = divmod(remaining, 60)
        text = f"{label}: {mins:02d}:{secs:02d} осталось"
        sys.stdout.write("\r" + " " * max(last_len, len(text)) + "\r")
        sys.stdout.write(text)
        sys.stdout.flush()
        time.sleep(1)
        remaining = total_seconds - int(time.time() - start)
        last_len = len(text)
    sys.stdout.write("\r" + " " * last_len + "\r")
    sys.stdout.flush()
    print(f"{label}: 00:00 осталось")

# -------- login --------
def ensure_logged_in(page: Page):
    try:
        L(page, SEL_HIDE_MY_EMAIL).first.wait_for(timeout=5000)
        log("✓ Сессия активна")
        return
    except Exception:
        log("⚠️ Не залогинен. Войдите (логин/2FA/Trust) в открытом окне.")
        last = time.time()
        while True:
            try:
                L(page, SEL_HIDE_MY_EMAIL).first.wait_for(timeout=2000)
                log("✓ Логин распознан, продолжаю.")
                return
            except Exception:
                if time.time() - last > 30:
                    log("…жду логина/подтверждения.")
                    last = time.time()
                time.sleep(1)

# -------- контексты --------
Context = Tuple[Literal["panel","iframe"], Any]  # ("panel", Page) | ("iframe", FrameLocator)

def detect_context(page: Page, timeout_ms: int = 12000) -> Context:
    deadline = time.time() + timeout_ms/1000.0
    try:
        L(page, SEL_PANEL_ROOT).first.wait_for(timeout=600)
        log("→ Обнаружена панель <aside>")
        return ("panel", page)
    except Exception:
        pass
    while time.time() < deadline:
        try:
            L(page, SEL_PANEL_ROOT).first.wait_for(timeout=250)
            log("→ Обнаружена панель <aside>")
            return ("panel", page)
        except Exception:
            pass
        try:
            iframe_el = page.locator(HME_IFRAME_CSS).first
            iframe_el.wait_for(timeout=250, state="visible")
            fl: FrameLocator = page.frame_locator(HME_IFRAME_CSS)
            log("→ Обнаружен iframe hidemyemail (через frame_locator)")
            return ("iframe", fl)
        except Exception:
            pass
        time.sleep(0.08)
    raise RuntimeError("Не удалось определить контекст (ни панель, ни iframe)")

def open_hme(page: Page) -> Context:
    try:
        ctx = detect_context(page, timeout_ms=1200)
        pause_page(page, HME_AFTER_CLICK_SLEEP_MS)
        return ctx
    except Exception:
        pass
    L(page, SEL_HIDE_MY_EMAIL).first.click(timeout=DEFAULT_TIMEOUT)
    log("✓ Клик по Hide My Email")
    ctx = detect_context(page, timeout_ms=DEFAULT_TIMEOUT)
    pause_page(page, HME_AFTER_CLICK_SLEEP_MS)
    return ctx

# -------- работа в контексте --------
def click_add(ctx: Context):
    kind, scope = ctx
    if kind == "panel":
        L(scope, SEL_PANEL_ADD).first.wait_for(timeout=DEFAULT_TIMEOUT)
        L(scope, SEL_PANEL_ADD).first.click(timeout=DEFAULT_TIMEOUT)
        log("✓ Клик по '+' (панель)")
        return
    fl: FrameLocator = scope
    strategies = [
        ("IF_ADD (env)", lambda: L(fl, IF_ADD).first),
        ("role[name~=Add|Добавить|+]", lambda: fl.get_by_role("button", name=ADD_NAME_RE).first),
        ("IconButton.AddButton", lambda: L(fl, ".IconButton.AddButton button").first),
        ("any icon-only button",  lambda: L(fl, "button.button-icon-only").first),
    ]
    last_err = None
    for label, getter in strategies:
        try:
            loc = getter()
            try:
                cnt = L(fl, IF_ADD).count()
                log(f"   debug: IF_ADD count={cnt}")
            except Exception:
                pass
            loc.wait_for(timeout=4000)
            loc.click(timeout=DEFAULT_TIMEOUT)
            log(f"✓ Клик по '+' (iframe, {label})")
            return
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("Не удалось кликнуть '+' в iframe")

def fill_label(ctx: Context, text: str):
    kind, scope = ctx
    sel = SEL_PANEL_LABEL if kind == "panel" else IF_LABEL
    inp = L(scope, sel).first
    inp.wait_for(timeout=DEFAULT_TIMEOUT)
    inp.fill(text, timeout=DEFAULT_TIMEOUT)
    log(f"→ Ввёл номер {text}")

def read_generated(ctx: Context) -> str:
    kind, scope = ctx
    sel = SEL_PANEL_GEN if kind == "panel" else IF_GEN
    try:
        txt = L(scope, sel).first.inner_text(timeout=15000)  # твоя правка: 15с максимум
        m = EMAIL_RE.search(txt)
        return m.group(0) if m else ""
    except Exception:
        return ""

# ——— детект ошибки ВНУТРИ текущего диалога (aside/iframe)
def detect_rate_limit_in_ctx(ctx: Context) -> bool:
    kind, scope = ctx

    # 1) явные элементы ошибок
    selectors = [
        "[role='alert']",
        ".FormField-error",
        ".InlineError",
        ".ErrorMessage",
        ".Typography-error",
        ".form-textbox-error",
        ".Error", ".error",
    ]
    if kind == "panel" and SEL_PANEL_ERROR_ICON:
        selectors.insert(0, SEL_PANEL_ERROR_ICON)  # твой точный XPath — приоритетно

    for sel in selectors:
        try:
            if L(scope, sel).first.is_visible():
                return True
        except Exception:
            pass

    # 2) фразы в тексте на RU/EN
    try:
        patterns = [
            re.compile(r"в\s+настоящий\s+момент\s+невозможно\s+создать\s+больш", re.I),
            re.compile(r"достигнут[оа]\s+предельн", re.I),
            re.compile(r"повторит[еь]\s+позже", re.I),
            re.compile(r"limit|too\s+many|try\s+again\s+later|come\s+back\s+in|reached\s+the\s+limit|hour", re.I),
        ]
        for pat in patterns:
            if has_text(scope, pat):
                return True
    except Exception:
        pass

    return False

def click_create_and_quick_check(ctx: Context, quick_ms: int):
    """
    Жмём 'Создать' и тупо ждём quick_ms.
    Если за это время ВНУТРИ диалога видим ошибку — RateLimited.
    Если ошибки не проявилось — считаем успех.
    """
    def _wait_and_click_button(btn_loc):
        btn_loc.wait_for(timeout=DEFAULT_TIMEOUT)
        start = time.time()
        while time.time() - start < 20:
            try:
                if btn_loc.is_enabled():
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            # кнопка так и не активировалась — обычно это и есть лимит
            raise RateLimited("Create button stayed disabled (likely hourly limit)")
        btn_loc.scroll_into_view_if_needed(timeout=2000)
        btn_loc.click(timeout=DEFAULT_TIMEOUT)

    kind, scope = ctx
    if kind == "panel":
        btn = L(scope, SEL_PANEL_CREATE).first
    else:
        fl: FrameLocator = scope
        btn = L(fl, ".modal-button-bar button").last

    _wait_and_click_button(btn)
    log("✓ Клик по 'Создать'")

    # ждём строго quick_ms и в это время мониторим ошибку
    end = time.time() + quick_ms/1000.0
    while time.time() < end:
        if detect_rate_limit_in_ctx(ctx):
            raise RateLimited("Rate-limit error shown in dialog")
        time.sleep(0.1)

# -------- один цикл --------
def create_one_alias(page: Page, number: int) -> str:
    emails_before = set(EMAIL_RE.findall(page_text(page)))

    ctx = open_hme(page)
    click_add(ctx)
    fill_label(ctx, str(number))

    gen_email = read_generated(ctx)
    if gen_email:
        log(f"→ Предварительно сгенерировано: {gen_email}")

    # Правило: если за QUICK_CHECK_MS ошибка не появилась — успех
    click_create_and_quick_check(ctx, quick_ms=QUICK_CHECK_MS)

    # опционально обновим главную и попробуем подтвердить появление адреса
    pause_page(page, 600)
    page.reload(wait_until="domcontentloaded")
    ensure_logged_in(page)

    emails_after = set(EMAIL_RE.findall(page_text(page)))
    if gen_email and gen_email in emails_after:
        log(f"→ Новый адрес: {gen_email}")
        return gen_email

    new_emails = emails_after - emails_before
    if new_emails:
        found = sorted(new_emails)[0]
        log(f"→ Новый адрес: {found}")
        return found

    # если не нашли на странице — всё равно используем сгенерированный
    return gen_email or "unknown"

# -------- main --------
def main_loop():
    abs_profile = os.path.abspath(PERSIST_DIR)
    log(f"Профиль: {abs_profile}")
    os.makedirs(abs_profile, exist_ok=True)

    # общие аргументы браузера
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--no-first-run",
        # anti-throttling / фоновая работа
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-features=CalculateNativeWinOcclusion",
        "--disable-ipc-flooding-protection",
        "--wm-window-animations-disabled",
    ]
    # чтобы в headless не лез в macOS Keychain (иначе всплывает запрос)
    if HEADLESS:
        args.append("--use-mock-keychain")  # или: args.append("--password-store=basic")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=abs_profile,
            headless=HEADLESS,
            user_agent=FIXED_UA,
            viewport={"width": 1280, "height": 900},
            locale=LOCALE,
            timezone_id=TIMEZONE,
            args=args,
        )
        page = ctx.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)

        log(f"Открываю: {ICLOUD_URL}")
        page.goto(ICLOUD_URL)
        ensure_logged_in(page)

        file_numbers, _ = read_file_existing(ALIASES_FILE)
        next_number = compute_next_number(file_numbers, START_NUMBER)
        log(f"Стартовый номер = {next_number}")

        while True:
            log(f"=== Новая партия: создаю {BATCH_SIZE} адрес(ов) ===")
            created = 0

            for attempt_idx in range(1, BATCH_SIZE + 1):
                log(f"[Партия] Попытка #{attempt_idx} из {BATCH_SIZE} — текущий номер: {next_number}")
                try:
                    email = create_one_alias(page, next_number)
                    append_mapping(ALIASES_FILE, next_number, email)
                    created += 1
                    log(f"✅ {next_number}:{email}  (успешно в попытке #{attempt_idx})")
                    next_number += 1                 # инкремент ТОЛЬКО при успехе
                    pause_page(page, 800)
                except RateLimited as rl:
                    log(f"⏸ Достигли почасового лимита на попытке #{attempt_idx}: {rl}")
                    break                            # прерываем оставшиеся попытки в этой партии
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    log(f"⚠️ Ошибка на попытке #{attempt_idx}: {e}")
                    # номер НЕ увеличиваем — попробуем тот же позже
                    pause_page(page, 1500)
                    # продолжаем попытки в рамках партии

            # ---- СОН ПОСЛЕ ПАРТИИ ----
            if created == BATCH_SIZE:
                log(f"Готово: {created}/{BATCH_SIZE}. Причина паузы: партия завершена.")
            else:
                log(f"Готово: {created}/{BATCH_SIZE}. Причина паузы: hourly rate limit или ошибки.")

            sleep_with_countdown(SLEEP_SECONDS, label=f"Сплю {SLEEP_SECONDS} сек")

            # после сна сразу обновляем страницу и убеждаемся, что сессия жива
            try:
                page.reload(wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
                ensure_logged_in(page)
                log("✓ Страница обновлена после сна, продолжаю работу")
            except Exception as e:
                log(f"⚠️ Ошибка при обновлении после сна: {e}")

# -------- restart wrapper --------
if __name__ == "__main__":
    os.environ["PYTHONUNBUFFERED"] = "1"
    print(">> script started", flush=True)
    while True:
        try:
            main_loop()
        except KeyboardInterrupt:
            print("\n[INFO] Остановлено пользователем.")
            sys.exit(0)
        except Exception as e:
            print("!! FATAL (перезапуск через 10с) ::", repr(e))
            time.sleep(10)
