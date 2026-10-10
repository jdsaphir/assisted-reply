# Assisted Reply

A private, user-installed Discord app that drafts replies for its owner. Right-click a message, pick
**Apps → Assisted Reply**, and a draft written with the Anthropic (Claude) API appears, visible only to you.
You send it, edit it, ask for another wording or discard it. Nothing is posted until you approve it.

- Works in DMs, group DMs and server channels, without the app ever joining a server.
- Drafts are grounded in local "memory" files that you write: who you are, how you write, standard answers,
  and optional notes per person and per server.
- Only the owner can use it. Anyone else gets "This app is private."
- No self-botting: a normal bot token and the official interactions API only.

## How it works

| Command (right-click a message → Apps) | What it does |
|---|---|
| **Assisted Reply** | Drafts a reply to that message. |
| **Assisted Reply (add note)** | Asks first for a short note ("say I'm free after 3"), then drafts with it. |

The draft appears as a message only you can see, with four buttons:

- **Send** posts the draft in the conversation.
- **Edit** opens the draft in a text box, then posts your edited version.
- **Regenerate** asks for a different wording.
- **Discard** removes the draft.

A draft stays usable for 14 minutes (Discord's limit for an interaction is 15). After that, or after the app
restarts, its buttons answer "This draft expired, run Assisted Reply again."

There is also a slash command, `/assisted-reply-sync`, that registers the commands with Discord again. The app
already does this at every start, so you rarely need it.

## Setup

You need Python 3.12 or newer.

### 1. Create the Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. **Installation**: under *Installation Contexts*, enable **User Install** and disable **Guild Install**
   (the app never joins servers). In the default install settings for User Install, keep the
   `applications.commands` scope. Use the Discord-provided install link.
3. **Bot**: turn off **Public Bot** so that only you can install the app. Leave all three Privileged Gateway
   Intents off: the app needs none. Click **Reset Token** and copy the bot token.
4. Open the install link from the Installation page and choose **Add to My Apps** to install it to your account.

A second, separate application for testing is a good idea: two processes on the same token would both answer
every command.

### 2. Get an Anthropic API key

API usage is billed separately from Claude subscriptions: a Claude Pro or Max plan does not include API access.

1. Sign in to the [Claude Console](https://platform.claude.com).
2. Under **Billing**, buy prepaid credits (the minimum is enough to start).
3. Under **Settings → Workspaces**, create a workspace for this app and set a monthly **spend limit** on it,
   so a mistake can never cost more than you decided.
4. Create an API key **inside that workspace** and copy it.

Rough cost: with the default model (`claude-haiku-5-5`) a draft costs about $0.0003, so a dollar pays for a few
thousand drafts. `claude-sonnet-5-5` writes better drafts for about 20 times that, still under a cent each.
Check the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) for current numbers. The app
logs the token counts of every draft.

### 3. Install and run

```bash
git clone https://github.com/jdsaphir/assisted-reply.git
cd assisted-reply
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env && chmod 600 .env    # then fill it in
cp -r memory.example memory               # then edit the files

.venv/bin/python bot.py
```

In `.env`, three keys are required:

| Key | Value |
|---|---|
| `DISCORD_TOKEN` | The bot token from step 1. |
| `ANTHROPIC_API_KEY` | The API key from step 2. |
| `OWNER_ID` | Your Discord user ID (enable Developer Mode, right-click yourself, **Copy User ID**). |

The others are optional:

| Key | Default | Meaning |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Any current Claude model ID. |
| `ANTHROPIC_EFFORT` | `low` | How much the model thinks: `low`, `medium`, `high`, `xhigh` or `max`. Low keeps drafts fast (about a second). Set it to empty to send no effort at all. |
| `ANTHROPIC_MAX_TOKENS` | `2048` | Output cap per draft, thinking included. |
| `ANTHROPIC_TIMEOUT` | `30` | Seconds before one attempt is abandoned. |
| `ANTHROPIC_MAX_RETRIES` | `1` | Retries after a failed attempt. |
| `HISTORY_LIMIT` | `6` | Earlier exchanges from the same channel included in the prompt. `0` turns this off. |
| `MEMORY_DIR` | `memory` | Folder with your memory files. |
| `MEMORY_MAX_CHARS` | `24000` | Cap on memory text per prompt. A warning is logged when files are cut. |
| `DB_PATH` | `data/assisted_reply.sqlite3` | The local log (see Privacy). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `MENTION_AUTHOR_IN_SERVERS` | `false` | Also start a reply posted in a server with a mention of the person answered. |

At startup the app checks the configuration and stops with the name of any missing or malformed key.

### 4. Write your memory files

The `memory/` folder is read on every request, so you can edit it while the app runs. It is git-ignored.

| File | Content |
|---|---|
| `me.md` | Who you are and how you write: casing, emoji, length, phrases you use. This shapes every draft. |
| `faq.md` | Standard answers to things people often ask you. |
| `people/<user_id>.md` | Optional. Notes about one person, used when the message is from them. |
| `servers/<guild_id>.md` | Optional. Your role, the tone and the rules in one server, used for messages there. |

The model never invents plans, times or commitments. When it lacks a fact it leaves a marked placeholder such
as `[time?]` for you to fill in with **Edit**. The instructions it follows are in `prompts/system.md`, which you
can edit too.

### 5. Use it

Right-click (or long-press on mobile) any message → **Apps** → **Assisted Reply**. Read the draft, then
**Send**, **Edit**, **Regenerate** or **Discard**.

## Servers

- A reply posted from the app is labelled by Discord with "*you* used Assisted Reply" and the APP tag. That
  label comes from Discord and cannot be removed.
- Discord shows the posted message as a reply to the message you ran the command on. Set
  `MENTION_AUTHOR_IN_SERVERS=true` if you also want the text to start with a mention of that person. Nobody
  else is ever pinged: `@everyone`, `@here`, role mentions and other user mentions in a draft stay inert.
- Servers can deny members the **Use External Apps** permission. Discord then shows the app's messages only to
  you. The app warns you on the draft when it sees this, and if a send turns out to be hidden it tells you and
  keeps the draft in a copyable block so you can paste it yourself.
- Some communities have rules about generated replies. Check them before you use this there.
- In a server channel the model is told that other people will read the reply, and keeps private details from
  your notes out of it unless your note asks for them.

## Privacy

- Each message you run the app on is sent to Anthropic, together with your memory files, to write the draft.
- The app keeps a small local SQLite log (`DB_PATH`): for each channel, the messages you ran it on and the
  replies you sent, so later drafts have some continuity. Only the last `HISTORY_LIMIT` exchanges per channel
  are kept. Delete the file to clear it.
- `.env`, `memory/` and `data/` are git-ignored. Message text is never written to the application log.

## Hosting it always-on

The app is a single process that only makes outbound connections: it needs no open port, no domain and no
database server. A cheap VPS, a Raspberry Pi or a container platform such as Railway or Fly.io all work. Keep
exactly one process per bot token.

An example systemd unit (adjust the user and paths):

```ini
[Unit]
Description=Assisted Reply
After=network-online.target
Wants=network-online.target

[Service]
User=youruser
WorkingDirectory=/path/to/assisted-reply
ExecStart=/path/to/assisted-reply/.venv/bin/python bot.py
Restart=always
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
```

The app logs to stderr (so to the journal under systemd), stops cleanly on SIGTERM, and writes only to
`DB_PATH`. Drafts in progress are lost on a restart.

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The tests mock Discord and Anthropic and need no network or tokens.
