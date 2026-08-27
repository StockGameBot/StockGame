# Discord Bot Setup

How to create the Discord application, invite the bot, and fix common issues.

## 1. Create the application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. **New Application** → name it → create.
3. Open **Bot** in the sidebar → **Add Bot** (if needed).
4. Under **Privileged Gateway Intents**, enable only:
   - **Server Members Intent**
   Leave **Message Content Intent** and **Presence Intent** disabled (this bot does not use them).
5. **Reset Token** / copy the token → set `DISCORD_TOKEN` in `.env`.

## 2. Invite the bot

1. Developer Portal → **OAuth2** → **URL Generator**.
2. Scopes:
   - `bot`
   - `applications.commands`
3. Bot permissions (minimum useful set):
   - View Channels
   - Send Messages
   - Embed Links
   - Attach Files
   - Read Message History
   - Use Application Commands (slash commands)
   - Add Reactions (optional)
4. Open the generated URL, pick your server, authorize.
5. You need **Manage Server** on that Discord server to add the bot.

## 3. Set `OWNER`

1. Discord → User Settings → Advanced → enable **Developer Mode**.
2. Right-click your user → **Copy User ID**.
3. Put that numeric ID in `.env` as `OWNER` (no quotes required).

## 4. After the bot is online

1. Start the process (`python discord_bot.py` or Docker). The bot should show online in the member list.
2. In a channel, type `/` and look for Stock Game commands.
3. Smoke-test with `/game-list` or `/create-game`.

Slash commands are synced globally when the bot becomes ready. First sync can take a short while to show up everywhere.

## Troubleshooting

| Problem | Things to try |
|---------|----------------|
| Bot offline | Process not running; bad `DISCORD_TOKEN`; check logs for login / privileged-intents errors |
| Login failed / improper token | Regenerate the token in the Developer Portal and update `.env` |
| Privileged intents error | Enable **Server Members Intent** in the portal, then restart |
| Slash commands missing | Wait a few minutes after first sync; reload Discord (`Ctrl+R` / `Cmd+R`); confirm `applications.commands` was in the invite URL; re-invite if the scope was missing; check [Discord Integrations](Discord-Integrations) role/channel denies |
| Commands work but member names look wrong | Server Members Intent must be enabled; names are resolved via API fetch (members are not cached in RAM) |
| Cannot DM users (alerts / invites) | User must share a server with the bot and allow DMs from server members |

Critical operational alerts may be DMed to a hardcoded admin user ID configured in code (`helpers/logging_setup.py`). That is separate from `OWNER`.

## Related

- [Discord Integrations](Discord-Integrations) — restrict commands by role and channel
