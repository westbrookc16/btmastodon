from BTSpeak import dialogs 
#from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace

from config import ConfigError, load_config, save_config
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
    author_is_known_followed: bool = False


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
            choices=[
                "Login",
                "Home Timeline",
                "Notifications",
                "Post Status",
                "Mention User",
                "Settings",
                "Quit",
            ]
            choice = dialogs.request_choice(choices,"Welcome to Mastodon")
            if choice==None:
                return 0
            choice=choice.label.strip().lower()
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
            elif choice == "4":
                whoami(argparse.Namespace())
            elif choice == "post status":
                status = prompt("Status text: ")
                if status!=None:
                    
                    visibility = prompt_visibility()
                    post(argparse.Namespace(status=status, visibility=visibility))
            elif choice == "mention user":
                mention_user(argparse.Namespace())
            elif choice == "settings":
                settings(argparse.Namespace())
            else:
                print("Unknown choice.")
        except EOFError:
            print("Goodbye.")
            return 0
        except (ApiError, ConfigError, RuntimeError, ValueError, argparse.ArgumentTypeError) as exc:
            print(f"error: {exc}", file=sys.stderr)


def prompt(label: str) -> str:
    return dialogs.request_input(label)


def prompt_required(label: str) -> str:
    value = prompt(label).strip()
    if not value:
        raise ValueError("A value is required")
    return value


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
    show_home_timeline_menu(
        client,
        statuses,
        args.limit,
        config.show_toot_numbers,
        config.show_toot_usernames,
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


def post(args: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    status_text = args.status
    quote_status_id = getattr(args, "quote_status_id", None)
    if quote_status_id:
        status_text = quote_status_text(client, quote_status_id, status_text)

    status = client.post_status(
        status_text,
        args.visibility,
        getattr(args, "in_reply_to_id", None),
    )
    dialogs.showMessage(posted_status_message("Posted", status))
    return 0


def quote(args: argparse.Namespace) -> int:
    client = MastodonClient(load_config())
    status = client.post_status(
        quote_status_text(client, args.status_id, args.status),
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

    search = prompt("Search display name: ")
    if search is None:
        return 0

    matches = filter_accounts_by_display_name(following, search)
    if not matches:
        dialogs.showMessage("No matching followed users.")
        return 0

    selected = request_account_choice(matches, "Mention User")
    if selected is None:
        return 0

    _, acct = account_target(selected)
    mention = account_mention(acct)
    if not mention:
        dialogs.showMessage("This user cannot be mentioned.")
        return 0

    text = prompt(f"Post {mention}: ")
    if text is None:
        return 0
    status = text
    if not reply_mentions_account(status, mention):
        status = f"{mention} {status}".strip()
    visibility = prompt_visibility()
    post(
        argparse.Namespace(
            status=status,
            visibility=visibility,
            in_reply_to_id=None,
            quote_status_id=None,
        )
    )
    return 0


def filter_accounts_by_display_name(accounts: list[dict], search: str) -> list[dict]:
    query = search.strip().lower()
    if not query:
        return accounts

    return [
        account
        for account in accounts
        if query in account_display_name(account).lower()
    ]


def account_display_name(account: dict) -> str:
    return plain_text(str(account.get("display_name") or ""))


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


def settings(_: argparse.Namespace) -> int:
    config = load_config()
    choices = ["Toot Numbers", "Toot Usernames", BACK_CHOICE]
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
    return 0


def timeline_choice_from_status(
    status: dict,
    index: int,
    show_numbers: bool = True,
    show_usernames: bool = True,
    author_is_known_followed: bool = False,
) -> TimelineChoice:
    reply_to_id, reply_to_acct = status_reply_target(status)
    author_id, author_acct = status_author_target(status)
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
        author_is_known_followed,
    )


def timeline_choice_from_notification(
    notification: dict,
    index: int,
    show_numbers: bool = True,
) -> TimelineChoice:
    reply_to_id, reply_to_acct = notification_reply_target(notification)
    author_id, author_acct = notification_author_target(notification)
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
    )


def show_home_timeline_menu(
    client: MastodonClient,
    statuses: list[dict],
    limit: int,
    show_numbers: bool,
    show_usernames: bool,
) -> None:
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
            max_id = last_page_id(items)
            if not max_id:
                dialogs.showMessage("No more timeline items to load.")
                continue

            next_statuses = client.home_timeline(limit, max_id=max_id)
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
        actions.append("Reply")
    if item.boost_id:
        actions.append("Boost")
    if item.quote_id:
        actions.append("Quote")
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
        reply_to_toot(item)
    elif choice == "boost":
        boost_toot(client, item)
    elif choice == "quote":
        quote_toot(item)
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


def reply_to_toot(item: TimelineChoice) -> None:
    if not item.reply_to_id:
        dialogs.showMessage("This item cannot be replied to.")
        return

    mention = account_mention(item.reply_to_acct)
    reply = prompt(f"Reply {mention}: " if mention else "Reply: ")
    if reply==None:
        return
    if mention and not reply_mentions_account(reply, mention):
        reply = f"{mention} {reply}"
    visibility = prompt_visibility()
    post(
        argparse.Namespace(
            status=reply,
            visibility=visibility,
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


def status_author_target(status: dict) -> tuple[str, str]:
    source = status.get("reblog") or status
    return account_target(source.get("account"))


def notification_author_target(notification: dict) -> tuple[str, str]:
    status = notification.get("status")
    if isinstance(status, dict):
        return status_author_target(status)
    return "", ""


def account_target(account: object) -> tuple[str, str]:
    if not isinstance(account, dict):
        return "", ""
    return (
        str(account.get("id") or ""),
        str(account.get("acct") or account.get("username") or ""),
    )


def reply_mentions_account(reply: str, mention: str) -> bool:
    return mention.lower() in reply.lower().split()


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
