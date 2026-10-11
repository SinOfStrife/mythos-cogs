# LinkQuoter

Quotes Discord message links in chat with embeds, attachments, and delete buttons.

---

## Installation

1. Install the cog:
```ini
[p]cog install strife-cogs linkquoter
```

2. Load the cog:
```ini
[p]load linkquoter
```

---

## Features

* Quotes message links automatically in chat, or manually via `[p]linkquote`.
* Works by replying to a message and typing `[p]linkquote`.
* Keeps any text sent alongside the link at the top of the quote.
* Adds a delete button (🗑️) so the person who posted it or a mod can remove it.
* Shows thread and parent channel names in the footer.
* Respects channel view permissions and blocks NSFW messages from quoting into SFW channels.
* All cross-server and auto quote features are **OFF** by default.

---

## Commands

* `[p]linkquote [link]` *(aliases: `lq`, `quote`)* — Quote a message link or reply.
* `[p]linkquoteset toggle` — Turn auto-quoting on or off.
* `[p]linkquoteset delete` — Toggle deleting the original link message.
* `[p]linkquoteset mode <off|whitelist|public>` — Set cross-server quote mode.
* `[p]linkquoteset whitelist <server_id>` — Add or remove a server from the whitelist.
* `[p]linkquoteset ignorechannel <channel>` — Ignore or unignore a channel or thread.
* `[p]linkquoteset ignorerole <role>` — Ignore or unignore a role.
* `[p]linkquoteset settings` — View current settings.

---

## Cross-Server Modes

Both servers must allow cross-server quoting for quotes between them to work.

| Source Server (where message is from) | Destination Server (where link is sent) | Status |
| :--- | :--- | :---: |
| **`off`** | Any | Blocked |
| Any | **`off`** | Blocked |
| **`whitelist`** | **`whitelist`** | Allowed if both servers whitelist each other |
| **`whitelist`** | **`public`** | Allowed if Source whitelists Destination |
| **`public`** | **`whitelist`** | Allowed if Destination whitelists Source |
| **`public`** | **`public`** | Allowed |

---

## Credits & License

* Adapted from code by [PhenoM4n4n](https://github.com/phenom4n4n) and [AAA3A](https://github.com/AAA3A-AAA3A).
* MIT License.
