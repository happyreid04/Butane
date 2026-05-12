import telebot
import requests
from bs4 import BeautifulSoup
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
import json
import os

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN       = "YOUR_BOT_TOKEN"
CHANNEL_ID      = "@your_channel_username"   # or numeric: -1001234567890
GOOGLE_API_KEY  = "YOUR_GOOGLE_API_KEY"      # used with Custom Search JSON API
GOOGLE_CSE_ID   = "YOUR_CSE_ID"             # Custom Search Engine ID (from cse.google.com)

bot = telebot.TeleBot(BOT_TOKEN)

# ─────────────────────────────────────────────
# SECTION 1 — RETRY DECORATOR (exponential backoff)
# ─────────────────────────────────────────────
def with_retries(func, *args, max_retries=3, base_delay=2, **kwargs):
    """
    Calls `func(*args, **kwargs)` up to `max_retries` times.
    On failure waits base_delay * 2^attempt seconds (exponential backoff).
    Returns (result, None) on success or (None, last_exception) on total failure.
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs), None
        except Exception as exc:
            last_exc = exc
            wait = base_delay * (2 ** attempt)
            print(f"[Retry {attempt+1}/{max_retries}] {exc} — waiting {wait}s")
            time.sleep(wait)
    return None, last_exc


# ─────────────────────────────────────────────
# SECTION 2 — SCRAPER (rich extraction)
# ─────────────────────────────────────────────
def fetch_page(url: str) -> requests.Response:
    """Raw HTTP GET with a browser-like User-Agent."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()          # raises HTTPError for 4xx/5xx
    return resp


def scrape_data(url: str) -> dict:
    """
    Scrapes a URL and returns a structured dict with:
        title, description, headings (h1-h3), top links, image URLs, body snippet.
    Uses with_retries() for resilience.
    """
    response, err = with_retries(fetch_page, url)
    if err:
        return {"error": str(err)}

    soup = BeautifulSoup(response.text, "html.parser")

    # ── Title ──────────────────────────────────
    title = soup.title.string.strip() if soup.title else "N/A"

    # ── Meta description ───────────────────────
    meta = soup.find("meta", attrs={"name": "description"})
    description = meta["content"].strip() if meta and meta.get("content") else "N/A"

    # ── Headings h1→h3 (max 5 each) ───────────
    headings = {}
    for tag in ("h1", "h2", "h3"):
        headings[tag] = [h.get_text(strip=True) for h in soup.find_all(tag)][:5]

    # ── Top internal/external links (max 10) ───
    links = []
    for a in soup.find_all("a", href=True)[:10]:
        href = a["href"]
        if href.startswith("http"):
            links.append(href)

    # ── Image src URLs (max 5) ─────────────────
    images = []
    for img in soup.find_all("img", src=True)[:5]:
        src = img["src"]
        if src.startswith("http"):
            images.append(src)

    # ── Body text snippet (first 300 chars) ────
    body_text = soup.get_text(separator=" ", strip=True)
    snippet = " ".join(body_text.split())[:300]

    return {
        "url": url,
        "title": title,
        "description": description,
        "headings": headings,
        "links": links,
        "images": images,
        "snippet": snippet,
    }


def format_scrape_result(data: dict) -> str:
    """Turns the scraped dict into a readable Telegram message (MarkdownV2-safe)."""
    if "error" in data:
        return f"❌ *Scraping failed:* `{data['error']}`"

    lines = [
        f"🌐 *Title:* {data['title']}",
        f"📝 *Description:* {data['description']}",
        "",
    ]

    for tag, items in data["headings"].items():
        if items:
            lines.append(f"*{tag.upper()} Headings:*")
            lines += [f"  • {h}" for h in items]

    if data["links"]:
        lines.append("\n🔗 *Top Links:*")
        lines += [f"  • {l}" for l in data["links"]]

    if data["images"]:
        lines.append("\n🖼 *Images found:*")
        lines += [f"  • {i}" for i in data["images"]]

    if data["snippet"]:
        lines.append(f"\n📄 *Snippet:* _{data['snippet']}…_")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# SECTION 3 — GOOGLE CUSTOM SEARCH (API key only)
# ─────────────────────────────────────────────
def google_search(query: str, num: int = 5) -> list[dict]:
    """
    Calls Google Custom Search JSON API using only an API key + CSE ID.
    No OAuth / Client ID required.
    Docs: https://developers.google.com/custom-search/v1/using_rest

    Returns a list of {title, link, snippet} dicts.
    """
    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx":  GOOGLE_CSE_ID,
        "q":   query,
        "num": min(num, 10),      # API max is 10
    }
    response, err = with_retries(requests.get, endpoint, params=params, timeout=10)
    if err:
        return [{"error": str(err)}]

    data = response.json()
    results = []
    for item in data.get("items", []):
        results.append({
            "title":   item.get("title", ""),
            "link":    item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })
    return results


def format_google_results(results: list[dict]) -> str:
    if not results:
        return "No results found."
    if "error" in results[0]:
        return f"❌ Google API error: `{results[0]['error']}`"

    lines = ["🔍 *Google Search Results:*\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"*{i}. {r['title']}*")
        lines.append(f"   {r['snippet']}")
        lines.append(f"   🔗 {r['link']}\n")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# SECTION 4 — SEND TO TELEGRAM CHANNEL
# ─────────────────────────────────────────────
def send_to_channel(text: str, parse_mode: str = "Markdown") -> bool:
    """
    Pushes a message to CHANNEL_ID.
    The bot must be an admin of the channel with 'Post Messages' permission.
    Returns True on success.
    """
    _, err = with_retries(
        bot.send_message,
        CHANNEL_ID,
        text,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
    )
    return err is None


# ─────────────────────────────────────────────
# SECTION 5 — INLINE KEYBOARDS
# ─────────────────────────────────────────────
def scrape_action_keyboard(url: str) -> InlineKeyboardMarkup:
    """Keyboard shown after a scrape result, offering follow-up actions."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📤 Send to Channel", callback_data=f"channel|{url}"),
        InlineKeyboardButton("🔍 Google this URL", callback_data=f"google|{url}"),
        InlineKeyboardButton("🔄 Re-scrape",       callback_data=f"rescrape|{url}"),
    )
    return kb


def search_action_keyboard(query: str) -> InlineKeyboardMarkup:
    """Keyboard shown after Google search results."""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📤 Send results to Channel", callback_data=f"gsend|{query}"),
    )
    return kb


# ─────────────────────────────────────────────
# SECTION 6 — BOT COMMAND HANDLERS
# ─────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    text = (
        "👋 *Welcome to the Web Scraper Bot!*\n\n"
        "Send me a URL and I'll extract:\n"
        "  • Title & meta description\n"
        "  • H1/H2/H3 headings\n"
        "  • Top links & images\n"
        "  • A body text snippet\n\n"
        "Or use /search <query> to run a Google search.\n"
        "Type /help for full command reference."
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(commands=["help"])
def cmd_help(message):
    text = (
        "📖 *Command Reference*\n\n"
        "*Scraping*\n"
        "  Just paste any URL — the bot scrapes it immediately.\n\n"
        "*Google Search*\n"
        "  `/search <query>` — search via Google Custom Search API\n\n"
        "*Channel*\n"
        "  After scraping or searching, tap *Send to Channel* to\n"
        "  push the result to the configured Telegram channel.\n\n"
        "*Other*\n"
        "  `/start` — show welcome message\n"
        "  `/help`  — this menu\n"
    )
    bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(commands=["search"])
def cmd_search(message):
    query = message.text.partition(" ")[2].strip()
    if not query:
        bot.reply_to(message, "Usage: `/search <your query>`", parse_mode="Markdown")
        return

    bot.send_chat_action(message.chat.id, "typing")
    results = google_search(query)
    formatted = format_google_results(results)
    bot.send_message(
        message.chat.id,
        formatted,
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=search_action_keyboard(query),
    )


# ─────────────────────────────────────────────
# SECTION 7 — URL / GENERAL MESSAGE HANDLER
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
def handle_message(message):
    text = message.text.strip()

    if text.startswith("http://") or text.startswith("https://"):
        bot.send_chat_action(message.chat.id, "typing")
        bot.send_message(message.chat.id, "⏳ Scraping… please wait.")

        data = scrape_data(text)
        formatted = format_scrape_result(data)

        bot.send_message(
            message.chat.id,
            formatted,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=scrape_action_keyboard(text),
        )
    else:
        bot.reply_to(
            message,
            "Please send a valid URL starting with http:// or https://\n"
            "Or use /search <query> to do a Google search.",
        )


# ─────────────────────────────────────────────
# SECTION 8 — INLINE BUTTON CALLBACK HANDLER
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    bot.answer_callback_query(call.id)          # dismiss the loading spinner
    action, _, payload = call.data.partition("|")

    # ── Send scraped result to channel ────────
    if action == "channel":
        data = scrape_data(payload)
        formatted = format_scrape_result(data)
        success = send_to_channel(formatted)
        reply = "✅ Sent to channel!" if success else "❌ Failed to send to channel."
        bot.send_message(call.message.chat.id, reply)

    # ── Google the scraped URL ─────────────────
    elif action == "google":
        bot.send_chat_action(call.message.chat.id, "typing")
        results = google_search(payload)
        formatted = format_google_results(results)
        bot.send_message(
            call.message.chat.id,
            formatted,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=search_action_keyboard(payload),
        )

    # ── Re-scrape the same URL ─────────────────
    elif action == "rescrape":
        bot.send_chat_action(call.message.chat.id, "typing")
        data = scrape_data(payload)
        formatted = format_scrape_result(data)
        bot.send_message(
            call.message.chat.id,
            formatted,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=scrape_action_keyboard(payload),
        )

    # ── Send Google results to channel ────────
    elif action == "gsend":
        results = google_search(payload)
        formatted = format_google_results(results)
        success = send_to_channel(formatted)
        reply = "✅ Search results sent to channel!" if success else "❌ Failed to send."
        bot.send_message(call.message.chat.id, reply)


# ─────────────────────────────────────────────
# SECTION 9 — POLLING
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("Bot is running…")
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
