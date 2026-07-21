from BTSpeak import dialogs 
#from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, replace

from config import ConfigError, config_dir, load_config, save_config
from api import ApiError
from mastodon import MastodonClient, authorize_in_browser, register_app
from render import (
    account_name,
    notification_links,
    notification_reply_target,
    plain_text,
    render_notification,
    render_status,
    has_quote_reference,
    status_links,
    status_quote_url,
    status_reply_target,
)


BACK_CHOICE = "Back"
LOAD_NEXT_CHOICE = "Load Next"
CACHE_TTL_SECONDS = 300
MENTION_RE = re.compile(r"(^|\s)@[A-Za-z0-9_][A-Za-z0-9_.-]*(?:@[A-Za-z0-9.-]+)?\b")
_main_menu_lists_cache: tuple[float, list[dict]] | None = None


@dataclass(frozen=True)
class TimelineChoice:
    label: str
    links: list[str]
    reply_to_id: str
    reply_to_acct: str
    boost_id: str
    quote_id: str
    quote_url: str
    page_id: str
    author_id: str
    author_acct: str
    author_profile_url: str
    author_is_known_followed: bool = False
    default_visibility: str = ""
    reply_mentions: list[str] = field(default_factory=list)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return menu()

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except (ApiError, ConfigError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btmastodon",
        description="A braille-first Mastodon terminal client.",
    )
    subcommands = parser.add_subparsers(required=True)

    login_parser = subcommands.add_parser("login", help="Log in to a Mastodon instance")
    login_parser.add_argument("instance", help="Instance host, for example mastodon.social")
    login_parser.set_defaults(func=login)

    whoami_parser = subcommands.add_parser("whoami", help="Show the logged-in account")
    whoami_parser.set_defaults(func=whoami)

    timeline_parser = subcommands.add_parser("timeline", help="Read the home timeline")
    timeline_parser.add_argument("--limit", type=bounded_limit, default=20)
    timeline_parser.set_defaults(func=timeline)

    notifications_parser = subcommands.add_parser("notifications", help="Read notifications")
    notifications_parser.add_argument("--limit", type=bounded_limit, default=20)
    notifications_parser.set_defaults(func=notifications)

    direct_parser = subcommands.add_parser("direct", help="Read direct messages")
    direct_parser.add_argument("--limit", type=bounded_limit, default=20)
    direct_parser.set_defaults(func=direct_messages)

    post_parser = subcommands.add_parser("post", help="Post a status")
    post_parser.add_argument("status", help="Status text to post")
    post_parser.add_argument(
        "--visibility",
        choices=["public", "unlisted", "private", "direct"],
        default="public",
    )
    post_parser.add_argument("--quote-status-id", help="Status ID to quote")
    post_parser.set_defaults(func=post)

    boost_parser = subcommands.add_parser("boost", help="Boost a status")
    boost_parser.add_argument("status_id", help="Status ID to boost")
    boost_parser.set_defaults(func=boost)

    quote_parser = subcommands.add_parser("quote", help="Quote a status")
    quote_parser.add_argument("status_id", help="Status ID to quote")
    quote_parser.add_argument("status", help="Status text to post")
    quote_parser.add_argument(
        "--visibility",
        choices=["public", "unlisted", "private", "direct"],
        default="public",
    )
    quote_parser.set_defaults(func=quote)

    settings_parser = subcommands.add_parser("settings", help="Change preferences")
    settings_parser.set_defaults(func=settings)

    return parser


def menu() -> int:
    while True:
        #print()
        #dialogs.showMessage("""BTMastodon menu
        #1. Login
        #2. View home timeline
        #3. View notifications
        #4. View logged-in account
        #5. Post a status
        #q. Quit""")
        try:                        
            lists = main_menu_lists()
            list_choices = list_menu_choices(lists)
            choices=[
                "Login",
                "Home Timeline",
                "Notifications",
                "Direct Messages",
                *list_choices,
                "Post Status",
                "Mention User",
                "Create List",
                "Settings",
                "Quit",
            ]
            choice = dialogs.request_choice(choices,"Welcome to Mastodon")
            if choice==None:
                return 0
            choice_label = choice.label.strip()
            choice=choice_label.lower()
        except EOFError:
            print("Goodbye.")
            return 0

        if choice in {"q", "quit", "exit"}:
            dialogs.showMessage("Goodbye.")
            return 0

        try:
            if choice == "login":
                instance = dialogs.request_input("Instance host, for example mastodon.social: ")
                login(argparse.Namespace(instance=instance))
            elif choice == "home timeline":
                timeline(argparse.Namespace(limit=20))
            elif choice == "notifications":
                notifications(argparse.Namespace(limit=20))
            elif choice == "direct messages":
                direct_messages(argparse.Namespace(limit=20))
            elif choice in {label.lower() for label in list_choices}:
                selected_list = list_choices[choice_label]
                list_menu(selected_list)
            elif choice == "4":
                whoami(argparse.Namespace())
            elif choice == "post status":
                status = prompt("Status text: ")
                if status!=None:
                    
                    visibility = prompt_visibility()
                    post(
                        argparse.Namespace(
                            status=status,
                            visibility=visibility,
                            prompt_direct_recipient=True,
                            in_reply_to_id=None,
                            quote_status_id=None,
                        )
                    )
            elif choice == "mention user":
                mention_user(argparse.Namespace())
            elif choice == "create list":
                create_list(argparse.Namespace())
            elif choice == "settings":
                settings(argparse.Namespace())
            else:
                print("Unknown choice.")
        except EOFError:
            print("Goodbye.")
            return 0
        except (ApiError, ConfigError, RuntimeError, ValueError, argparse.ArgumentTypeError) as exc:
            print(f"error: {exc}", file=sys.stderr)


def prompt(label: str) -> str | None:
    return dialogs.request_input(label)


def prompt_required(label: str) -> str:
    raw_value = prompt(label)
    if raw_value is None:
        raise ValueError("A value is required")
    value = raw_value.strip()
    if not value:
        raise ValueError("A value is required")
    return value


def main_menu_lists() -> list[dict]:
    global _main_menu_lists_cache
    if _main_menu_lists_cache is not None:
        expires_at, cached_lists = _main_menu_lists_cache
        if expires_at > time.time():
            return cached_lists

    try:
        lists = MastodonClient(load_config()).lists()
    except (ApiError, ConfigError, RuntimeError):
        return []
    _main_menu_lists_cache = (time.time() + CACHE_TTL_SECONDS, lists)
    return lists


def invalidate_main_menu_lists_cache() -> None:
    global _main_menu_lists_cache
    _main_menu_lists_cache = None


def list_menu_choices(lists: list[dict]) -> dict[str, dict]:
    choices: dict[str, dict] = {}
    used_labels: set[str] = set()
    for mastodon_list in lists:
        title = list_title(mastodon_list)
        label = f"List: {title}"
        list_id = mastodon_list_id(mastodon_list)
        if label.lower() in used_labels and list_id:
            label = f"{label} ({list_id})"
        choices[label] = mastodon_list
        used_labels.add(label.lower())
    return choices


def menu_limit() -> int:
    raw = prompt("Limit 1-40, default 20: ").strip()
    if not raw:
        return 20
    return bounded_limit(raw)


def prompt_visibility() -> str:
    choices = ["Public", "Unlisted", "Private", "Direct"]
    return dialogs.request_choice(choices, "Visibility").label.strip().lower()


def prompt_show_toot_numbers(current: bool) -> bool | None:
    current_label = "Show" if current else "Hide"
    choices = ["Show Toot Numbers", "Hide Toot Numbers", BACK_CHOICE]
    choice = dialogs.request_choice(choices, f"Toot Numbers Currently {current_label}")
    if choice is None:
        return None

    value = choice.label.strip().lower()
    if value == BACK_CHOICE.lower():
        return None
    return value == "show toot numbers"


def prompt_show_toot_usernames(current: bool) -> bool | None:
    current_label = "Show" if current else "Hide"
    choices = ["Show Usernames", "Display Names Only", BACK_CHOICE]
    choice = dialogs.request_choice(choices, f"Toot Usernames Currently {current_label}")
    if choice is None:
        return None

    value = choice.label.strip().lower()
    if value == BACK_CHOICE.lower():
        return None
    return value == "show usernames"


def login(args: argparse.Namespace) -> int:
    credentials = register_app(args.instance)
    config = authorize_in_browser(args.instance, credentials)
    path = save_config(config)
    invalidate_hidden_home_account_ids_cache()
    invalidate_main_menu_lists_cache()
    dialogs.showMessage(f"Logged in. Config saved to {path}")
    return 0


def whoami(_: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    account = client.verify_account()
    print(account_name(account))
    note = account.get("note")
    if note:
        from .render import plain_text

        rendered_note = plain_text(str(note))
        if rendered_note:
            print(rendered_note)
    return 0


def timeline(args: argparse.Namespace) -> int:
    config = load_config()
    client = MastodonClient(config)
    statuses = client.home_timeline(args.limit)
    hidden_home_account_ids = exclusive_list_account_ids(client)
    show_home_timeline_menu(
        client,
        statuses,
        args.limit,
        config.show_toot_numbers,
        config.show_toot_usernames,
        hidden_home_account_ids,
    )
    return 0


def notifications(args: argparse.Namespace) -> int:
    config = load_config()
    client = MastodonClient(config)
    items = client.notifications(args.limit)
    enrich_quoted_notifications(client, items)
    show_timeline_menu(
        client,
        [
            timeline_choice_from_notification(item, index, config.show_toot_numbers)
            for index, item in enumerate(items, 1)
        ],
        "Notifications",
        config.show_toot_numbers,
        config.show_toot_usernames,
    )
    return 0


def direct_messages(args: argparse.Namespace) -> int:
    config = load_config()
    client = MastodonClient(config)
    conversations = client.conversations(args.limit)
    show_direct_messages_menu(
        client,
        conversations,
        args.limit,
        config.show_toot_numbers,
        config.show_toot_usernames,
    )
    return 0


def create_list(_: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    title = prompt_required("List name: ")
    mastodon_list = client.create_list(title)
    invalidate_main_menu_lists_cache()
    dialogs.showMessage(f"Created list {list_title(mastodon_list)}. Its users will be hidden from Home.")
    return 0


def list_menu(mastodon_list: dict) -> int:
    config = load_config()
    client = MastodonClient(config)
    while True:
        title = list_title(mastodon_list)
        home_visibility_choice = (
            "Show Users in Home"
            if list_is_exclusive(mastodon_list)
            else "Hide Users from Home"
        )
        choice = dialogs.request_choice(
            ["Read List", "Add User", home_visibility_choice, BACK_CHOICE],
            f"List: {title}",
        )
        if choice is None:
            return 0

        value = choice.label.strip().lower()
        if value == BACK_CHOICE.lower():
            return 0
        if value == "read list":
            show_list_timeline(
                client,
                mastodon_list,
                20,
                config.show_toot_numbers,
                config.show_toot_usernames,
            )
        elif value == "add user":
            add_user_to_list(client, mastodon_list)
        elif value == "hide users from home":
            mastodon_list = set_list_exclusive(client, mastodon_list, True)
        elif value == "show users in home":
            mastodon_list = set_list_exclusive(client, mastodon_list, False)


def show_list_timeline(
    client: MastodonClient,
    mastodon_list: dict,
    limit: int,
    show_numbers: bool,
    show_usernames: bool,
) -> None:
    list_id = mastodon_list_id(mastodon_list)
    if not list_id:
        raise RuntimeError("Could not determine list ID")

    title = list_title(mastodon_list)
    statuses = client.list_timeline(list_id, limit)
    enrich_quoted_statuses(client, statuses)
    items = [
        timeline_choice_from_status(
            status,
            index,
            show_numbers,
            show_usernames,
            author_is_known_followed=True,
        )
        for index, status in enumerate(statuses, 1)
    ]

    while True:
        choice = request_timeline_choice(
            items,
            title,
            include_load_next=True,
            load_next_count=limit,
        )
        if choice is None:
            return
        if choice == LOAD_NEXT_CHOICE:
            max_id = last_page_id(items)
            if not max_id:
                dialogs.showMessage("No more list items to load.")
                continue

            next_statuses = client.list_timeline(list_id, limit, max_id=max_id)
            if not next_statuses:
                dialogs.showMessage("No more list items to load.")
                continue

            enrich_quoted_statuses(client, next_statuses)
            items = [
                timeline_choice_from_status(
                    status,
                    index,
                    show_numbers,
                    show_usernames,
                    author_is_known_followed=True,
                )
                for index, status in enumerate(next_statuses, 1)
            ]
            continue

        open_timeline_choice(client, choice, show_numbers, show_usernames)


def add_user_to_list(client: MastodonClient, mastodon_list: dict) -> None:
    list_id = mastodon_list_id(mastodon_list)
    if not list_id:
        raise RuntimeError("Could not determine list ID")

    selected = prompt_followed_account(client, f"Add User to {list_title(mastodon_list)}")
    if selected is None:
        return

    account_id, acct = account_target(selected)
    if not account_id:
        dialogs.showMessage("This user cannot be added to a list.")
        return

    client.add_account_to_list(list_id, account_id)
    if list_is_exclusive(mastodon_list):
        add_hidden_home_account_id_to_cache(account_id)
    user = account_mention(acct) or account_name(selected)
    dialogs.showMessage(f"Added {user} to {list_title(mastodon_list)}.")


def set_list_exclusive(
    client: MastodonClient,
    mastodon_list: dict,
    exclusive: bool,
) -> dict:
    list_id = mastodon_list_id(mastodon_list)
    if not list_id:
        raise RuntimeError("Could not determine list ID")

    updated_list = client.update_list(
        list_id,
        list_title(mastodon_list),
        exclusive,
        list_replies_policy(mastodon_list),
    )
    invalidate_hidden_home_account_ids_cache()
    invalidate_main_menu_lists_cache()
    if exclusive:
        dialogs.showMessage(f"Users in {list_title(updated_list)} will be hidden from Home.")
    else:
        dialogs.showMessage(f"Users in {list_title(updated_list)} will be shown in Home.")
    return updated_list


def post(args: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    status_text = args.status
    quote_status_id = getattr(args, "quote_status_id", None)
    if quote_status_id:
        status_text = quote_status_text(client, quote_status_id, status_text)
    status_text = ensure_direct_status_recipient(
        client,
        status_text,
        args.visibility,
        bool(getattr(args, "prompt_direct_recipient", False)),
    )

    status = client.post_status(
        status_text,
        args.visibility,
        getattr(args, "in_reply_to_id", None),
    )
    dialogs.showMessage(posted_status_message("Posted", status))
    return 0


def quote(args: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    status_text = quote_status_text(client, args.status_id, args.status)
    status_text = ensure_direct_status_recipient(
        client,
        status_text,
        args.visibility,
        bool(getattr(args, "prompt_direct_recipient", False)),
    )
    status = client.post_status(
        status_text,
        args.visibility,
    )
    dialogs.showMessage(posted_status_message("Quoted", status))
    return 0


def boost(args: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    status = client.boost_status(args.status_id)
    dialogs.showMessage(f"Boosted:\n{render_status(status)}")
    return 0


def mention_user(_: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    account = client.verify_account()
    account_id = str(account.get("id") or "")
    if not account_id:
        raise RuntimeError("Could not determine your account ID")

    following = client.account_following(account_id)
    if not following:
        dialogs.showMessage("No followed users to show.")
        return 0

    mentions = prompt_mention_accounts(following)
    if not mentions:
        return 0

    mention_text = " ".join(mentions)
    text = prompt(f"Post {mention_text}: ")
    if text is None:
        return 0

    status = prepend_missing_mentions(text, mentions)
    visibility = prompt_visibility()
    post(
        argparse.Namespace(
            status=status,
            visibility=visibility,
            prompt_direct_recipient=True,
            in_reply_to_id=None,
            quote_status_id=None,
        )
    )
    return 0


def prompt_mention_accounts(following: list[dict]) -> list[str]:
    mentions: list[str] = []
    while True:
        mention = prompt_mention_account(following, "Mention User")
        if not mention:
            return []

        if mention.lower() in {selected.lower() for selected in mentions}:
            dialogs.showMessage(f"{mention} is already selected.")
        else:
            mentions.append(mention)

        choice = dialogs.request_choice(
            ["Add Another User", "Compose Toot", BACK_CHOICE],
            selected_mentions_title(mentions),
        )
        if choice is None:
            return []

        value = choice.label.strip().lower()
        if value == "compose toot":
            return mentions
        if value == BACK_CHOICE.lower():
            return []


def selected_mentions_title(mentions: list[str]) -> str:
    count = len(mentions)
    if count == 1:
        return "1 Mention Selected"
    return f"{count} Mentions Selected"


def prompt_mention_account(following: list[dict], title: str) -> str:
    selected = prompt_account_from_following(following, title)
    if selected is None:
        return ""

    _, acct = account_target(selected)
    mention = account_mention(acct)
    if not mention:
        dialogs.showMessage("This user cannot be mentioned.")
        return ""
    return mention


def prompt_followed_account(client: MastodonClient, title: str) -> dict | None:
    account = client.verify_account()
    account_id = str(account.get("id") or "")
    if not account_id:
        raise RuntimeError("Could not determine your account ID")

    following = client.account_following(account_id)
    if not following:
        dialogs.showMessage("No followed users to show.")
        return None

    return prompt_account_from_following(following, title)


def prompt_account_from_following(following: list[dict], title: str) -> dict | None:
    search = prompt("Search followed user: ")
    if search is None:
        return None

    matches = filter_accounts_by_display_name(following, search)
    if not matches:
        dialogs.showMessage("No matching followed users.")
        return None

    return request_account_choice(matches, title)


def filter_accounts_by_display_name(accounts: list[dict], search: str) -> list[dict]:
    query = search.strip().lower()
    if not query:
        return accounts

    return [
        account
        for account in accounts
        if query in account_search_text(account).lower()
    ]


def account_display_name(account: dict) -> str:
    return plain_text(str(account.get("display_name") or ""))


def account_search_text(account: dict) -> str:
    return " ".join(
        value
        for value in (
            account_display_name(account),
            str(account.get("acct") or ""),
            str(account.get("username") or ""),
        )
        if value
    )


def request_account_choice(accounts: list[dict], title: str) -> dict | None:
    by_label: dict[str, dict] = {}
    for index, account in enumerate(accounts, 1):
        label = f"{index}. {account_name(account)}"
        by_label[label] = account

    choices = list(by_label)
    choices.append(BACK_CHOICE)
    choice = dialogs.request_choice(choices, title)
    if choice is None:
        return None

    label = choice.label
    if label.strip().lower() == BACK_CHOICE.lower():
        return None
    return by_label.get(label)


def mastodon_list_id(mastodon_list: dict) -> str:
    return str(mastodon_list.get("id") or "").strip()


def list_title(mastodon_list: dict) -> str:
    return str(mastodon_list.get("title") or "Untitled List").strip()


def list_is_exclusive(mastodon_list: dict) -> bool:
    return bool(mastodon_list.get("exclusive"))


def list_replies_policy(mastodon_list: dict) -> str:
    return str(mastodon_list.get("replies_policy") or "").strip()


def posted_status_message(action: str, status: dict, quoted_status_id: str | None = None) -> str:
    message = f"{action}:\n{render_status(status)}"
    if quoted_status_id and not has_quote_reference(status):
        message += (
            "\n\nQuote warning: the server did not return quote data. "
            "It may not support quote posts or may have ignored the quote request."
        )
    return message


def quote_status_text(client: MastodonClient, quoted_status_id: str, text: str) -> str:
    quoted = client.status(quoted_status_id)
    url = status_url(quoted)
    if not url:
        raise RuntimeError("Quoted status does not have a URL")
    return f"RE: {url}\n\n{text}"


def status_url(status: dict) -> str:
    source = status.get("reblog") or status
    return str(source.get("url") or source.get("uri") or "").strip()


def enrich_quoted_statuses(client: MastodonClient, statuses: list[dict]) -> None:
    for status in statuses:
        source = status.get("reblog") or status
        if not isinstance(source, dict):
            continue
        if source.get("quoted_status") or source.get("quote_status"):
            continue

        quote_url = status_quote_url(source)
        if not quote_url:
            continue

        try:
            quoted = client.resolve_status_url(quote_url)
        except (ApiError, RuntimeError):
            quoted = None
        if quoted:
            source["quoted_status"] = quoted


def enrich_quoted_notifications(client: MastodonClient, notifications: list[dict]) -> None:
    statuses: list[dict] = []
    for notification in notifications:
        status = notification.get("status")
        if isinstance(status, dict):
            statuses.append(status)
    enrich_quoted_statuses(client, statuses)


def exclusive_list_account_ids(client: MastodonClient) -> set[str]:
    cached_account_ids = load_hidden_home_account_ids_cache()
    if cached_account_ids is not None:
        return cached_account_ids

    account_ids: set[str] = set()
    for mastodon_list in client.lists():
        if not isinstance(mastodon_list, dict) or not list_is_exclusive(mastodon_list):
            continue

        list_id = mastodon_list_id(mastodon_list)
        if not list_id:
            continue

        for account in exclusive_list_accounts(client, list_id):
            account_id, _ = account_target(account)
            if account_id:
                account_ids.add(account_id)
    save_hidden_home_account_ids_cache(account_ids)
    return account_ids


def hidden_home_account_ids_cache_path():
    return config_dir() / "hidden_home_account_ids.json"


def load_hidden_home_account_ids_cache() -> set[str] | None:
    try:
        raw = json.loads(hidden_home_account_ids_cache_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if not isinstance(raw, dict):
        return None
    try:
        expires_at = float(raw.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= time.time():
        return None

    account_ids = raw.get("account_ids")
    if not isinstance(account_ids, list):
        return None
    return {str(account_id) for account_id in account_ids if str(account_id)}


def save_hidden_home_account_ids_cache(account_ids: set[str]) -> None:
    path = hidden_home_account_ids_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "expires_at": time.time() + CACHE_TTL_SECONDS,
        "account_ids": sorted(account_ids),
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def add_hidden_home_account_id_to_cache(account_id: str) -> None:
    cached_account_ids = load_hidden_home_account_ids_cache()
    if cached_account_ids is None:
        return

    cached_account_ids.add(account_id)
    save_hidden_home_account_ids_cache(cached_account_ids)


def invalidate_hidden_home_account_ids_cache() -> None:
    try:
        hidden_home_account_ids_cache_path().unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def exclusive_list_accounts(client: MastodonClient, list_id: str) -> list[dict]:
    accounts: list[dict] = []
    max_id: str | None = None
    while True:
        page = client.list_accounts(list_id, max_id=max_id)
        if not page:
            return accounts

        accounts.extend(page)
        max_id = last_account_id(page)
        if not max_id:
            return accounts


def last_account_id(accounts: list[dict]) -> str:
    for account in reversed(accounts):
        account_id, _ = account_target(account)
        if account_id:
            return account_id
    return ""


def filter_home_statuses(statuses: list[dict], hidden_account_ids: set[str]) -> list[dict]:
    if not hidden_account_ids:
        return statuses

    return [
        status
        for status in statuses
        if status_author_id(status) not in hidden_account_ids
    ]


def settings(_: argparse.Namespace) -> int:
    config = load_config()
    choices = ["Toot Numbers", "Toot Usernames", "Clear Cache", BACK_CHOICE]
    choice = dialogs.request_choice(choices, "Settings")
    if choice is None:
        return 0

    value = choice.label.strip().lower()
    if value == BACK_CHOICE.lower():
        return 0
    if value == "toot numbers":
        show_toot_numbers = prompt_show_toot_numbers(config.show_toot_numbers)
        if show_toot_numbers is None:
            return 0

        save_config(replace(config, show_toot_numbers=show_toot_numbers))
        state = "shown" if show_toot_numbers else "hidden"
        dialogs.showMessage(f"Toot numbers will be {state}.")
    elif value == "toot usernames":
        show_toot_usernames = prompt_show_toot_usernames(config.show_toot_usernames)
        if show_toot_usernames is None:
            return 0

        save_config(replace(config, show_toot_usernames=show_toot_usernames))
        state = "shown" if show_toot_usernames else "hidden"
        dialogs.showMessage(f"Toot usernames will be {state}.")
    elif value == "clear cache":
        clear_cache()
    return 0


def clear_cache() -> None:
    invalidate_hidden_home_account_ids_cache()
    invalidate_main_menu_lists_cache()
    dialogs.showMessage("Cache cleared.")


def timeline_choice_from_status(
    status: dict,
    index: int,
    show_numbers: bool = True,
    show_usernames: bool = True,
    author_is_known_followed: bool = False,
) -> TimelineChoice:
    reply_to_id, reply_to_acct = status_reply_target(status)
    author_id, author_acct = status_author_target(status)
    author_profile_url = status_author_profile_url(status)
    source = status.get("reblog") or status
    return TimelineChoice(
        render_status(status, index if show_numbers else None, show_usernames),
        status_links(status),
        reply_to_id,
        reply_to_acct,
        reply_to_id,
        reply_to_id,
        status_url(source),
        str(status.get("id") or ""),
        author_id,
        author_acct,
        author_profile_url,
        author_is_known_followed,
        "",
        status_reply_mentions(status),
    )


def timeline_choice_from_notification(
    notification: dict,
    index: int,
    show_numbers: bool = True,
) -> TimelineChoice:
    reply_to_id, reply_to_acct = notification_reply_target(notification)
    author_id, author_acct = notification_author_target(notification)
    author_profile_url = notification_author_profile_url(notification)
    return TimelineChoice(
        render_notification(notification, index if show_numbers else None),
        notification_links(notification),
        reply_to_id,
        reply_to_acct,
        reply_to_id,
        reply_to_id,
        "",
        "",
        author_id,
        author_acct,
        author_profile_url,
        False,
        "",
        notification_reply_mentions(notification),
    )


def timeline_choice_from_conversation(
    conversation: dict,
    index: int,
    show_numbers: bool = True,
    show_usernames: bool = True,
) -> TimelineChoice:
    status = conversation.get("last_status")
    if not isinstance(status, dict):
        participants = conversation_participants(conversation, show_usernames)
        prefix = f"{index}. " if show_numbers else ""
        label = f"{prefix}Direct message with {participants}" if participants else f"{prefix}Direct message"
        return TimelineChoice(label, [], "", "", "", "", "", str(conversation.get("id") or ""), "", "", "")

    item = timeline_choice_from_status(status, index, show_numbers, show_usernames)
    participants = conversation_participants(conversation, show_usernames)
    recipient_acct = conversation_recipient_acct(conversation)
    if participants:
        label = f"Direct message with {participants}\n{item.label}"
    else:
        label = f"Direct message\n{item.label}"
    return replace(
        item,
        label=label,
        reply_to_acct=recipient_acct or item.reply_to_acct,
        reply_mentions=conversation_reply_mentions(conversation) or item.reply_mentions,
        boost_id="",
        quote_id="",
        quote_url="",
        page_id=str(conversation.get("id") or ""),
        default_visibility="direct",
    )


def conversation_participants(conversation: dict, show_usernames: bool = True) -> str:
    accounts = conversation.get("accounts")
    if not isinstance(accounts, list):
        return ""

    names = [
        account_name(account, show_usernames)
        for account in accounts
        if isinstance(account, dict)
    ]
    return ", ".join(name for name in names if name)


def conversation_recipient_acct(conversation: dict) -> str:
    accounts = conversation.get("accounts")
    if not isinstance(accounts, list):
        return ""

    for account in accounts:
        if isinstance(account, dict):
            acct = str(account.get("acct") or account.get("username") or "").strip()
            if acct:
                return acct
    return ""


def status_reply_mentions(status: dict) -> list[str]:
    source = status.get("reblog") or status
    mentions: list[str] = []

    _, author_acct = account_target(source.get("account"))
    add_unique_mention(mentions, account_mention(author_acct))

    status_mentions = source.get("mentions")
    if isinstance(status_mentions, list):
        for mention in status_mentions:
            if not isinstance(mention, dict):
                continue
            acct = str(mention.get("acct") or mention.get("username") or "")
            add_unique_mention(mentions, account_mention(acct))

    return mentions


def notification_reply_mentions(notification: dict) -> list[str]:
    status = notification.get("status")
    if isinstance(status, dict):
        return status_reply_mentions(status)
    return []


def conversation_reply_mentions(conversation: dict) -> list[str]:
    accounts = conversation.get("accounts")
    if not isinstance(accounts, list):
        return []

    mentions: list[str] = []
    for account in accounts:
        _, acct = account_target(account)
        add_unique_mention(mentions, account_mention(acct))
    return mentions


def add_unique_mention(mentions: list[str], mention: str) -> None:
    if mention and mention.lower() not in {item.lower() for item in mentions}:
        mentions.append(mention)


def show_home_timeline_menu(
    client: MastodonClient,
    statuses: list[dict],
    limit: int,
    show_numbers: bool,
    show_usernames: bool,
    hidden_account_ids: set[str] | None = None,
) -> None:
    hidden_account_ids = hidden_account_ids or set()
    next_max_id = last_status_id(statuses)
    statuses = filter_home_statuses(statuses, hidden_account_ids)
    enrich_quoted_statuses(client, statuses)
    items = [
        timeline_choice_from_status(
            status,
            index,
            show_numbers,
            show_usernames,
            author_is_known_followed=not bool(status.get("reblog")),
        )
        for index, status in enumerate(statuses, 1)
    ]

    while True:
        choice = request_timeline_choice(
            items,
            "Home Timeline",
            include_load_next=True,
            load_next_count=limit,
        )
        if choice is None:
            return
        if choice == LOAD_NEXT_CHOICE:
            if not next_max_id:
                dialogs.showMessage("No more timeline items to load.")
                continue

            next_statuses = client.home_timeline(limit, max_id=next_max_id)
            next_max_id = last_status_id(next_statuses)
            next_statuses = filter_home_statuses(next_statuses, hidden_account_ids)
            if not next_statuses:
                dialogs.showMessage("No more timeline items to load.")
                continue

            enrich_quoted_statuses(client, next_statuses)
            items = [
                timeline_choice_from_status(
                    status,
                    index,
                    show_numbers,
                    show_usernames,
                    author_is_known_followed=not bool(status.get("reblog")),
                )
                for index, status in enumerate(next_statuses, 1)
            ]
            continue

        open_timeline_choice(client, choice, show_numbers, show_usernames)


def show_direct_messages_menu(
    client: MastodonClient,
    conversations: list[dict],
    limit: int,
    show_numbers: bool,
    show_usernames: bool,
) -> None:
    items = [
        timeline_choice_from_conversation(conversation, index, show_numbers, show_usernames)
        for index, conversation in enumerate(conversations, 1)
        if isinstance(conversation, dict)
    ]

    while True:
        choice = request_timeline_choice(
            items,
            "Direct Messages",
            include_load_next=True,
            load_next_count=limit,
        )
        if choice is None:
            return
        if choice == LOAD_NEXT_CHOICE:
            max_id = last_page_id(items)
            if not max_id:
                dialogs.showMessage("No more direct messages to load.")
                continue

            next_conversations = client.conversations(limit, max_id=max_id)
            if not next_conversations:
                dialogs.showMessage("No more direct messages to load.")
                continue

            items = [
                timeline_choice_from_conversation(conversation, index, show_numbers, show_usernames)
                for index, conversation in enumerate(next_conversations, 1)
                if isinstance(conversation, dict)
            ]
            continue

        open_timeline_choice(client, choice, show_numbers, show_usernames)


def show_timeline_menu(
    client: MastodonClient,
    items: list[TimelineChoice],
    title: str,
    show_numbers: bool = True,
    show_usernames: bool = True,
) -> None:
    while True:
        choice = request_timeline_choice(items, title)
        if choice is None:
            return
        open_timeline_choice(client, choice, show_numbers, show_usernames)


def request_timeline_choice(
    items: list[TimelineChoice],
    title: str,
    include_load_next: bool = False,
    load_next_count: int | None = None,
) -> TimelineChoice | str | None:
    if not items and not include_load_next:
        dialogs.showMessage(f"No {title.lower()} to show.")
        return None

    by_label = {(item.label or "(empty)"): item for item in items}
    choices = list(by_label)
    if include_load_next:
        if load_next_count is None:
            choices.append(LOAD_NEXT_CHOICE)
        else:
            choices.append(f"{LOAD_NEXT_CHOICE} {load_next_count}")
    choices.append(BACK_CHOICE)

    choice = dialogs.request_choice(choices, title)
    if choice is None:
        return None
    label = choice.label
    normalized = label.strip().lower()
    if normalized == BACK_CHOICE.lower():
        return None
    if normalized.startswith(LOAD_NEXT_CHOICE.lower()):
        return LOAD_NEXT_CHOICE
    return by_label.get(label)


def last_page_id(items: list[TimelineChoice]) -> str:
    for item in reversed(items):
        if item.page_id:
            return item.page_id
    return ""


def last_status_id(statuses: list[dict]) -> str:
    for status in reversed(statuses):
        status_id = str(status.get("id") or "")
        if status_id:
            return status_id
    return ""


def open_timeline_choice(
    client: MastodonClient,
    item: TimelineChoice,
    show_numbers: bool = True,
    show_usernames: bool = True,
) -> None:
    actions = []
    author_relationship = toot_author_relationship(client, item)
    if item.links:
        actions.append("Open Link")
    if item.reply_to_id:
        actions.append("Reply All")
        actions.append("Reply")
    if item.boost_id:
        actions.append("Boost")
    if item.quote_id:
        actions.append("Quote")
    if item.author_profile_url:
        actions.append("View Profile")
    author_is_followed = should_offer_unfollow_author(author_relationship) or item.author_is_known_followed
    if should_offer_follow_author(author_relationship, author_is_followed):
        actions.append("Follow Author")
    elif author_is_followed:
        actions.append("Unfollow Author")
    actions.extend(["View Conversation", BACK_CHOICE])

    choice = dialogs.request_choice(actions, "Toot Actions")
    if choice is None:
        return
    choice = choice.label.strip().lower()
    if choice == "open link":
        open_timeline_links(item.links)
    elif choice == "reply":
        reply_to_toot(client, item, reply_all=False)
    elif choice == "reply all":
        reply_to_toot(client, item, reply_all=True)
    elif choice == "boost":
        boost_toot(client, item)
    elif choice == "quote":
        quote_toot(item)
    elif choice == "view profile":
        open_url_in_desktop(item.author_profile_url)
    elif choice == "follow author":
        follow_toot_author(client, item)
    elif choice == "unfollow author":
        unfollow_toot_author(client, item)
    elif choice == "view conversation":
        view_conversation(client, item, show_numbers, show_usernames)


def toot_author_relationship(client: MastodonClient, item: TimelineChoice) -> dict:
    if not item.author_id:
        return {}

    return client.account_relationship(item.author_id)


def should_offer_follow_author(relationship: dict, author_is_followed: bool = False) -> bool:
    if author_is_followed:
        return False
    if not relationship:
        return False
    return not bool(relationship.get("following") or relationship.get("requested"))


def should_offer_unfollow_author(relationship: dict) -> bool:
    return bool(relationship.get("following"))


def open_timeline_links(links: list[str]) -> None:
    if not links:
        return
    if len(links) == 1:
        open_url_in_desktop(links[0])
        return

    choices = links + [BACK_CHOICE]
    while True:
        choice = dialogs.request_choice(choices, "Open Link")
        if choice is None:
            return
        choice = choice.label
        if choice.strip().lower() == BACK_CHOICE.lower():
            return
        
        open_url_in_desktop(choice)


def reply_to_toot(client: MastodonClient, item: TimelineChoice, reply_all: bool = True) -> None:
    if not item.reply_to_id:
        dialogs.showMessage("This item cannot be replied to.")
        return

    if reply_all:
        mentions = item.reply_mentions or [account_mention(item.reply_to_acct)]
    else:
        mentions = [account_mention(item.reply_to_acct)]
    mentions = [mention for mention in mentions if mention]
    mentions = exclude_own_mentions(mentions, client.verify_account())
    mention_text = " ".join(mentions)
    prompt_label = "Reply All" if reply_all else "Reply"
    reply = prompt(f"{prompt_label} {mention_text}: " if mention_text else f"{prompt_label}: ")
    if reply==None:
        return
    reply = prepend_missing_mentions(reply, mentions)
    visibility = item.default_visibility or prompt_visibility()
    post(
        argparse.Namespace(
            status=reply,
            visibility=visibility,
            prompt_direct_recipient=False,
            in_reply_to_id=item.reply_to_id,
            quote_status_id=None,
        )
    )


def boost_toot(client: MastodonClient, item: TimelineChoice) -> None:
    if not item.boost_id:
        dialogs.showMessage("This item cannot be boosted.")
        return

    status = client.boost_status(item.boost_id)
    dialogs.showMessage(f"Boosted:\n{render_status(status)}")


def quote_toot(item: TimelineChoice) -> None:
    if not item.quote_url:
        dialogs.showMessage("This item cannot be quoted.")
        return

    text = prompt("Quote text: ")
    if text is None:
        return
    visibility = prompt_visibility()
    post(
        argparse.Namespace(
            status=f"RE: {item.quote_url}\n\n{text}",
            visibility=visibility,
            prompt_direct_recipient=True,
            in_reply_to_id=None,
            quote_status_id=None,
        )
    )


def follow_toot_author(client: MastodonClient, item: TimelineChoice) -> None:
    if not item.author_id:
        dialogs.showMessage("This item does not have an author to follow.")
        return

    relationship = client.follow_account(item.author_id)
    author = account_mention(item.author_acct) or "author"
    if relationship.get("following"):
        dialogs.showMessage(f"Followed {author}.")
    elif relationship.get("requested"):
        dialogs.showMessage(f"Follow request sent to {author}.")
    else:
        dialogs.showMessage(f"Followed {author}.")


def unfollow_toot_author(client: MastodonClient, item: TimelineChoice) -> None:
    if not item.author_id:
        dialogs.showMessage("This item does not have an author to unfollow.")
        return

    client.unfollow_account(item.author_id)
    author = account_mention(item.author_acct) or "author"
    dialogs.showMessage(f"Unfollowed {author}.")


def account_mention(acct: str) -> str:
    acct = acct.strip().lstrip("@")
    if not acct:
        return ""
    return f"@{acct}"


def exclude_own_mentions(mentions: list[str], account: dict) -> list[str]:
    own_mentions = own_account_mentions(account)
    return [
        mention
        for mention in mentions
        if mention.lower() not in own_mentions
    ]


def own_account_mentions(account: dict) -> set[str]:
    mentions: set[str] = set()
    for key in ("acct", "username"):
        mention = account_mention(str(account.get(key) or ""))
        if mention:
            mentions.add(mention.lower())
    return mentions


def status_author_target(status: dict) -> tuple[str, str]:
    source = status.get("reblog") or status
    return account_target(source.get("account"))


def status_author_id(status: dict) -> str:
    author_id, _ = status_author_target(status)
    return author_id


def notification_author_target(notification: dict) -> tuple[str, str]:
    return account_target(notification.get("account"))


def status_author_profile_url(status: dict) -> str:
    source = status.get("reblog") or status
    return account_profile_url(source.get("account"))


def notification_author_profile_url(notification: dict) -> str:
    return account_profile_url(notification.get("account"))


def account_target(account: object) -> tuple[str, str]:
    if not isinstance(account, dict):
        return "", ""
    return (
        str(account.get("id") or ""),
        str(account.get("acct") or account.get("username") or ""),
    )


def account_profile_url(account: object) -> str:
    if not isinstance(account, dict):
        return ""
    return str(account.get("url") or account.get("uri") or "")


def reply_mentions_account(reply: str, mention: str) -> bool:
    return mention.lower() in reply.lower().split()


def prepend_missing_mentions(status_text: str, mentions: list[str]) -> str:
    missing = [
        mention
        for mention in mentions
        if not reply_mentions_account(status_text, mention)
    ]
    if not missing:
        return status_text
    return f"{' '.join(missing)} {status_text}".strip()


def ensure_direct_status_recipient(
    client: MastodonClient,
    status_text: str,
    visibility: str,
    prompt_for_recipient: bool = False,
) -> str:
    if visibility != "direct" or status_has_mention(status_text):
        return status_text
    if not prompt_for_recipient:
        raise ValueError("Direct messages must mention at least one recipient")

    mention = prompt_direct_recipient(client)
    if not mention:
        raise ValueError("Direct messages must mention at least one recipient")
    return f"{mention} {status_text}".strip()


def status_has_mention(status_text: str) -> bool:
    return bool(MENTION_RE.search(status_text))


def prompt_direct_recipient(client: MastodonClient) -> str:
    account = client.verify_account()
    account_id = str(account.get("id") or "")
    if not account_id:
        raise RuntimeError("Could not determine your account ID")

    following = client.account_following(account_id)
    if not following:
        raise ValueError("Direct messages require a mentioned recipient")

    search = prompt("Search display name: ")
    if search is None:
        return ""

    matches = filter_accounts_by_display_name(following, search)
    if not matches:
        dialogs.showMessage("No matching followed users.")
        return ""

    selected = request_account_choice(matches, "Direct Message Recipient")
    if selected is None:
        return ""

    _, acct = account_target(selected)
    return account_mention(acct)


def view_conversation(
    client: MastodonClient,
    item: TimelineChoice,
    show_numbers: bool = True,
    show_usernames: bool = True,
) -> None:
    if not item.reply_to_id:
        dialogs.showMessage(item.label)
        return

    context = client.status_context(item.reply_to_id)
    ancestors = context_statuses(context, "ancestors")
    descendants = context_statuses(context, "descendants")
    selected_status = client.status(item.reply_to_id)
    enrich_quoted_statuses(client, ancestors)
    enrich_quoted_statuses(client, [selected_status])
    enrich_quoted_statuses(client, descendants)

    conversation_items: list[TimelineChoice] = []
    conversation_items.extend(
        timeline_choice_from_status(status, index, show_numbers, show_usernames)
        for index, status in enumerate(ancestors, 1)
    )
    conversation_items.append(
        timeline_choice_from_status(
            selected_status,
            len(conversation_items) + 1,
            show_numbers,
            show_usernames,
            author_is_known_followed=item.author_is_known_followed,
        )
    )
    conversation_items.extend(
        timeline_choice_from_status(status, index, show_numbers, show_usernames)
        for index, status in enumerate(descendants, len(conversation_items) + 1)
    )

    show_timeline_menu(
        client,
        conversation_items,
        "Conversation",
        show_numbers,
        show_usernames,
    )


def context_statuses(context: dict, key: str) -> list[dict]:
    statuses = context.get(key)
    if not isinstance(statuses, list):
        return []
    return [status for status in statuses if isinstance(status, dict)]


def open_url_in_desktop(url: str) -> None:
    try:
        from BTSpeak import terminal, web_search

        dialogs.stopActivityIndicator()
        dialogs.clearScreen()
        dialogs.show_message("Opening link in desktop mode.")
        terminal.switch_and_wait(terminal.TARGET_DESKTOP)
        web_search.open_url(url)
    except ImportError:
        print(url)


def bounded_limit(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be a number") from exc
    if value < 1 or value > 40:
        raise argparse.ArgumentTypeError("limit must be between 1 and 40")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
