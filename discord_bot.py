# DISCORD Bot
# SOME AI USED
# Draft exclusivity is enforced by the backend; Discord exposes it during game creation.
# TODO set up some sort of draft system for stocks

# BUILT-IN
from datetime import datetime, timedelta
import asyncio
import io
import logging
import os
import sys
import time
from typing import Any, Awaitable, Callable, Literal, Mapping, Optional, cast # 3.13 +

# EXTERNAL
import discord
from discord import app_commands
from discord.ui import Button, View
from discord.ext import commands, tasks
from dotenv import load_dotenv
import pytz

# LOCAL
from helpers.datatype_validation import GameLeaderboard
from helpers.views import (
    Pagination,
    get_leaderboard_generator,
    get_portfolio_generator,
)
from helpers.leaderboard_push import (
    bot_can_push_to_channel,
    collect_player_picks,
    fingerprint_image_rows,
    push_all_recurring_leaderboards,
)
from helpers.recurring_leaderboard_image import get_recurring_generator
import helpers.autocomplete as ac
from helpers.logging_setup import (
    attach_critical_dm_bot,
    flush_critical_dm_queue,
    latest_log_path,
    setup_app_logging,
)
from stocks import Frontend
from helpers.exceptions import NotAllowedError, DoesntExistError, AlreadyExistsError, InvalidDateFormatError
from helpers.sp500 import ensure_sp500_seeded
from helpers.alpaca_client import AlpacaMarketData
from db_schema import ensure_database, db_ver


load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
DB_NAME = os.getenv('DB_NAME')
OWNER = os.getenv('OWNER')
if not TOKEN or not DB_NAME or not OWNER:
    raise RuntimeError('Missing one or more required environment variables: DISCORD_TOKEN, DB_NAME, OWNER.')
try:
    OWNER_ID = int(OWNER)
except ValueError as exc:
    raise RuntimeError('OWNER must be a numeric Discord user ID.') from exc
    

# Set up intents with all necessary permissions
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True
# intents.dm_messages = True # for invite user command

# Testing variables
ephemeral_test = True # Set to False for testing, True for production
name_cutoff = 25 # Cut names off at 25 characters
# Legacy optional fallback only. Prefer Discord Server Settings → Integrations
# to decide who can see/run sensitive commands (see wiki: Discord Integrations).
dev_role_id = 1412173045350666271
# Guild where Administrator / dev_role moderator powers apply (shared DB is not guild-scoped).
HOME_GUILD_ID = 1358170062762283119

logger = setup_app_logging(console_level=logging.INFO, root_level=logging.DEBUG)

# Create / migrate / remake SQLite before Frontend opens it.
_db_action = ensure_database(DB_NAME)
logger.info("Database %s: %s (schema %s)", DB_NAME, _db_action, db_ver)

def is_home_guild(interaction: discord.Interaction) -> bool:
    """True when the command was invoked in the bot's primary (home) guild."""
    return interaction.guild is not None and interaction.guild.id == HOME_GUILD_ID


def has_permission(user: discord.Member) -> bool:
    """In-bot safety check for privileged actions in the home guild.

    Primary access control should be Discord **Integrations** (who can invoke
    the slash command at all). This function is a secondary gate:

    1. Guild **Administrator** permission (preferred in-bot check)
    2. Optional hardcoded ``dev_role_id`` (legacy; kept for compatibility)

    Args:
        user: Guild member running the command.

    Returns:
        True if the member passes the in-bot checks.
    """
    if user.guild_permissions.administrator:
        return True
    # Legacy role fallback — not the main reliance; Integrations should gate access.
    return any(role.id == dev_role_id for role in user.roles)


def is_moderator(interaction: discord.Interaction) -> bool:
    """Whether the caller may run privileged bot actions.

    Bot ``OWNER`` may act from any guild. Guild Administrator / ``dev_role_id``
    only count in ``HOME_GUILD_ID`` so a foreign server admin cannot touch the
    shared database.
    """
    if interaction.user.id == OWNER_ID:
        return True
    if not is_home_guild(interaction):
        return False
    return isinstance(interaction.user, discord.Member) and has_permission(interaction.user)

def simple_embed(status:str, title:str, desc:Optional[str]=None):
    """Create a simple discord embed object
    
    Objects with a status of 'failed' will be set to red

    Args:
        status (str): Status/result of action ('success', 'failed')
        title (str): Title.
        desc (Optional[str], optional): Description. Defaults to None.

    Returns:
        discord.Embed: Embed object
    """
        
    return discord.Embed(
        title = title,
        description = desc,
        color= discord.Color.green() if status == 'success' else discord.Color.red()
    )

def interaction_custom_id(interaction: discord.Interaction) -> str:
    data = cast(Mapping[str, Any], interaction.data or {})
    value = data.get('custom_id')
    return value if isinstance(value, str) else ''


class InitiatorOnlyView(discord.ui.View):
    """A short-lived component view restricted to the command initiator."""

    def __init__(self, initiator_id: int, *, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.initiator_id = initiator_id
        self.message: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.initiator_id:
            return True
        await interaction.response.send_message(
            "Only the person who started this command can use these controls.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            embed = self.message.embeds[0].copy() if self.message.embeds else discord.Embed()
            embed.set_footer(text="This confirmation expired. Run the command again to continue.")
            await self.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            logger.debug('Could not remove controls from an expired view.', exc_info=True)

# Process pending users helper
async def process_pending_user(interaction: discord.Interaction, game_id: str, pending_users: list, current_index: int):
    """Process a single pending user with approve/deny buttons"""
    
    if current_index >= len(pending_users):
        # All users processed
        embed = discord.Embed(
            title="All Pending Users Processed",
            description=f"You have processed all pending users for game #{game_id}.",
            color=discord.Color.green()
        )
        await interaction.edit_original_response(embed=embed, view=None)
        return
    
    current_user = pending_users[current_index]
    user_id = current_user.user_id
    
    # Try to get user display name
    try:
        user = await interaction.client.fetch_user(user_id)
        user_display = f"{user.display_name} ({user.name})" if user.display_name != user.name else user.name
        user_mention = user.mention
    except:
        user_display = f"User ID: {user_id}"
        user_mention = f"<@{user_id}>"
    
    # Create embed for current pending user
    embed = discord.Embed(
        title=f"Pending User Approval ({current_index + 1}/{len(pending_users)})",
        description=f"**User:** {user_display}\n**User ID:** {user_id}\n**Game:** #{game_id}",
        color=discord.Color.orange()
    )
    embed.set_footer(text=f"Processing user {current_index + 1} of {len(pending_users)}")
    
    # Create approve/deny buttons
    approve_button = discord.ui.Button(
        label="Approve",
        style=discord.ButtonStyle.success,
        emoji="✅"
    )
    
    deny_button = discord.ui.Button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        emoji="❌"
    )
    
    skip_button = discord.ui.Button(
        label="Skip",
        style=discord.ButtonStyle.secondary,
        emoji="⏭️"
    )
    
    cancel_button = discord.ui.Button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="🚫",
        custom_id="cancel"
    )
    
    view = discord.ui.View()
    view.add_item(approve_button)
    view.add_item(deny_button)
    view.add_item(skip_button)
    view.add_item(cancel_button)
    
    # Button callbacks
    async def reject_other_clicker(button_interaction: discord.Interaction) -> bool:
        if button_interaction.user.id == interaction.user.id:
            return False
        await button_interaction.response.send_message(
            "Only the moderator who started this review can use these controls.",
            ephemeral=True,
        )
        return True

    async def approve_callback(button_interaction: discord.Interaction):
        if await reject_other_clicker(button_interaction):
            return
        try:
            # Approve the user
            fe.approve_game_users(
                user_id=interaction.user.id,
                game_id=game_id,
                approved_user_id=user_id
            )
            
            # Try to notify the approved user
            try:
                game_name = fe._get_game_name(game_id=game_id)
                approval_embed = discord.Embed(
                    title="Game Approval",
                    description=f"You have been approved to join the game '{game_name}' (#{game_id})!",
                    color=discord.Color.green()
                )
                await user.send(embed=approval_embed)
                notification_status = "✉️ User notified"
            except:
                notification_status = "⚠️ Could not notify user (DMs disabled)"
            
            # Show confirmation and move to next user
            success_embed = discord.Embed(
                title="User Approved",
                description=f"✅ {user_display} has been approved for game #{game_id}.\n{notification_status}",
                color=discord.Color.green()
            )
            await button_interaction.response.edit_message(embed=success_embed, view=None)
            
            # Wait a moment then process next user
            import asyncio
            await asyncio.sleep(1.5)
            await process_pending_user(interaction, game_id, pending_users, current_index + 1)
            
        except Exception as e:
            logger.exception(f'Failed to approve user {user_id} for game {game_id}. Error: {e}')
            error_embed = discord.Embed(
                title="Approval Failed",
                description=f"❌ Failed to approve {user_display}. Please try again or contact a moderator.",
                color=discord.Color.red()
            )
            await button_interaction.response.edit_message(embed=error_embed, view=None)
    
    async def deny_callback(button_interaction: discord.Interaction):
        if await reject_other_clicker(button_interaction):
            return
        try:
            # Remove the user from pending (deny them)
            participant_id = fe._participant_id(user_id=user_id, game_id=game_id)
            fe.be.remove_participant(participant_id=participant_id)
            
            # Show confirmation and move to next user
            deny_embed = discord.Embed(
                title="User Denied",
                description=f"❌ {user_display} has been denied access to game #{game_id}.",
                color=discord.Color.red()
            )
            await button_interaction.response.edit_message(embed=deny_embed, view=None)
            
            # Wait a moment then process next user
            import asyncio
            await asyncio.sleep(1.5)
            await process_pending_user(interaction, game_id, pending_users, current_index + 1)
            
        except Exception as e:
            logger.exception(f'Failed to deny user {user_id} for game {game_id}. Error: {e}')
            error_embed = discord.Embed(
                title="Denial Failed",
                description=f"❌ Failed to deny {user_display}. Please try again or contact a moderator.",
                color=discord.Color.red()
            )
            await button_interaction.response.edit_message(embed=error_embed, view=None)
    
    async def skip_callback(button_interaction: discord.Interaction):
        if await reject_other_clicker(button_interaction):
            return
        skip_embed = discord.Embed(
            title="User Skipped",
            description=f"⏭️ Skipped {user_display}. They will remain pending.",
            color=discord.Color.blue()
        )
        await button_interaction.response.edit_message(embed=skip_embed, view=None)
        
        # Wait a moment then process next user
        import asyncio
        await asyncio.sleep(1.5)
        await process_pending_user(interaction, game_id, pending_users, current_index + 1)
    
    async def cancel_callback(button_interaction: discord.Interaction):
        if await reject_other_clicker(button_interaction):
            return
        cancel_embed = discord.Embed(
            title="Process Cancelled",
            description=f"Pending user management cancelled. Remaining users are still pending.",
            color=discord.Color.orange()
        )
        await button_interaction.response.edit_message(embed=cancel_embed, view=None)
    
    # Set callbacks
    approve_button.callback = approve_callback  # type: ignore[assignment]
    deny_button.callback = deny_callback  # type: ignore[assignment]
    skip_button.callback = skip_callback  # type: ignore[assignment]
    cancel_button.callback = cancel_callback  # type: ignore[assignment]
    
    # Send or edit the message
    if current_index == 0:
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.edit_original_response(embed=embed, view=view)

bot = commands.Bot(command_prefix="$", intents=intents)
logger.info(f'Connecting with DB: {DB_NAME}')
fe = Frontend(database_name=DB_NAME, owner_user_id=OWNER_ID, source='discord') # Frontend
ac.init_autocomplete(fe)  # Inject the shared Frontend instance into autocomplete module

# Prevent overlapping update_all runs if a cycle takes longer than the loop interval.
_game_update_lock = asyncio.Lock()

# In-memory leaderboard image cache: "game_id:rank_page" -> (png_bytes, fingerprint, generated_at)
_leaderboard_image_cache: dict[str, tuple[bytes, str, float]] = {}
_LEADERBOARD_CACHE_TTL_SEC = 600.0  # align with long-lived view timeout
_LEADERBOARD_CACHE_MAX_ENTRIES = 64
_LEADERBOARD_RANK_PAGE_SIZE = 15
_RECURRING_LEADERBOARD_RANK_PAGE_SIZE = 5
# Generous so a full rank page always fits; pagination, not height, caps the rows.
_RECURRING_PAGE_MAX_HEIGHT = 8000


def invalidate_leaderboard_cache(game_ids: Optional[list[str]] = None) -> None:
    if game_ids is None:
        _leaderboard_image_cache.clear()
        return
    for gid in game_ids:
        prefix = f"{gid}:"
        for key in list(_leaderboard_image_cache):
            if key == str(gid) or key.startswith(prefix):
                _leaderboard_image_cache.pop(key, None)


def _store_leaderboard_cache(cache_key: str, data: bytes, fingerprint: str) -> None:
    """Insert a cache entry and drop oldest keys when over capacity."""
    now = time.monotonic()
    _leaderboard_image_cache[cache_key] = (data, fingerprint, now)
    if len(_leaderboard_image_cache) <= _LEADERBOARD_CACHE_MAX_ENTRIES:
        return
    ordered = sorted(_leaderboard_image_cache.items(), key=lambda item: item[1][2])
    overflow = len(_leaderboard_image_cache) - _LEADERBOARD_CACHE_MAX_ENTRIES
    for key, _value in ordered[:overflow]:
        _leaderboard_image_cache.pop(key, None)


async def _run_update_and_push(*, force: bool = False) -> None:
    """Run GameLogic.update_all off-loop, then push recurring leaderboards on Discord."""
    if force:
        await asyncio.to_thread(fe.gl.update_all, None, True)
    else:
        await asyncio.to_thread(fe.gl.update_all)
    await push_all_recurring_leaderboards(bot, fe, name_resolver=resolve_player_name)


@tasks.loop(minutes=15)
async def scheduled_game_update():
    """Refresh prices (Alpaca) and game portfolios every 15 minutes without blocking Discord."""
    if _game_update_lock.locked():
        logger.debug('Skipping scheduled update; previous cycle still running.')
        return
    async with _game_update_lock:
        try:
            await _run_update_and_push(force=False)
        except Exception:
            logger.exception('Scheduled game update failed.')

@scheduled_game_update.before_loop
async def wait_for_scheduled_update():
    await bot.wait_until_ready()

async def _seed_sp500_on_startup() -> None:
    """Idempotently load S&P 500 tickers in a worker thread; never block Discord."""
    try:
        alpaca = AlpacaMarketData()
        if not alpaca.configured:
            logger.warning('Skipping S&P 500 seed: Alpaca credentials not configured.')
            return
        stats = await asyncio.to_thread(ensure_sp500_seeded, fe.be, alpaca, log=logger)
        logger.info(
            'S&P 500 startup seed: listed=%s existing=%s added=%s priced=%s failed=%s',
            stats['listed'],
            stats['existing'],
            stats['added'],
            stats['priced'],
            stats['failed'],
        )
    except Exception:
        logger.exception('S&P 500 startup seed failed; continuing without blocking the bot')


# Event: Called when the bot is ready and connected to Discord
@bot.event
async def on_ready():
    """Prints a message to the console when the bot is online and syncs slash commands."""
    attach_critical_dm_bot(bot)
    await flush_critical_dm_queue()
    if bot.user is None:
        logger.critical('Ready event fired without an authenticated bot user.')
        return
    logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    # Recurring series are owned by the bot account so `/game-list owner:@Bot` filters them.
    try:
        fe.register(user_id=bot.user.id, username=bot.user.name, source='discord')
        fe.gl.recurring_game_owner_id = bot.user.id
        logger.info('Recurring games will be owned by bot user id %s', bot.user.id)
    except Exception:
        logger.exception('Failed to register bot user for recurring-game ownership')
    if not scheduled_game_update.is_running():
        scheduled_game_update.start()
    # Keep the equity universe current without delaying command sync.
    asyncio.create_task(_seed_sp500_on_startup())
    try:
        # Sync commands globally
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
        for command in synced:
            logger.info(f"   - {command.name}: {command.description}")
    except Exception as e:
        logger.critical(f"Failed to sync commands: {e}")


# GAME INTERACTION RELATED

@bot.tree.command(name="create-game-advanced", description="Create a new stock game without a wizard")
@app_commands.describe(
    name="Name of the game",
    start_date="Game start date (YYYY-MM-DD). Does not by itself stop buying.",
    end_date="End date (YYYY-MM-DD)",
    pick_date="Last day players can buy stocks (YYYY-MM-DD). Leave empty = buy anytime.",
    starting_money="Starting money amount",
    total_picks="Number of stocks each player can pick",
    exclusive_picks="Draft mode: each stock can only be picked by one player (requires a deadline on or before the start date)",
    private_game="Whether the game is private (requires owner approval for new users)",
    # sell_during_game="Whether players may sell owned stocks during the game",  # not implemented yet
)
async def create_game_advanced(
    interaction: discord.Interaction,
    name: app_commands.Range[str, 1, name_cutoff],
    start_date: str,
    end_date: str | None = None,
    starting_money: app_commands.Range[int, 1, 1000000000000] = 10000,
    total_picks: app_commands.Range[int, 1, 1000] = 10,
    exclusive_picks: bool = False,
    private_game: bool = False,
    pick_date: str | None = None,
    # sell_during_game: bool = False,  # not implemented yet
):
    # Create game using frontend and return
    try:
        game_id = fe.new_game(
            user_id=interaction.user.id,
            name=name,
            start_date=start_date,
            end_date=end_date,
            starting_money=starting_money,
            total_picks=total_picks,
            exclusive_picks=exclusive_picks,
            private_game= private_game,
            pick_date=pick_date,
            update_frequency='alpaca',
            sell_during_game=False,  # selling not implemented; keep default off
        )
        
        pick_note = (
            f"Pick deadline: `{pick_date}`"
            if pick_date
            else "Pick deadline: none — players can buy anytime"
        )
        embed = discord.Embed(
            title="Game Created Successfully",
            description=f"Game '{name}' has been created. Game ID: #{game_id}\n{pick_note}",
            color=discord.Color.green()
        )
    except (InvalidDateFormatError, ValueError, TypeError) as exc:
        embed = discord.Embed(
            title="Game Creation Failed",
            description=str(exc),
            color=discord.Color.red(),
        )
    except Exception as exc:
        logger.exception("Advanced game creation failed", exc_info=exc)
        embed = simple_embed(
            status='failed',
            title='Game Creation Failed',
            desc='Unable to create the game. Please check the supplied values and try again.',
        )
     
    await interaction.response.send_message(embed=embed, ephemeral=ephemeral_test)

# this code is a complete mess at the moment, trying to get it to work my way but it is taking more time than it's worth
# THIS ITERATION IS WORKING IN THE CURRENT STATE
@bot.tree.command(name="create-game", description="Guided setup for stock game creation")
async def create_game(interaction: discord.Interaction):
    # Create the initial embed
    embed = discord.Embed(
        title="Welcome to the Game Creation Wizard!",
        description="Click the button below to start creating your game.",
        color=discord.Color.blue()
    )
    
    # Create a button
    game_creation_wizard_start = discord.ui.Button(
        label="Create Stock Game",
        emoji="🛠️",
        style=discord.ButtonStyle.primary
    )
    
    # Create a view to hold the button
    game_creation_button_view = InitiatorOnlyView(interaction.user.id, timeout=120)
    game_creation_button_view.add_item(game_creation_wizard_start)
    
    # Send the initial message with the embed and button
    await interaction.response.send_message(embed=embed, view=game_creation_button_view, ephemeral=ephemeral_test)
    game_creation_button_view.message = await interaction.original_response()
    
    # Define what happens when the button is clicked
    async def game_creation_wizard_start_callback(interaction: discord.Interaction):
        original_user = interaction.user.id
        # Create a modal (popup) for text input
        initial_wizard_modal = discord.ui.Modal(title="Create Game Wizard", timeout=60)

        # Add a text input field for each text and number input
        name_input = discord.ui.TextInput(
            label="Name of your Stock Game",
            placeholder=f"{interaction.user.display_name}'s Stock Game",
            required=True,
            max_length=name_cutoff,
            min_length=3,
        )

        start_date_input = discord.ui.TextInput(
            label="Start Date (when the game becomes active)",
            placeholder=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"), # Default to 7 days from now
            required=True,
            max_length=10,
            min_length=10,
            style=discord.TextStyle.short
        )

        end_date_input = discord.ui.TextInput(
            label="End Date *leave blank for no end date",
            placeholder="YYYY-MM-DD",
            required=False,
            max_length=10,
            min_length=10,
            style=discord.TextStyle.short
        )

        starting_money_input = discord.ui.TextInput(
            label="Starting Money Amount",
            placeholder="10000",
            required=False
        )

        total_picks_input = discord.ui.TextInput(
            label="Total Picks",
            placeholder="10",
            required=False
        )

        initial_wizard_modal.add_item(name_input)
        initial_wizard_modal.add_item(start_date_input)
        initial_wizard_modal.add_item(end_date_input)
        initial_wizard_modal.add_item(starting_money_input)
        initial_wizard_modal.add_item(total_picks_input)

        # Show the modal
        await interaction.response.send_modal(initial_wizard_modal)

        async def initial_wizard_timeout():
            try:
                await interaction.edit_original_response(
                    embed=simple_embed(
                        status='failed',
                        title='Game Creation Timed Out',
                        desc='The form was not submitted in time. Run /create-game to start again.',
                    ),
                    view=None,
                )
            except discord.HTTPException:
                logger.debug('Could not mark game creation modal as timed out.', exc_info=True)

        initial_wizard_modal.on_timeout = initial_wizard_timeout
        
        # Define what happens when the modal is submitted
        async def initial_wizard_callback(interaction: discord.Interaction):
            # Create a exclusive picks embed
            exclusive_picks_embed = discord.Embed(
                title="Do you want exclusive picks?",
                description="If you select 'Yes', a stock can only be picked by one player. If you select 'No', a stock can be picked by multiple players.",
                color=discord.Color.blue()
            )

            # Create buttons for exclusive picks
            exclusive_picks_yes = discord.ui.Button(
                label="Yes",
                style=discord.ButtonStyle.success,
                custom_id="exclusive_picks_yes"
            )

            exclusive_picks_no = discord.ui.Button(
                label="No",
                style=discord.ButtonStyle.danger,
                custom_id="exclusive_picks_no"
            )

            exclusive_picks_view = discord.ui.View()
            exclusive_picks_view.add_item(exclusive_picks_yes)
            exclusive_picks_view.add_item(exclusive_picks_no)
            
            # Send the response
            await interaction.response.edit_message(embed=exclusive_picks_embed, view=exclusive_picks_view)

            # Define what happens when the exclusive picks button is clicked
            async def exclusive_picks_callback(interaction: discord.Interaction):
                if interaction.user.id != original_user:
                    await interaction.response.send_message(
                        "Only the person who started this wizard can make selections.",
                        ephemeral=True,
                    )
                    return
                # Check which button was clicked
                if interaction_custom_id(interaction) == "exclusive_picks_yes":
                    game_exclusive_picks = True
                else:
                    game_exclusive_picks = False

                pick_date_modal = discord.ui.Modal(title="Buy / Pick Deadline", timeout=60)

                pick_date_input = discord.ui.TextInput(
                    label=(
                        "Pick deadline (required for exclusive picks)"
                        if game_exclusive_picks
                        else "Pick deadline (blank = buy anytime)"
                    ),
                    placeholder="YYYY-MM-DD — leave blank to allow buying anytime",
                    required=game_exclusive_picks,
                    max_length=10,
                    min_length=10 if game_exclusive_picks else 0,
                    style=discord.TextStyle.short
                )

                pick_date_modal.add_item(pick_date_input)
                
                await interaction.response.send_modal(pick_date_modal)
                
                async def pick_date_callback(interaction: discord.Interaction):
                    if interaction.user.id != original_user:
                        await interaction.response.send_message(
                            "Only the person who started this wizard can make selections.",
                            ephemeral=True,
                        )
                        return

                    # Create a response embed for join after start
                    private_embed = discord.Embed(
                        title="Do you want your game to be private?",
                        description=(
                            "If you select 'Yes', the game stays hidden from public lists. "
                            "Players who use `/join-game` need owner approval via `/manage-pending`. "
                            "Owner `/invite`s join private games immediately (no approval).\n\n"
                            "If you select 'No', it will be visible publicly.\n\n"
                            "⚠️ **DM tip:** `/invite` delivers join buttons by DM. If the invited user has "
                            "DMs from server members turned off, private invites may not work — ask them to "
                            "enable DMs, or share the game ID privately."
                        ),
                        color=discord.Color.blue()
                    )

                    # Create buttons for join after start
                    private_yes = discord.ui.Button(
                        label="Yes",
                        style=discord.ButtonStyle.success,
                        custom_id="private_yes"
                    )

                    private_no = discord.ui.Button(
                        label="No",
                        style=discord.ButtonStyle.danger,
                        custom_id="private_no"
                    )

                    private_game_view = discord.ui.View()
                    private_game_view.add_item(private_yes)
                    private_game_view.add_item(private_no)
                    
                    # Send the response
                    await interaction.response.edit_message(embed=private_embed, view=private_game_view)

                    # Define what happens when the join after start button is clicked
                    async def private_game_callback(button_interaction: discord.Interaction):
                        if button_interaction.user.id != original_user:
                            await button_interaction.response.send_message(
                                "Only the person who started this wizard can make selections.",
                                ephemeral=True,
                            )
                            return

                        private_game = interaction_custom_id(button_interaction) == "private_yes"

                        # --- Selling UI (not implemented yet; restore later) ---
                        # sell_embed = discord.Embed(
                        #     title="Allow selling during the game?",
                        #     description="If enabled, players can sell owned stocks. Otherwise, owned picks are permanent.",
                        #     color=discord.Color.blue(),
                        # )
                        # sell_yes = discord.ui.Button(label="Allow selling", style=discord.ButtonStyle.success, custom_id="sell_yes")
                        # sell_no = discord.ui.Button(label="Keep picks permanent", style=discord.ButtonStyle.secondary, custom_id="sell_no")
                        # sell_view = discord.ui.View(timeout=120)
                        # sell_view.add_item(sell_yes)
                        # sell_view.add_item(sell_no)
                        # await button_interaction.response.edit_message(embed=sell_embed, view=sell_view)
                        # async def sell_callback(sell_interaction: discord.Interaction):
                        #     ... (was: ask sell yes/no, then show confirmation)
                        # sell_yes.callback = sell_callback
                        # sell_no.callback = sell_callback
                        # --- end selling UI ---

                        sell_during_game = False  # selling not implemented; keep default off
                        try:
                            game_starting_money = int(float(starting_money_input.value.replace(',', ''))) if starting_money_input.value else 10000
                            game_total_picks = int(total_picks_input.value.replace(',', '')) if total_picks_input.value else 10
                            if game_starting_money < 1 or game_total_picks < 1:
                                raise ValueError
                        except ValueError:
                            await button_interaction.response.edit_message(
                                embed=simple_embed(
                                    status='failed',
                                    title='Invalid Game Settings',
                                    desc='Starting money and total picks must be whole numbers greater than zero. Run /create-game to try again.',
                                ),
                                view=None,
                            )
                            return

                        game_name = name_input.value
                        game_start_date = start_date_input.value
                        game_end_date = end_date_input.value or None
                        game_pick_date = pick_date_input.value or None
                        pick_deadline_text = game_pick_date or "None — players can buy anytime"
                        confirmation_embed = discord.Embed(
                            title="Game Creation Confirmation",
                            description=(
                                f"**Name:** {game_name}\n"
                                f"**Start date:** {game_start_date}\n"
                                f"**End date:** {game_end_date or 'None'}\n"
                                f"**Starting money:** ${game_starting_money:,}\n"
                                f"**Total picks:** {game_total_picks}\n"
                                f"**Exclusive picks:** {'Yes' if game_exclusive_picks else 'No'}\n"
                                f"**Private game:** {'Yes' if private_game else 'No'}\n"
                                # f"**Selling enabled:** {'Yes' if sell_during_game else 'No'}\n"  # not implemented yet
                                f"**Pick deadline:** {pick_deadline_text}"
                            ),
                            color=discord.Color.blue(),
                        )
                        confirmation_embed.set_footer(text="Confirm to create the game, or cancel to discard these settings.")
                        confirmation_view = discord.ui.View(timeout=120)
                        confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.success)
                        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
                        confirmation_view.add_item(confirm_button)
                        confirmation_view.add_item(cancel_button)

                        async def confirm_callback(confirm_interaction: discord.Interaction):
                            if confirm_interaction.user.id != original_user:
                                await confirm_interaction.response.send_message(
                                    "Only the person who started this wizard can confirm it.",
                                    ephemeral=True,
                                )
                                return
                            try:
                                game_id = fe.new_game(
                                    user_id=confirm_interaction.user.id,
                                    name=game_name,
                                    start_date=game_start_date,
                                    end_date=game_end_date,
                                    pick_date=game_pick_date,
                                    starting_money=game_starting_money,
                                    total_picks=game_total_picks,
                                    exclusive_picks=game_exclusive_picks,
                                    private_game=private_game,
                                    update_frequency='alpaca',
                                    sell_during_game=sell_during_game,
                                )
                                creation_status_embed = simple_embed(
                                    status='success',
                                    title='Game Created Successfully',
                                    desc=f"Game '{game_name}' has been created. Game ID: #{game_id}",
                                )
                            except (InvalidDateFormatError, ValueError, TypeError) as exc:
                                creation_status_embed = simple_embed(
                                    status='failed',
                                    title='Game Creation Failed',
                                    desc=str(exc),
                                )
                            except Exception as exc:
                                logger.exception('Guided game creation failed', exc_info=exc)
                                creation_status_embed = simple_embed(
                                    status='failed',
                                    title='Game Creation Failed',
                                    desc='Unable to create the game. Please check the settings and try again.',
                                )
                            await confirm_interaction.response.edit_message(embed=creation_status_embed, view=None)

                        async def cancel_callback(cancel_interaction: discord.Interaction):
                            if cancel_interaction.user.id != original_user:
                                await cancel_interaction.response.send_message(
                                    "Only the person who started this wizard can cancel it.",
                                    ephemeral=True,
                                )
                                return
                            await cancel_interaction.response.edit_message(
                                embed=simple_embed(status='success', title='Game Creation Cancelled', desc='No game was created.'),
                                view=None,
                            )

                        confirm_button.callback = confirm_callback  # type: ignore[assignment]
                        cancel_button.callback = cancel_callback  # type: ignore[assignment]
                        await button_interaction.response.edit_message(embed=confirmation_embed, view=confirmation_view)

                    private_yes.callback = private_game_callback  # type: ignore[assignment]
                    private_no.callback = private_game_callback  # type: ignore[assignment]

                # Set the pick date modal callback
                pick_date_modal.on_submit = pick_date_callback

            # Set the exclusive button callback
            exclusive_picks_yes.callback = exclusive_picks_callback
            exclusive_picks_no.callback = exclusive_picks_callback

        # Set the modal callback
        initial_wizard_modal.on_submit = initial_wizard_callback

    # Set the button callback
    game_creation_wizard_start.callback = game_creation_wizard_start_callback


class LeaderboardChannelSelect(discord.ui.View):
    """Ephemeral channel picker after enabling push_leaderboard on a template."""

    def __init__(
        self,
        template_id: int,
        on_saved: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__(timeout=180)
        self.template_id = template_id
        self.on_saved = on_saved
        self.select = discord.ui.ChannelSelect(
            placeholder="Choose a text channel for leaderboard posts",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send("This can only be used in a server.", ephemeral=True)
            return
        values = self.select.values
        if not values:
            await interaction.followup.send("No channel selected.", ephemeral=True)
            return

        # ChannelSelect yields lightweight AppCommandChannel objects, not real channels.
        selected = values[0]
        channel = selected.resolve()
        if channel is None:
            try:
                channel = await selected.fetch()
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                f"I can't access <#{selected.id}>. Pick a text channel I can see, then try again.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        if me is None or not bot_can_push_to_channel(channel, me):
            try:
                fe.be.update_game_template(
                    template_id=self.template_id,
                    push_leaderboard=False,
                    clear_leaderboard_channel=True,
                )
            except Exception:
                logger.exception("Failed to clear push settings after permission check")
            await interaction.followup.send(
                "I need **View Channel**, **Send Messages**, **Embed Links**, and **Attach Files** "
                f"in {channel.mention}. Push stays off until you fix permissions and re-enable it.",
                ephemeral=True,
            )
            self.stop()
            return

        try:
            fe.be.update_game_template(
                template_id=self.template_id,
                push_leaderboard=True,
                leaderboard_channel_id=str(channel.id),
            )
        except Exception:
            logger.exception("Failed to save leaderboard channel for template %s", self.template_id)
            await interaction.followup.send("❌ Could not save that channel. Try again.", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Leaderboard posts will go to {channel.mention}.",
            ephemeral=True,
        )
        if self.on_saved is not None:
            try:
                await self.on_saved()
            except Exception:
                logger.debug("Push-channel saved callback failed.", exc_info=True)
        self.stop()


@bot.tree.command(name="create-recurring-game", description="Create a recurring game template")
@app_commands.default_permissions()
@app_commands.describe(
    name="Name of the game template",
    start_date="First game start date (YYYY-MM-DD). Later games repeat monthly from this day",
    recurring_period="Months between recurring games (optional, default: 1)",
    game_length="How many months each game lasts. 0 = infinite. Cannot exceed recurring period",
    create_days_in_advance="How many days before each game's start to create it (optional, default: 7)",
    starting_money="Starting money for players (optional, default: 10000)",
    pick_date="Buy deadline in days before each game start. Negative = after start. Empty = anytime",
    private_game="Make the game private (optional, default: False)",
    total_picks="Maximum number of picks per player (optional, default: 10)",
    exclusive_picks="Enable exclusive picks: each stock can only be picked once (optional, default: False)",
    push_leaderboard="Post/edit a live leaderboard image in a channel (default: False)",
)
async def create_recurring_game(
    interaction: discord.Interaction,
    name: app_commands.Range[str, 1, name_cutoff],
    start_date: str,
    recurring_period: app_commands.Range[int, 1, 12] = 1,
    game_length: app_commands.Range[int, 0, 12] = 1,
    create_days_in_advance: app_commands.Range[int, 0, 30] = 7,
    starting_money: app_commands.Range[int, 1, 1000000000000] = 10000,
    pick_date: int | None = None,
    private_game: bool = False,
    total_picks: app_commands.Range[int, 1, 1000] = 10,
    exclusive_picks: bool = False,
    push_leaderboard: bool = False,
):
        """Create a recurring game template"""

        await interaction.response.defer(ephemeral=ephemeral_test)

        try:
            if exclusive_picks and pick_date is None:
                await interaction.followup.send(
                    "❌ Exclusive picks requires a pick deadline on or before each game start. "
                    "Press the **↑ up arrow** to edit your previous command.",
                    ephemeral=ephemeral_test,
                )
                return
            if exclusive_picks and pick_date is not None and pick_date < 0:
                await interaction.followup.send(
                    "❌ Exclusive picks cannot use a pick deadline after the game start. "
                    "Press the **↑ up arrow** to edit your previous command.",
                    ephemeral=ephemeral_test,
                )
                return
            if pick_date is not None and (pick_date < -30 or pick_date > 30):
                await interaction.followup.send(
                    "❌ Pick date must be between -30 and 30 days relative to each game's start date. "
                    "Press the **↑ up arrow** to edit your previous command.",
                    ephemeral=ephemeral_test,
                )
                return
            if game_length > 0 and game_length > recurring_period:
                await interaction.followup.send(
                    "❌ Game length cannot be longer than the recurring period "
                    "(that would overlap active games). "
                    "Press the **↑ up arrow** to edit your previous command.",
                    ephemeral=ephemeral_test,
                )
                return

            user_id = interaction.user.id
            fe.register(user_id=user_id, username=interaction.user.display_name)

            template_id = fe.be.add_game_template(
                user_id=user_id,
                name=name,
                start_date=start_date,
                create_days_in_advance=create_days_in_advance,
                recurring_period=recurring_period,
                game_length=game_length,
                starting_money=starting_money,
                pick_date=pick_date,
                private_game=private_game,
                total_picks=total_picks,
                exclusive_picks=exclusive_picks,
                sell_during_game=False,  # selling not implemented; keep default off
            )

            embed = discord.Embed(
                title="✅ Recurring Game Template Created!",
                description=f"Successfully created recurring game template: **{name}**",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )

            embed.add_field(name="📅 Start Date", value=start_date, inline=True)
            embed.add_field(name="🔄 Recurring Every", value=f"{recurring_period} months", inline=True)
            embed.add_field(name="⏱️ Game Length", value=(f"{game_length} months" if game_length != 0 else "infinite"), inline=True)
            embed.add_field(name="💰 Starting Money", value=f"${starting_money:,.2f}", inline=True)
            embed.add_field(name="📊 Total Picks", value=str(total_picks), inline=True)
            embed.add_field(name="🔒 Private", value="Yes" if private_game else "No", inline=True)

            if pick_date is not None:
                if pick_date > 0:
                    pick_date_text = f"{pick_date} days before each game start"
                elif pick_date < 0:
                    pick_date_text = f"{abs(pick_date)} days after each game start"
                else:
                    pick_date_text = "On each game start date"
                embed.add_field(name="📝 Pick Deadline", value=pick_date_text, inline=True)
            else:
                embed.add_field(name="📝 Pick Deadline", value="None — buy anytime", inline=True)

            embed.add_field(name="🎯 Exclusive Picks", value="Yes" if exclusive_picks else "No", inline=True)
            embed.add_field(name="🏷️ Updates", value="alpaca", inline=True)
            embed.add_field(name="⏰ Create in Advance", value=f"{create_days_in_advance} days", inline=True)
            embed.add_field(
                name="📣 Push Leaderboard",
                value="Choose a channel below" if push_leaderboard else "No",
                inline=True,
            )
            if private_game:
                embed.set_footer(
                    text=(
                        f"Created by {interaction.user.display_name} · Template ID {template_id} · "
                        "Private: /join-game needs approval; owner /invite joins immediately"
                    )
                )
            else:
                embed.set_footer(text=f"Created by {interaction.user.display_name} · Template ID {template_id}")

            await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)
            if push_leaderboard:
                await interaction.followup.send(
                    "Select the channel where I should post the live leaderboard:",
                    view=LeaderboardChannelSelect(template_id),
                    ephemeral=True,
                )

        except AlreadyExistsError:
            await interaction.followup.send(
                f"❌ A recurring template named **{name}** already exists. "
                "Choose a different name — press the **↑ up arrow** to bring back your previous command and edit it.",
                ephemeral=ephemeral_test,
            )
        except InvalidDateFormatError:
            await interaction.followup.send(
                "❌ Invalid start date. Use `YYYY-MM-DD` (example: `2026-08-01`). "
                "Press the **↑ up arrow** to edit your previous command.",
                ephemeral=ephemeral_test,
            )
        except ValueError as e:
            await interaction.followup.send(
                f"❌ {e} Press the **↑ up arrow** to edit your previous command.",
                ephemeral=ephemeral_test,
            )
        except Exception as e:
            logger.exception(f'User {interaction.user.id} failed to create recurring template', exc_info=e)
            error_message = "❌ Failed to create recurring game template. Please try again or contact a moderator."
            await interaction.followup.send(error_message, ephemeral=ephemeral_test)

@bot.tree.command(name="join-game", description="Join an existing stock game")
@app_commands.autocomplete(game_id=ac.join_games_autocomplete)
@app_commands.describe(
    game_id="ID of the game to join"
)
async def join_game(
    interaction: discord.Interaction, 
    game_id: str
):
    status = 'failed'
    description = "failed"
    try:
        fe.register(user_id=interaction.user.id, username=interaction.user.display_name)
        fe.join_game(
            user_id=interaction.user.id, 
            game_id=game_id
        )

        game_name = fe._get_game_name(game_id)
        title = "Game Joined Successfully"
        description = f"You have joined **{game_name}** (#{game_id})."
        status = 'success'
        # Private games create a pending participant until the owner approves it.
        try:
            participants = fe.be.get_many_participants(user_id=interaction.user.id, game_id=game_id)
            if participants and participants[0].status == 'pending':
                title = "Join Request Submitted"
                description = f"Your request to join **{game_name}** (#{game_id}) is pending. The owner must approve it before you can play."
        except LookupError:
            logger.warning('Could not verify join status for user %s in game %s.', interaction.user.id, game_id)
    except LookupError:
        description = f'No game with the ID {game_id}.'
        
    except ValueError as e:
        if 'already in game.' in str(e).lower():
            description = f'You are already in this game ID {game_id}.'
            
        elif '`pick_date` has passed.' in str(e).lower():
            description = f'The pick date for this game has passed.'
        else:
            description = 'Unable to join this game. Please check its settings and try again.'
            
    except Exception as e:
        logger.exception(f'User: {interaction.user.id} failed to join game {game_id}.  Error: {e}')
        description = f'An unexpected error ocurred when joining game {game_id}. Please try again or contact a moderator.'

    if status == 'failed':
        title = "Game Join Failed"

    await interaction.response.send_message(embed=simple_embed(status = status, title = title, desc = description), ephemeral=ephemeral_test)

@bot.tree.command(name="delete-game", description="Delete a game (Owner/Admin) - with confirmation")
@app_commands.autocomplete(game_id=ac.owner_games_autocomplete)
@app_commands.describe(
    game_id="The game ID to delete"
)
async def delete_game(
    interaction: discord.Interaction,
    game_id: str,
):
    try:
        game_info = fe.game_info(game_id, False)
    except LookupError:
        await interaction.response.send_message(
            embed=simple_embed(status='failed', title='Game Not Found', desc=f'No game exists with ID #{game_id}.'),
            ephemeral=ephemeral_test,
        )
        return

    game = game_info.game
    if interaction.user.id != game.owner_id and not is_moderator(interaction):
        await interaction.response.send_message(
            embed=simple_embed(status='failed', title='Not Allowed', desc='Only the game owner or a moderator can delete this game.'),
            ephemeral=ephemeral_test,
        )
        return

    confirm_view = InitiatorOnlyView(interaction.user.id, timeout=30)
    confirm_btn = discord.ui.Button(label="Yes, delete it", style=discord.ButtonStyle.danger, emoji="⚠️")
    cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

    confirm_embed = discord.Embed(
        title="Confirm Deletion",
        description=f"Are you sure you want to delete game **{game.name}** (#{game_id})?\nThis action cannot be undone.",
        color=discord.Color.orange()
    )

    async def do_delete(btn_interaction: discord.Interaction):
        try:
            if is_moderator(btn_interaction):
                fe.remove_game(user_id=interaction.user.id, game_id=game_id, enforce_permissions=False)
            else:
                fe.remove_game(user_id=interaction.user.id, game_id=game_id)
            await btn_interaction.response.edit_message(
                embed=simple_embed(status='success', title='Deleted', desc=f'Game #{game_id} has been deleted.'),
                view=None
            )
        except PermissionError:
            await btn_interaction.response.edit_message(
                embed=simple_embed(status='failed', title='Failed', desc='You do not have permission to delete this game.'),
                view=None
            )
        except Exception as exc:
            logger.exception(f'Failed to delete game {game_id}', exc_info=exc)
            await btn_interaction.response.edit_message(
                embed=simple_embed(status='failed', title='Failed', desc='An error occurred while deleting the game.'),
                view=None
            )

    async def cancel_delete(btn_interaction: discord.Interaction):
        await btn_interaction.response.edit_message(
            embed=simple_embed(status='success', title='Cancelled', desc='Deletion cancelled.'),
            view=None
        )

    confirm_btn.callback = do_delete  # type: ignore[assignment]
    cancel_btn.callback = cancel_delete  # type: ignore[assignment]
    confirm_view.add_item(confirm_btn)
    confirm_view.add_item(cancel_btn)
    await interaction.response.send_message(embed=confirm_embed, view=confirm_view, ephemeral=ephemeral_test)
    confirm_view.message = await interaction.original_response()

@bot.tree.command(name="manage-game", description="Manage an existing stock game")
@app_commands.autocomplete(game_id=ac.owner_games_autocomplete)
@app_commands.describe(
    game_id="ID of the game to update",
    name="New name of the game",
    owner="New game owner",
    start_date="New start date (YYYY-MM-DD). Cannot be changed once game has started",
    end_date="New end date (YYYY-MM-DD)",
    clear_end_date="Remove the end date",
    pick_date="New pick deadline (YYYY-MM-DD). Cannot be changed once game has started",
    clear_pick_date="Remove the pick deadline before the game starts",
    private_game="Whether the game is private or not",
    starting_money="New starting money amount. Cannot be changed once game has started",
    total_picks="New number of stocks each player can pick. Cannot be changed once game has started",
    exclusive_picks="Only allow each stock to be picked by one player. Requires a deadline on or before the start date",
    # sell_during_game="Whether users can sell stocks during the game. Cannot be changed once game has started",  # not implemented yet
    # update_frequency="How often prices should update ('daily', 'hourly')", #, 'minute', 'realtime')"
)
async def manage_game(
    interaction: discord.Interaction, 
    game_id: str,
    name: app_commands.Range[str, 1, name_cutoff] | None = None,
    owner: discord.User | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    clear_end_date: bool = False,
    starting_money: app_commands.Range[int, 1, 1000000000000] | None = None,
    total_picks: app_commands.Range[int, 1, 1000] | None = None,
    pick_date: str | None = None,
    clear_pick_date: bool = False,
    private_game: bool | None = None,
    exclusive_picks: bool | None = None,
    # sell_during_game: bool | None = None,  # not implemented yet
    # update_frequency: Literal['daily', 'hourly'] | None = None
):
    
    try:
        game_info = fe.game_info(game_id, False)
    except LookupError:
        embed = discord.Embed(
            title="Game Not Found",
            description=f"Could not find a game with ID {game_id}.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral_test)
        return

    try:
        if end_date and clear_end_date:
            raise ValueError('Choose either a new end date or remove the existing one, not both.')
        if pick_date and clear_pick_date:
            raise ValueError('Choose either a new pick deadline or remove the existing one, not both.')
        if owner is not None:
            fe.register(owner.id, username=owner.display_name)

        fe.manage_game(
            user_id=interaction.user.id,
            game_id=game_id,
            name=name,
            owner=owner.id if owner else None,
            start_date=start_date,
            end_date=end_date,
            starting_money=starting_money,
            pick_date=pick_date,
            private_game=private_game,
            total_picks=total_picks,
            exclusive_picks=exclusive_picks,
            # update_frequency=update_frequency,
            # sell_during_game=sell_during_game,  # not implemented yet
            clear_end_date=clear_end_date,
            clear_pick_date=clear_pick_date,
        )

        embed = discord.Embed(
            title="Game Updated Successfully",
            description=f"Game #{game_id} has been updated!",
            color=discord.Color.green()
        )
        
    except ValueError as e: # Should catch issues
        embed = discord.Embed(
            title="Game Update Failed",
            description=str(e),
            color=discord.Color.red()
        )
    except Exception as e:
        logger.exception("Game update failed", exc_info=e)
        embed = discord.Embed(
            title="Game Update Failed",
            description="Unable to update the game. Please try again or contact a moderator.",
            color=discord.Color.red()
        )

    await interaction.response.send_message(embed=embed, ephemeral=ephemeral_test)

@bot.tree.command(name="invite", description="Invite a user to a game (requires their DMs from this server)")
@app_commands.autocomplete(game_id=ac.all_games_autocomplete)
@app_commands.describe(
    game_id="ID of the game to invite them to",
    user="User to invite (must allow DMs from server members for the invite to arrive)"
)
async def invite_user(
    interaction: discord.Interaction, 
    game_id: str,
    user: discord.User
):
    await interaction.response.defer(ephemeral=ephemeral_test) # Defer the response to allow time for the update

    try:
        invited_game = fe.be.get_game(game_id)
    except LookupError:
        await interaction.followup.send(
            embed=simple_embed(status='failed', title='Game Not Found', desc=f'No game exists with ID #{game_id}.'),
            ephemeral=ephemeral_test,
        )
        return

    if invited_game.private_game and interaction.user.id != invited_game.owner_id:
        await interaction.followup.send(
            embed=simple_embed(
                status='failed',
                title='Not Allowed',
                desc='Only the game owner can invite players to a private game.',
            ),
            ephemeral=ephemeral_test,
        )
        return

    invite_embed = discord.Embed(
        title="Game Invite",
        description=f"You have been invited to **{invited_game.name}** (#{game_id}) by {interaction.user.display_name}.",
        color=discord.Color.green()
    )

    accept_button = discord.ui.Button(
        label="Accept Invite",
        style=discord.ButtonStyle.success,
        custom_id="accept_invite",
        emoji="✅"
    )

    decline_button = discord.ui.Button(
        label="Decline Invite",
        style=discord.ButtonStyle.danger,
        custom_id="decline_invite",
        emoji="❌"
    )

    view = discord.ui.View()
    view.add_item(accept_button)    
    view.add_item(decline_button)

    async def accept_invite_callback(button_interaction: discord.Interaction):
        # Validate that the clicker is the invited user
        if button_interaction.user.id != user.id:
            await button_interaction.response.send_message(
                "This invite was not meant for you.", ephemeral=True
            )
            return

        try:
            fe.register(user_id=user.id, username=user.display_name)
            fe.join_game(
                user_id=user.id,
                game_id=game_id,
                force_active=bool(invited_game.private_game),
            )
            participant = fe.be.get_many_participants(user_id=user.id, game_id=game_id)[0]
            if participant.status == 'pending':
                title = 'Join Request Submitted'
                description = 'Your request is pending owner approval before you can play.'
            else:
                title = 'Game Joined'
                description = f'You joined **{invited_game.name}** (#{game_id}).'
            accept_embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.green(),
            )
        except ValueError as exc:
            message = str(exc)
            if 'already in game' in message.lower():
                message = 'You are already participating in this game.'
            elif 'pick_date' in message.lower():
                message = 'The pick deadline for this game has passed.'
            accept_embed = simple_embed(status='failed', title='Game Join Failed', desc=message)
        except LookupError:
            accept_embed = simple_embed(status='failed', title='Game Join Failed', desc='This game is no longer available.')
        except Exception as exc:
            logger.exception('Invite acceptance failed for user %s and game %s.', user.id, game_id, exc_info=exc)
            accept_embed = discord.Embed(
                title="Game Join Failed",
                description='Unable to join the game. Please try again or contact a moderator.',
                color=discord.Color.red(),
            )

        await button_interaction.response.edit_message(embed=accept_embed, view=None)

    async def decline_invite_callback(interaction: discord.Interaction):
        if interaction.user.id != user.id:
            await interaction.response.send_message("This invite was not meant for you.", ephemeral=True)
            return
        decline_embed = discord.Embed(
            title="Invite Declined",
            description=f"You have declined the invite to game #{game_id}.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=decline_embed, view=None)

    accept_button.callback = accept_invite_callback  # type: ignore[assignment]
    decline_button.callback = decline_invite_callback

    try:
        await user.send(embed=invite_embed, view=view)
        await interaction.followup.send(
            embed=discord.Embed(
                title='Invite Sent',
                description=f'Invite sent to {user.mention}.',
                color=discord.Color.blue(),
            ),
            ephemeral=ephemeral_test,
        )
    except discord.Forbidden:
        if invited_game.private_game:
            description = f"{user.mention} has DMs disabled. Private-game details were not posted publicly; ask them to enable DMs or send them the game ID privately."
            await interaction.followup.send(
                embed=simple_embed(status='failed', title='Invite Not Delivered', desc=description),
                ephemeral=ephemeral_test,
            )
            return
        if interaction.channel is None:
            await interaction.followup.send(
                embed=simple_embed(status='failed', title='Invite Not Delivered', desc=f"{user.mention} has DMs disabled and no channel is available for a public invite."),
                ephemeral=ephemeral_test,
            )
            return
        try:
            channel = cast(discord.abc.Messageable, interaction.channel)
            await channel.send(
                f"{user.mention}, {interaction.user.display_name} invited you to **{invited_game.name}** (#{game_id}). Use `/join-game {game_id}` to join."
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                embed=simple_embed(status='failed', title='Invite Not Delivered', desc=f"{user.mention} has DMs disabled and I could not post an in-channel invite."),
                ephemeral=ephemeral_test,
            )
        else:
            await interaction.followup.send(
                embed=discord.Embed(
                    title='Invite Posted in Channel',
                    description=f'{user.mention} has DMs disabled, so the public-game invite was posted here.',
                    color=discord.Color.blue(),
                ),
                ephemeral=ephemeral_test,
            )

    except Exception as e:
        logger.exception(f'User: {interaction.user.id} tried to invite user: {user.id} to game: {game_id}. Error: {e}')
        error_embed = discord.Embed(
            title="Invite Failed",
            description=f"An unexpected error occurred while trying to invite {user.mention} to game #{game_id}.",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=error_embed, ephemeral=ephemeral_test)

@bot.tree.command(name="manage-pending", description="Approve or deny pending users for your private game")
@app_commands.autocomplete(game_id=ac.owner_games_autocomplete)
@app_commands.describe(
    game_id="ID of the game to manage pending users for"
)
async def manage_pending(
    interaction: discord.Interaction,
    game_id: str
):
    await interaction.response.defer(ephemeral=ephemeral_test)
    
    try:
        # Get pending users for the game
        pending_users = fe.pending_game_users(
            user_id=interaction.user.id,
            game_id=game_id
        )
        
        if not pending_users:
            embed = discord.Embed(
                title="No Pending Users",
                description=f"There are no pending users for game #{game_id}.",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)
            return
        
        # Start the approval process with the first pending user
        await process_pending_user(interaction, game_id, list(pending_users), 0)
        
    except PermissionError:
        embed = discord.Embed(
            title="Permission Denied",
            description="You don't have permission to manage pending users for this game.",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)
    except Exception as e:
        logger.exception(f'User: {interaction.user.id} failed to get pending users for game {game_id}. Error: {e}')
        embed = discord.Embed(
            title="Error",
            description=f"An unexpected error occurred while getting pending users. Please try again or contact a moderator.",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)
  
@bot.tree.command(name="kick-player", description="Kick a player from your private game")
@app_commands.autocomplete(game_id=ac.private_owner_games_autocomplete)
@app_commands.describe(
    game_id="Private game ID",
    user="Player to kick",
)
async def kick_player(
    interaction: discord.Interaction,
    game_id: str,
    user: discord.User,
):
    await interaction.response.defer(ephemeral=ephemeral_test)
    target_user_id = user.id

    try:
        game = fe.be.get_game(game_id)
    except LookupError:
        await interaction.followup.send(
            embed=simple_embed(status='failed', title='Game Not Found', desc=f'No game exists with ID #{game_id}.'),
            ephemeral=ephemeral_test,
        )
        return

    if interaction.user.id != game.owner_id and not is_moderator(interaction):
        await interaction.followup.send(
            embed=simple_embed(
                status='failed',
                title='Not Allowed',
                desc='Only the game owner or a moderator can kick players from this game.',
            ),
            ephemeral=ephemeral_test,
        )
        return

    try:
        await asyncio.to_thread(
            fe.kick_player,
            user_id=interaction.user.id,
            game_id=game_id,
            target_user_id=target_user_id,
            enforce_permissions=not is_moderator(interaction),
        )
        await interaction.followup.send(
            embed=simple_embed(
                status='success',
                title='Player Kicked',
                desc=f'{user.mention} was removed from **{game.name}** (#{game_id}).',
            ),
            ephemeral=ephemeral_test,
        )
    except NotAllowedError as exc:
        await interaction.followup.send(
            embed=simple_embed(
                status='failed',
                title='Cannot Kick Player',
                desc=exc.message or str(exc),
            ),
            ephemeral=ephemeral_test,
        )
    except PermissionError as exc:
        await interaction.followup.send(
            embed=simple_embed(status='failed', title='Not Allowed', desc=str(exc)),
            ephemeral=ephemeral_test,
        )
    except (DoesntExistError, LookupError):
        await interaction.followup.send(
            embed=simple_embed(
                status='failed',
                title='Player Not Found',
                desc=f'{user.mention} is not participating in game #{game_id}.',
            ),
            ephemeral=ephemeral_test,
        )
    except Exception as exc:
        logger.exception(
            'Kick failed | actor=%s game=%s target=%s',
            interaction.user.id,
            game_id,
            target_user_id,
            exc_info=exc,
        )
        await interaction.followup.send(
            embed=simple_embed(
                status='failed',
                title='Kick Failed',
                desc='An unexpected error occurred while kicking that player.',
            ),
            ephemeral=ephemeral_test,
        )


class RecurringTemplateManager(discord.ui.View):
    """Paginate through recurring templates one at a time with stop/delete."""

    def __init__(self, interaction: discord.Interaction, templates: list):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.templates = list(templates)
        self.index = 0
        self.confirming_delete = False
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.interaction.user.id:
            return True
        await interaction.response.send_message(
            "Only the moderator who ran this command can use these controls.",
            ephemeral=True,
        )
        return False

    def _sync_buttons(self) -> None:
        self.clear_items()
        if self.confirming_delete:
            confirm = discord.ui.Button(label="Confirm Delete", style=discord.ButtonStyle.danger)
            cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
            confirm.callback = self._confirm_delete  # type: ignore[method-assign]
            cancel.callback = self._cancel_delete  # type: ignore[method-assign]
            self.add_item(confirm)
            self.add_item(cancel)
            return

        prev_btn = discord.ui.Button(emoji="◀️", style=discord.ButtonStyle.blurple, disabled=self.index <= 0)
        next_btn = discord.ui.Button(
            emoji="▶️",
            style=discord.ButtonStyle.blurple,
            disabled=self.index >= len(self.templates) - 1,
        )
        delete_btn = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)

        stopped = bool(self.templates and self.templates[self.index].status == "disabled")
        if stopped:
            toggle_btn = discord.ui.Button(label="Resume", style=discord.ButtonStyle.success)
            toggle_btn.callback = self._resume  # type: ignore[method-assign]
        else:
            toggle_btn = discord.ui.Button(label="Stop", style=discord.ButtonStyle.secondary)
            toggle_btn.callback = self._stop  # type: ignore[method-assign]

        push_on = bool(self.templates and self.templates[self.index].push_leaderboard)
        if push_on:
            push_btn = discord.ui.Button(label="Disable Push", style=discord.ButtonStyle.secondary)
            push_btn.callback = self._disable_push  # type: ignore[method-assign]
            channel_btn = discord.ui.Button(label="Set Channel", style=discord.ButtonStyle.primary)
            channel_btn.callback = self._set_channel  # type: ignore[method-assign]
        else:
            push_btn = discord.ui.Button(label="Enable Push", style=discord.ButtonStyle.success)
            push_btn.callback = self._enable_push  # type: ignore[method-assign]
            channel_btn = None

        prev_btn.callback = self._previous  # type: ignore[method-assign]
        next_btn.callback = self._next  # type: ignore[method-assign]
        delete_btn.callback = self._ask_delete  # type: ignore[method-assign]
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(toggle_btn)
        self.add_item(push_btn)
        if channel_btn is not None:
            self.add_item(channel_btn)
        self.add_item(delete_btn)

    def _pick_deadline_text(self, template) -> str:
        if template.pick_date is None:
            return "None — buy anytime"
        if template.pick_date > 0:
            return f"{template.pick_date} days before each game start"
        if template.pick_date < 0:
            return f"{abs(template.pick_date)} days after each game start"
        return "On each game start date"

    def build_embed(self) -> discord.Embed:
        if not self.templates:
            return discord.Embed(
                title="Manage Recurring Games",
                description="No recurring templates left.",
                color=discord.Color.orange(),
            )

        template = self.templates[self.index]
        status_label = "Enabled" if template.status == "enabled" else "Stopped"
        length = "Infinite" if template.game_length == 0 else f"{template.game_length} months"
        embed = discord.Embed(
            title=f"📋 {template.name}",
            description=(
                f"Template **{self.index + 1}** of **{len(self.templates)}**\n"
                f"**Status:** {status_label}\n\n"
                "**Stop** — do not create future games; games already created keep running until they end.\n"
                "**Resume** — start creating future games again on the normal schedule.\n"
                "**Delete** — remove this template from the database (existing games stay)."
            ),
            color=discord.Color.blue() if template.status == "enabled" else discord.Color.dark_grey(),
        )
        embed.add_field(name="🔄 Recurring Every", value=f"{template.recurring_period} months", inline=True)
        embed.add_field(name="📅 First Start", value=str(template.start_date), inline=True)
        embed.add_field(name="⏱️ Game Length", value=length, inline=True)
        embed.add_field(name="⏰ Create Early", value=f"{template.create_days_in_advance} days", inline=True)
        embed.add_field(name="💰 Starting", value=f"${template.start_money:,.0f}", inline=True)
        embed.add_field(name="📊 Picks", value=str(template.pick_count), inline=True)
        embed.add_field(name="📝 Pick Deadline", value=self._pick_deadline_text(template), inline=True)
        embed.add_field(name="🔒 Private", value="Yes" if template.private_game else "No", inline=True)
        embed.add_field(name="🎯 Exclusive", value="Yes" if template.draft_mode else "No", inline=True)
        push_txt = "Off"
        if template.push_leaderboard:
            ch = template.leaderboard_channel_id
            push_txt = f"On → <#{ch}>" if ch else "On (no channel yet)"
        embed.add_field(name="📣 Push Leaderboard", value=push_txt, inline=False)
        if self.interaction.user.id != template.owner_id:
            embed.add_field(name="👤 Owner", value=f"<@{template.owner_id}>", inline=True)
        embed.set_footer(text=f"Template ID: {template.id}")
        return embed

    def build_delete_confirm_embed(self) -> discord.Embed:
        template = self.templates[self.index]
        return discord.Embed(
            title="Delete template?",
            description=(
                f"Permanently delete recurring template **{template.name}**?\n\n"
                "Existing games created from it will keep running, but no new games will be created.\n"
                "Confirm or Cancel — either way you will move to the next template."
            ),
            color=discord.Color.red(),
        )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self.confirming_delete = False
        self._sync_buttons()
        if not self.templates:
            await interaction.response.edit_message(embed=self.build_embed(), view=None)
            self.stop()
            return
        if self.index >= len(self.templates):
            self.index = len(self.templates) - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _advance_after_delete_prompt(
        self,
        interaction: discord.Interaction,
        *,
        deleted: bool,
    ) -> None:
        self.confirming_delete = False
        if deleted:
            # Index now points at what used to be the next template.
            if self.index >= len(self.templates):
                self.index = max(0, len(self.templates) - 1)
        # A cancellation advances when possible so moderators can quickly
        # review the next template without reopening the command.
        elif self.index < len(self.templates) - 1:
            self.index += 1
        self._sync_buttons()
        if not self.templates:
            await interaction.response.edit_message(embed=self.build_embed(), view=None)
            self.stop()
            return
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.index = max(0, self.index - 1)
        await self._refresh(interaction)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.index = min(len(self.templates) - 1, self.index + 1)
        await self._refresh(interaction)

    async def _stop(self, interaction: discord.Interaction) -> None:
        template = self.templates[self.index]
        try:
            fe.be.update_game_template(template_id=template.id, status="disabled")
            self.templates[self.index] = fe.be.get_game_template(template.id)
            self.confirming_delete = False
            self._sync_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send(
                f"🛑 **{template.name}** stopped. No new games will be created; "
                "games already in progress will finish normally.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.exception(
                "manage-recurring-games stop failed | user=%s template_id=%s",
                interaction.user.id,
                template.id,
                exc_info=exc,
            )
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Failed to stop this template. Please try again or contact a moderator.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to stop this template. Please try again or contact a moderator.",
                    ephemeral=True,
                )

    async def _resume(self, interaction: discord.Interaction) -> None:
        template = self.templates[self.index]
        try:
            fe.be.update_game_template(template_id=template.id, status="enabled")
            self.templates[self.index] = fe.be.get_game_template(template.id)
            self.confirming_delete = False
            self._sync_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send(
                f"▶️ **{template.name}** resumed. New games will be created again on schedule.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.exception(
                "manage-recurring-games resume failed | user=%s template_id=%s",
                interaction.user.id,
                template.id,
                exc_info=exc,
            )
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Failed to resume this template. Please try again or contact a moderator.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to resume this template. Please try again or contact a moderator.",
                    ephemeral=True,
                )

    async def _refresh_after_push_change(self) -> None:
        """Reload the current template and repaint the manager message."""
        try:
            template = self.templates[self.index]
            self.templates[self.index] = fe.be.get_game_template(template.id)
            self._sync_buttons()
            message = await self.interaction.original_response()
            await message.edit(embed=self.build_embed(), view=self)
        except Exception:
            logger.debug("Could not refresh template view after push change.", exc_info=True)

    async def _enable_push(self, interaction: discord.Interaction) -> None:
        template = self.templates[self.index]
        await interaction.response.send_message(
            f"Pick a channel for **{template.name}** leaderboard posts:",
            view=LeaderboardChannelSelect(template.id, on_saved=self._refresh_after_push_change),
            ephemeral=True,
        )

    async def _disable_push(self, interaction: discord.Interaction) -> None:
        template = self.templates[self.index]
        try:
            fe.be.update_game_template(
                template_id=template.id,
                push_leaderboard=False,
                clear_leaderboard_channel=True,
            )
            self.templates[self.index] = fe.be.get_game_template(template.id)
            self._sync_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send(
                f"📣 Leaderboard push disabled for **{template.name}**.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.exception("disable push failed | template_id=%s", template.id, exc_info=exc)
            await interaction.response.send_message("❌ Failed to disable push.", ephemeral=True)

    async def _set_channel(self, interaction: discord.Interaction) -> None:
        template = self.templates[self.index]
        await interaction.response.send_message(
            f"Pick a new channel for **{template.name}**:",
            view=LeaderboardChannelSelect(template.id, on_saved=self._refresh_after_push_change),
            ephemeral=True,
        )

    async def _ask_delete(self, interaction: discord.Interaction) -> None:
        self.confirming_delete = True
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_delete_confirm_embed(), view=self)

    async def _confirm_delete(self, interaction: discord.Interaction) -> None:
        template = self.templates[self.index]
        try:
            fe.be.remove_game_template(template_id=template.id)
            del self.templates[self.index]
            await self._advance_after_delete_prompt(interaction, deleted=True)
            await interaction.followup.send(f"🗑️ Deleted template **{template.name}**.", ephemeral=True)
        except Exception as exc:
            logger.exception(
                "manage-recurring-games delete failed | user=%s template_id=%s",
                interaction.user.id,
                template.id,
                exc_info=exc,
            )
            self.confirming_delete = False
            self._sync_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send(
                "❌ Failed to delete this template. Please try again or contact a moderator.",
                ephemeral=True,
            )

    async def _cancel_delete(self, interaction: discord.Interaction) -> None:
        await self._advance_after_delete_prompt(interaction, deleted=False)

    async def on_timeout(self) -> None:
        try:
            message = await self.interaction.original_response()
            await message.edit(view=None)
        except discord.HTTPException:
            logger.debug('Could not remove controls from an expired recurring-template view.', exc_info=True)


@bot.tree.command(name="leave-game", description="Leave a game you are participating in")
@app_commands.autocomplete(game_id=ac.all_games_autocomplete)
@app_commands.describe(
    game_id="ID of the game to leave"
)
async def leave_game(
    interaction: discord.Interaction,
    game_id: str,
):
    await interaction.response.defer(ephemeral=ephemeral_test)
    try:
        await asyncio.to_thread(fe.leave_game, interaction.user.id, game_id)
        await interaction.followup.send(
            embed=simple_embed(
                status='success',
                title='Left Game',
                desc=f'You have left game #{game_id}. Your associated picks were removed.',
            ),
            ephemeral=ephemeral_test,
        )
    except PermissionError:
        await interaction.followup.send(
            embed=simple_embed(
                status='failed',
                title='Cannot Leave Game',
                desc='Game owners must transfer ownership or delete the game instead.',
            ),
            ephemeral=ephemeral_test,
        )
    except DoesntExistError:
        await interaction.followup.send(
            embed=simple_embed(status='failed', title='Not in Game', desc=f'You are not participating in game #{game_id}.'),
            ephemeral=ephemeral_test,
        )
    except LookupError:
        await interaction.followup.send(
            embed=simple_embed(status='failed', title='Not in Game', desc=f"You are not in game #{game_id}."),
            ephemeral=ephemeral_test,
        )
    except Exception as e:
        logger.exception(f'User {interaction.user.id} failed to leave game {game_id}', exc_info=e)
        await interaction.followup.send(
            embed=simple_embed(status='failed', title='Error', desc='An unexpected error occurred while leaving the game.'),
            ephemeral=ephemeral_test,
        )

@bot.tree.command(name="manage-recurring-games", description="Browse, stop, or delete recurring templates")
@app_commands.default_permissions()
async def manage_recurring_games(interaction: discord.Interaction):
    """Paginate recurring templates; moderators see every template in the DB."""
    try:
        try:
            templates = fe.be.get_many_game_templates(status=None)
        except LookupError:
            templates = ()
        if is_moderator(interaction):
            visible_templates = list(templates)
        else:
            visible_templates = [t for t in templates if t.owner_id == interaction.user.id]
        if not visible_templates:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Manage Recurring Games",
                    description="You haven't created any recurring game templates yet.",
                    color=discord.Color.orange(),
                ),
                ephemeral=ephemeral_test,
            )
            return

        view = RecurringTemplateManager(interaction, visible_templates)
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=ephemeral_test,
        )
    except Exception as e:
        logger.exception(
            "manage-recurring-games failed | user=%s",
            interaction.user.id,
            exc_info=e,
        )
        await interaction.response.send_message(
            "❌ Unable to load recurring templates. Please try again or contact a moderator.",
            ephemeral=ephemeral_test,
        )


@bot.tree.command(name="update", description="Force-update all stock prices and portfolios")
@app_commands.default_permissions()
@app_commands.describe(
    # A future command option may expose targeted updates; the backend supports it.
)
async def update(
    interaction: discord.Interaction, 
    # game_id: str,
):
    if not is_moderator(interaction):
        await interaction.response.send_message(
            embed=simple_embed(
                status='failed',
                title='Not Allowed',
                desc='Only moderators in the home server can force-update all games.',
            ),
            ephemeral=ephemeral_test,
        )
        return
    await interaction.response.defer(ephemeral=ephemeral_test) # Defer the response to allow time for the update
    embed = discord.Embed()
    try:
        async with _game_update_lock:
            await asyncio.to_thread(
                fe.force_update,
                user_id=interaction.user.id,
                enforce_permissions=False,
            )
            await push_all_recurring_leaderboards(bot, fe, name_resolver=resolve_player_name)
        embed.title = "Success"
        embed.description = f"All games have been successfully updated"
        embed.color = discord.Color.green()
    except PermissionError:
        embed.title = "Failed"
        embed.description = "You do not have permission to update this game"
        embed.color = discord.Color.red()
    except Exception as e:
        embed.title = "Failed"
        embed.description = f"There was an error while executing this command. Please try again or contact a moderator."
        embed.color = discord.Color.red()

    await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)


# STOCK RELATED

@bot.tree.command(name="buy-stock", description="Buy a stock in a game")
@app_commands.autocomplete(game_id=ac.all_games_autocomplete, ticker=ac.buy_ticker_autocomplete)
@app_commands.describe(
    game_id="ID of the game",
    ticker="Stock ticker symbol"
)
async def buy_stock(
    interaction: discord.Interaction, 
    game_id: str, 
    ticker: str
):
    await interaction.response.defer(ephemeral=ephemeral_test) # Defer the response to allow time for the update
    status = 'failed' # Start with failed status
    title = 'Stock Purchase Failed'
    try:
        ticker = ticker.upper()
        await asyncio.to_thread(
            fe.buy_stock,
            user_id=interaction.user.id,
            game_id=game_id,
            ticker=ticker,
        )
        remaining, total = fe.pick_capacity(interaction.user.id, game_id)
        title = 'Stock Purchased'
        description = f'Added {ticker} to game #{game_id}. {remaining} of {total} picks remaining.'
        status = 'success'

    except ValueError as exc:
        if 'Invalid Ticker, too long!' in str(exc):
            description = f'The ticker {ticker} is not valid!'
        
        elif 'Stock is not tradeable' in str(exc):
            description = f'The ticker {ticker} is not tradeable.  This can occur when a stock is private or has been delisted.'
            
        elif 'Unable to find stock' in str(exc) or 'Failed to add `ticker`' in str(exc):
            description = f'The ticker {ticker} was not found.  Double check your spelling and try again!'
        
        else:
            logger.exception(f'Uncaught value error user: {interaction.user.id} tried to buy stock with ticker: {ticker}', exc_info=exc)
            description = 'An error ocurred while finding your stock.'
    
    except LookupError:
        description = f'No game with ID {game_id} found.'
    
    except NotAllowedError as exc: # REASONS ARE NOW IN THE DOCSTRING OF buy_stock!!
        if exc.reason == 'Not active':
            try:
                participant = fe.be.get_many_participants(user_id=interaction.user.id, game_id=game_id)[0]
                if participant.status == 'pending':
                    description = 'Your request to join this private game is still awaiting owner approval.'
                else:
                    description = f'You are not currently allowed to buy stocks in game #{game_id}.'
            except (LookupError, IndexError):
                description = f'You are not currently allowed to buy stocks in game #{game_id}.'
        
        elif exc.reason == 'Maximum picks reached':
            title="Game Pick Limit Reached"
            description = f'You have reached the maximum number of picks for this game.\nTo add another stock, you need to remove one of your current picks.'
        
        elif exc.reason == 'Past pick_date':
            description = f'The pick date for this game has passed, so you can no longer pick stocks.'
    
    except AlreadyExistsError as exc:
        description = f'You already own {ticker} in this game!'
        
    except DoesntExistError as exc: # Player isnt in the game at all
        if exc.table == 'game_participants':
            description = f'You are not in the game: {game_id}.'

    except Exception as e: # Other unexpeted errors
        logger.exception(f'User: {interaction.user.id} tried to buy the stock: {ticker} in game: {game_id}. Error: {e}')
        description='An unexpected error occurred while trying to buy the stock. Please try again or contact a moderator.'
            
    await interaction.followup.send(
        embed=simple_embed( # This just creates the status message
            status = status,
            title = title,
            desc = description
            ), 
        ephemeral=ephemeral_test
        )


# Selling is not implemented yet — keep the command commented for later use.
# @bot.tree.command(name="sell-stock", description="Sell an owned stock or cancel a pending buy")
# @app_commands.autocomplete(game_id=ac.all_games_autocomplete, ticker=ac.sell_ticker_autocomplete)
# @app_commands.describe(game_id="ID of the game", ticker="Stock ticker symbol")
# async def sell_stock(interaction: discord.Interaction, game_id: str, ticker: str):
#     await interaction.response.defer(ephemeral=ephemeral_test)
#     ticker = ticker.upper().strip()
#     try:
#         result = await asyncio.to_thread(
#             fe.sell_stock,
#             user_id=interaction.user.id,
#             game_id=game_id,
#             ticker=ticker,
#         )
#         if result == 'cancelled':
#             title = 'Purchase Cancelled'
#             description = f'Cancelled your pending purchase of {ticker} in game #{game_id}.'
#         elif result == 'sell_requested':
#             title = 'Sale Requested'
#             description = f'Sale of {ticker} was requested. It will complete on the next portfolio update.'
#         else:
#             title = 'Sale Already Requested'
#             description = f'A sale of {ticker} is already waiting for the next portfolio update.'
#         embed = simple_embed(status='success', title=title, desc=description)
#     except NotAllowedError as exc:
#         description = 'Selling is not enabled for this game.' if exc.reason == 'Selling disabled' else 'You are not allowed to sell this stock.'
#         embed = simple_embed(status='failed', title='Stock Sale Failed', desc=description)
#     except DoesntExistError:
#         embed = simple_embed(status='failed', title='Not in Game', desc=f'You are not participating in game #{game_id}.')
#     except LookupError:
#         embed = simple_embed(status='failed', title='Stock Sale Failed', desc=f'You do not have a matching {ticker} pick in game #{game_id}.')
#     except ValueError as exc:
#         embed = simple_embed(status='failed', title='Stock Sale Failed', desc=str(exc) or 'The sale could not be processed.')
#     except Exception as exc:
#         logger.exception('Stock sale failed for user %s in game %s.', interaction.user.id, game_id, exc_info=exc)
#         embed = simple_embed(status='failed', title='Stock Sale Failed', desc='Unable to process the sale. Please try again or contact a moderator.')
#     await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)


@bot.tree.command(name="remove-stock", description="Cancel a pending stock purchase")
@app_commands.autocomplete(game_id=ac.all_games_autocomplete, ticker=ac.sell_ticker_autocomplete)
@app_commands.describe(game_id="ID of the game", ticker="Pending stock ticker to cancel")
async def remove_stock(interaction: discord.Interaction, game_id: str, ticker: str):
    await interaction.response.defer(ephemeral=ephemeral_test)
    ticker = ticker.upper().strip()
    try:
        await asyncio.to_thread(
            fe.remove_pick,
            user_id=interaction.user.id,
            game_id=game_id,
            ticker=ticker,
        )
        remaining, total = fe.pick_capacity(interaction.user.id, game_id)
        embed = simple_embed(
            status='success',
            title='Pending Purchase Cancelled',
            desc=f'Cancelled {ticker} in game #{game_id}. {remaining} of {total} picks remaining.',
        )
    except DoesntExistError:
        embed = simple_embed(status='failed', title='Not in Game', desc=f'You are not participating in game #{game_id}.')
    except LookupError:
        embed = simple_embed(status='failed', title='No Pending Purchase', desc=f'No pending purchase of {ticker} was found in game #{game_id}.')
    except ValueError as exc:
        embed = simple_embed(
            status='failed',
            title='Cannot Cancel Purchase',
            desc=str(exc),
            # desc=f'{exc} Use /sell-stock only when selling is enabled for an owned stock.',  # not implemented yet
        )
    except Exception as exc:
        logger.exception('Pending-purchase cancellation failed for user %s in game %s.', interaction.user.id, game_id, exc_info=exc)
        embed = simple_embed(status='failed', title='Purchase Cancellation Failed', desc='Unable to cancel that pending purchase. Please try again.')
    await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)

# TODO Add buttons for buying stocks?
# TODO Add buttons for buying/selling stocks?  # selling not implemented yet

class UserLeaderboardView(discord.ui.View):
    """Browse games with outer controls and rank pages with inner controls."""

    def __init__(
        self,
        interaction: discord.Interaction,
        games: list[dict],
        *,
        show_game_controls: bool = True,
    ):
        super().__init__(timeout=600)
        self.interaction = interaction
        self.games = games
        self.game_index = 0
        self.rank_page_index = 0
        self.show_game_controls = show_game_controls
        self._sync_buttons()

    @property
    def current_game(self) -> dict:
        return self.games[self.game_index]

    @property
    def rank_pages(self) -> dict[int, dict]:
        return self.current_game["rank_pages"]

    @property
    def rank_page_count(self) -> int:
        return self.current_game["rank_page_count"]

    def _sync_buttons(self) -> None:
        self.clear_items()

        if self.show_game_controls:
            previous_game = discord.ui.Button(
                label="Previous game",
                style=discord.ButtonStyle.blurple,
                disabled=self.game_index <= 0,
            )
            previous_game.callback = self._previous_game  # type: ignore[method-assign]
            self.add_item(previous_game)

        previous_page = discord.ui.Button(
            label="Previous page",
            style=discord.ButtonStyle.secondary,
            disabled=self.rank_page_index <= 0,
        )
        next_page = discord.ui.Button(
            label="Next page",
            style=discord.ButtonStyle.secondary,
            disabled=self.rank_page_index >= self.rank_page_count - 1,
        )
        previous_page.callback = self._previous_rank_page  # type: ignore[method-assign]
        next_page.callback = self._next_rank_page  # type: ignore[method-assign]
        self.add_item(previous_page)
        self.add_item(next_page)

        if self.show_game_controls:
            next_game = discord.ui.Button(
                label="Next game",
            style=discord.ButtonStyle.blurple,
                disabled=self.game_index >= len(self.games) - 1,
            )
            next_game.callback = self._next_game  # type: ignore[method-assign]
            self.add_item(next_game)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.interaction.user.id:
            return True
        await interaction.response.send_message("Only you can flip these pages.", ephemeral=True)
        return False

    def _page_payload(self) -> tuple[discord.Embed, discord.File]:
        game = self.current_game
        page = self.rank_pages[self.rank_page_index]
        base_embed = game.get("embed")
        embed = base_embed.copy() if isinstance(base_embed, discord.Embed) else discord.Embed(
            title=game["title"],
            description=game.get("description"),
            color=discord.Color.blurple(),
        )
        notice = _prestart_notice(game.get("game"))
        if notice:
            existing = embed.description or ""
            embed.description = f"{notice}\n{existing}" if existing else notice
        footer_parts = []
        if self.show_game_controls:
            footer_parts.append(f"Game {self.game_index + 1} of {len(self.games)}")
        footer_parts.append(
            f"Leaderboard page {self.rank_page_index + 1} of {self.rank_page_count}"
        )
        footer_parts.append(
            f"Ranks {page['rank_start']}-{page['rank_end']}"
            if page["rank_end"]
            else "No ranked participants"
        )
        embed.set_footer(text=" | ".join(footer_parts))
        embed.set_image(url=f"attachment://{page['filename']}")
        file = discord.File(io.BytesIO(page["png"]), filename=page["filename"])
        return embed, file

    async def prepare(self) -> None:
        """Generate only the currently selected rank page."""
        if self.rank_page_index in self.rank_pages:
            return
        self.rank_pages[self.rank_page_index] = await _build_rank_page(
            self.current_game["game"],
            self.current_game["leaderboard"],
            self.interaction.guild,
            self.rank_page_index,
        )

    async def _edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.prepare()
        self._sync_buttons()
        embed, file = self._page_payload()
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    async def _previous_game(self, interaction: discord.Interaction) -> None:
        self.game_index = max(0, self.game_index - 1)
        self.rank_page_index = 0
        await self._edit(interaction)

    async def _next_game(self, interaction: discord.Interaction) -> None:
        self.game_index = min(len(self.games) - 1, self.game_index + 1)
        self.rank_page_index = 0
        await self._edit(interaction)

    async def _previous_rank_page(self, interaction: discord.Interaction) -> None:
        self.rank_page_index = max(0, self.rank_page_index - 1)
        await self._edit(interaction)

    async def _next_rank_page(self, interaction: discord.Interaction) -> None:
        self.rank_page_index = min(self.rank_page_count - 1, self.rank_page_index + 1)
        await self._edit(interaction)

    async def on_timeout(self) -> None:
        try:
            message = await self.interaction.original_response()
            await message.edit(view=None)
        except discord.HTTPException:
            pass


async def resolve_player_name(user_id: int, guild: discord.Guild | None) -> str:
    """Live Discord name for leaderboard rows, trimmed to the image's 16-char budget.

    Stored DB display names are unreliable (only set on first registration), so
    always prefer the current guild member / user profile.
    """
    def trim(value: str) -> str:
        return value if len(value) <= 16 else value[:15] + "~"

    member: discord.Member | None = None
    if guild is not None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None
    if member is not None:
        for candidate in (member.display_name, member.global_name, member.name):
            if candidate and len(candidate) <= 16:
                return candidate
        return trim(member.global_name or member.name)

    try:
        discord_user = await bot.fetch_user(user_id)
    except (discord.HTTPException, LookupError):
        return f"ID({user_id})"
    return trim(discord_user.display_name or discord_user.name)


def _cached_game_info_leaderboard_png(
    cache_key: str,
    game_data: dict,
    processed_leaderboard: list,
    recurring: bool = False,
) -> bytes:
    fingerprint = fingerprint_image_rows(
        game_data.get("id"),
        game_data.get("name"),
        processed_leaderboard,
    )
    now = time.monotonic()
    cached = _leaderboard_image_cache.get(cache_key)
    if (
        cached
        and cached[1] == fingerprint
        and (now - cached[2]) < _LEADERBOARD_CACHE_TTL_SEC
    ):
        return cached[0]
    if recurring:
        buf = get_recurring_generator(max_height=_RECURRING_PAGE_MAX_HEIGHT).create_image(
            game_data,
            processed_leaderboard,
            target_n=max(len(processed_leaderboard), 1),
        )
    else:
        buf = get_leaderboard_generator().create_leaderboard_image(
            game_data, processed_leaderboard
        )
    data = buf.getvalue()
    _store_leaderboard_cache(cache_key, data, fingerprint)
    return data


async def _build_rank_page(
    game,
    leaderboard: list[GameLeaderboard],
    guild: discord.Guild | None,
    page_index: int,
) -> dict:
    """Render one requested rank page, or reuse its cached PNG."""
    recurring = getattr(game, "template_id", None) is not None
    page_size = (
        _RECURRING_LEADERBOARD_RANK_PAGE_SIZE
        if recurring
        else _LEADERBOARD_RANK_PAGE_SIZE
    )
    start = page_index * page_size
    entries = leaderboard[start : start + page_size]
    rank_start = start + 1 if entries else 0
    rank_end = start + len(entries) if entries else 0
    filename = f"leaderboard_{game.id}_{page_index + 1}.png"
    cache_key = f"{game.id}:{page_index}"

    processed: list[dict] = []
    for rank, entry in enumerate(entries, start=rank_start):
        row = {
            "rank": rank,
            "user_id": entry.user_id,
            "display_name": await resolve_player_name(entry.user_id, guild),
            "current_value": entry.current_value,
            "joined": entry.joined,
            "change_dollars": entry.change_dollars,
            "change_percent": entry.change_percent,
            "last_updated": entry.last_updated,
        }
        if recurring:
            row["days_in_first"] = getattr(entry, "days_in_first", 0) or 0
            row["picks"] = (
                await asyncio.to_thread(collect_player_picks, fe, game.id, entry.user_id) or []
            )
        processed.append(row)
    game_data = {
        "name": game.name,
        "id": game.id,
        "owner": game.owner_id,
        "starting_money": game.start_money,
        "start_date": str(game.start_date),
        "end_date": str(game.end_date) if game.end_date else None,
        "status": game.status,
    }
    png = await asyncio.to_thread(
        _cached_game_info_leaderboard_png,
        cache_key,
        game_data,
        processed,
        recurring,
    )
    return {
        "png": png,
        "filename": filename,
        "rank_start": rank_start,
        "rank_end": rank_end,
    }


def _first_buy_approx_unix(game) -> int:
    """Unix timestamp for the approximate first purchase on ``game.start_date``.

    Games flip ``open`` → ``active`` on the Eastern calendar start date during the
    ~15‑minute ``update_all`` loop; pending buys settle once the game is active.
    We estimate that moment near US equity market open (``GameLogic.market_open_est``,
    default 09:30 America/New_York).
    """
    start = game.start_date
    if not hasattr(start, "year"):
        start = datetime.strptime(str(start), "%Y-%m-%d").date()
    open_clock = fe.gl.market_open_est
    eastern = pytz.timezone("America/New_York")
    local = eastern.localize(
        datetime(start.year, start.month, start.day, open_clock.hour, open_clock.minute)
    )
    return int(local.timestamp())


def _prestart_notice(game) -> str | None:
    """Obvious notice for games that have not started (``status == 'open'``)."""
    if game is None or getattr(game, "status", None) != "open":
        return None
    ts = _first_buy_approx_unix(game)
    return (
        f"⚠️⚠️ **This game has not started yet.** All stock picks stay **pending** until "
        f"the first buy settles on the start date: <t:{ts}:D> around <t:{ts}:t> (<t:{ts}:R>). "
        f"Prices will not move until then."
    )


def _leaderboard_game_data(
    game,
    leaderboard: list[GameLeaderboard],
    *,
    title: str | None = None,
    description: str | None = None,
    embed: discord.Embed | None = None,
) -> dict:
    """Create a lazy game descriptor without rendering any images."""
    page_size = (
        _RECURRING_LEADERBOARD_RANK_PAGE_SIZE
        if getattr(game, "template_id", None) is not None
        else _LEADERBOARD_RANK_PAGE_SIZE
    )
    return {
        "game": game,
        "leaderboard": leaderboard,
        "title": title,
        "description": description,
        "embed": embed,
        "rank_page_count": max(
            1,
            (len(leaderboard) + page_size - 1) // page_size,
        ),
        "rank_pages": {},
    }


def _game_info_embed(game, participant_count: int) -> discord.Embed:
    """Complete user-facing game configuration without decorative emoji."""
    aggregate = float(game.current_value or 0)
    dollars = float(game.change_dollars or 0)
    percent = float(game.change_percent or 0)
    embed = discord.Embed(
        title=f"{game.name} ({game.id})",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Overview",
        value=(
            f"Owner: <@{game.owner_id}>\n"
            f"Status: {game.status}\n"
            f"Participants: {participant_count}\n"
            f"Visibility: {'Private' if game.private_game else 'Public'}\n"
            f"Recurring template: {game.template_id if game.template_id is not None else 'No'}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Schedule",
        value=(
            f"Start date: {game.start_date}\n"
            f"Pick deadline: {game.pick_date if game.pick_date else 'Buy anytime'}\n"
            f"End date: {game.end_date if game.end_date else 'No end date'}\n"
            f"Created: {game.datetime_created.strftime('%Y-%m-%d %H:%M')}\n"
            f"Last updated: {game.last_updated.strftime('%Y-%m-%d %H:%M') if game.last_updated else 'Never'}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Rules",
        value=(
            f"Starting cash: ${float(game.start_money):,.2f}\n"
            f"Picks per player: {game.pick_count}\n"
            f"Exclusive picks: {'Yes' if game.draft_mode else 'No'}\n"
            f"Selling enabled: {'Yes' if game.allow_selling else 'No'}\n"
            f"Price updates: {game.update_frequency}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Performance",
        value=(
            f"Combined value: ${aggregate:,.2f}\n"
            f"Change: ${dollars:+,.2f}\n"
            f"Change: {percent:+.2f}%"
        ),
        inline=True,
    )
    return embed


def _user_can_view_leaderboard(game, user_id: int) -> bool:
    """Public games are visible to all; private games require ownership/participation."""
    if not game.private_game or game.owner_id == user_id:
        return True
    try:
        participants = fe.be.get_many_participants(game_id=game.id, user_id=user_id)
    except LookupError:
        return False
    return any(player.status in ("active", "pending") for player in participants)


def _user_can_view_game_info(game, user_id: int) -> bool:
    """Public games are visible; private games require ownership or active participation."""
    if not game.private_game:
        return True
    if game.owner_id == user_id:
        return True
    try:
        participants = fe.be.get_many_participants(game_id=game.id, user_id=user_id)
    except LookupError:
        return False
    return any(player.status == "active" for player in participants)


def _leaderboard_browse_games(user_id: int) -> list[tuple[Any, int]]:
    """User's games followed by public recurring games they are not in."""
    try:
        mine = fe.list_my_games_ranked(user_id)
    except LookupError:
        mine = []
    try:
        public_ranked = fe.list_games_ranked(
            include_public=True,
            include_private=False,
            include_open=True,
            include_active=True,
            include_ended=True,
        )
    except LookupError:
        public_ranked = []
    mine_ids = {str(game.id) for game, _count in mine}
    other_recurring = [
        item
        for item in public_ranked
        if item[0].template_id is not None
        and str(item[0].id) not in mine_ids
    ]
    return mine + other_recurring


@bot.tree.command(name="leaderboard", description="View leaderboards for your games or another public game")
@app_commands.autocomplete(game_id=ac.leaderboard_games_autocomplete)
@app_commands.describe(game_id="Optional game name or ID; leave blank to browse your games")
async def leaderboard_cmd(
    interaction: discord.Interaction,
    game_id: str | None = None,
):
    await interaction.response.defer(ephemeral=ephemeral_test)
    user_id = interaction.user.id
    if game_id:
        try:
            selected_game = await asyncio.to_thread(fe.be.get_game, game_id)
        except LookupError:
            await interaction.followup.send(
                embed=simple_embed(
                    status="failed",
                    title="Game not found",
                    desc=f"No game with ID `{game_id}` exists.",
                ),
                ephemeral=ephemeral_test,
            )
            return
        if not await asyncio.to_thread(_user_can_view_leaderboard, selected_game, user_id):
            await interaction.followup.send(
                embed=simple_embed(
                    status="failed",
                    title="Private game",
                    desc="You do not have access to this private game's leaderboard.",
                ),
                ephemeral=ephemeral_test,
            )
            return
        ranked = [(selected_game, 0)]
    else:
        try:
            ranked = await asyncio.to_thread(_leaderboard_browse_games, user_id)
        except Exception as exc:
            logger.exception("leaderboard failed | user=%s", user_id, exc_info=exc)
            await interaction.followup.send(
                embed=simple_embed(status="failed", title="Error", desc="Could not load your games."),
                ephemeral=ephemeral_test,
            )
            return
        if not ranked:
            await interaction.followup.send(
                embed=simple_embed(
                    status="failed",
                    title="No games",
                    desc=(
                        "There are no personal or recurring public leaderboards to browse. "
                        "You can still enter another public game with `/leaderboard game_id:`."
                    ),
                ),
                ephemeral=ephemeral_test,
            )
            return

    games: list[dict] = []
    for game, _player_count in ranked:
        try:
            info = await asyncio.to_thread(fe.game_info, game.id, True)
        except Exception:
            continue
        leaderboard = info.leaderboard or []
        rank_desc = (
            "You're not participating in this game."
            if game_id
            else "You're not on the board yet."
        )
        for i, entry in enumerate(leaderboard, start=1):
            if entry.user_id == user_id:
                d_chg = float(entry.change_dollars or 0)
                p_chg = float(entry.change_percent or 0)
                rank_desc = f"Your rank: **#{i}** · ${d_chg:+,.2f} ({p_chg:+.2f}%)"
                break
        games.append(
            _leaderboard_game_data(
                game,
                leaderboard,
                title=f"{game.name} [{game.id}]",
                description=rank_desc,
            )
        )

    if not games:
        await interaction.followup.send(
            embed=simple_embed(status="failed", title="No leaderboards", desc="No games with leaderboard data."),
            ephemeral=ephemeral_test,
        )
        return

    view = UserLeaderboardView(
        interaction,
        games,
        show_game_controls=game_id is None,
    )
    await view.prepare()
    embed, file = view._page_payload()
    await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=ephemeral_test)


def _participant_for_game(user_id: int, game_id: str):
    """Return the participant row or None when the user is not in the game."""
    try:
        return fe.be.get_many_participants(user_id=user_id, game_id=game_id)[0]
    except LookupError:
        return None


def _build_my_stocks_portfolio(user_id: int, game_id: str, display_name: str):
    """Load portfolio data and render the PNG (runs in a worker thread)."""
    picks = fe.my_stocks(user_id, game_id)
    info = fe.game_info(game_id)
    user_data = {
        'display_name': display_name,
        'user_id': user_id,
    }
    game_data = {
        'name': fe._get_game_name(game_id=game_id),
        'id': game_id,
    }
    stock_picks = [
        {
            'stock_ticker': pick.stock_ticker,
            'status': pick.status,
            'shares': pick.shares,
            'current_value': pick.current_value,
            'change_dollars': pick.change_dollars,
            'change_percent': pick.change_percent,
            'last_updated': pick.last_updated,
        }
        for pick in picks
    ]
    image_buffer = get_portfolio_generator().create_portfolio_image(
        user_data, game_data, stock_picks, info
    )
    remaining, total = fe.pick_capacity(user_id, game_id)
    return info, image_buffer, remaining, total


@bot.tree.command(name="my-stocks", description="View your stocks in a game as a visual portfolio")
@app_commands.autocomplete(game_id=ac.all_games_autocomplete)
@app_commands.describe(
    game_id="ID of the game"
)
async def my_stocks(
    interaction: discord.Interaction,
    game_id: str
):
    user_id = interaction.user.id
    await interaction.response.defer(ephemeral=ephemeral_test)

    try:
        participant = await asyncio.to_thread(_participant_for_game, user_id, game_id)
        if participant is None:
            await interaction.followup.send(
                embed=simple_embed(
                    status='failed',
                    title='Not in Game',
                    desc=(
                        'You are not currently participating in this game. '
                        'You can try to join it using the join-game command.'
                    ),
                ),
                ephemeral=ephemeral_test,
            )
            return

        if participant.status == 'pending':
            await interaction.followup.send(
                embed=simple_embed(
                    status='failed',
                    title='Not in Game',
                    desc=(
                        'You are not in this game yet. Your request to join this private '
                        'game is still awaiting owner approval.'
                    ),
                ),
                ephemeral=ephemeral_test,
            )
            return

        if participant.status != 'active':
            await interaction.followup.send(
                embed=simple_embed(
                    status='failed',
                    title='Not in Game',
                    desc='You are not currently participating in this game.',
                ),
                ephemeral=ephemeral_test,
            )
            return

        info, image_buffer, remaining, total = await asyncio.to_thread(
            _build_my_stocks_portfolio,
            user_id,
            game_id,
            interaction.user.display_name,
        )

        # Create Discord file
        file = discord.File(image_buffer, filename=f"portfolio_{user_id}_{game_id}.png")

        game = info.game
        rank_line = "Not ranked yet"
        if info.leaderboard:
            for i, entry in enumerate(info.leaderboard, start=1):
                if entry.user_id == user_id:
                    d_chg = float(entry.change_dollars or 0)
                    p_chg = float(entry.change_percent or 0)
                    rank_line = f"**#{i}** · ${d_chg:+,.2f} ({p_chg:+.2f}%)"
                    break

        status_label = game.status
        pick_line = (
            f"Pick deadline: `{game.pick_date}`"
            if game.pick_date
            else "Buy anytime"
        )
        notice = _prestart_notice(game)
        body = (
            f"Game `{game.id}` · **{status_label}**\n"
            f"{pick_line}\n"
            f"Your rank: {rank_line}\n"
            f"Picks remaining: **{remaining}** / {game.pick_count} "
            f"(${float(game.start_money) / int(game.pick_count):,.2f} per pick)"
        )
        embed = discord.Embed(
            title=f"{game.name}",
            description=f"{notice}\n\n{body}" if notice else body,
            color=discord.Color.blurple(),
        )
        embed.set_image(url=f"attachment://portfolio_{user_id}_{game_id}.png")
        embed.set_footer(text="More detail: /game-info")

        await interaction.followup.send(
            embed=embed,
            file=file,
            ephemeral=ephemeral_test,
        )
        
    except DoesntExistError:
        embed = simple_embed(
            status='failed', 
            title='Not in Game',
            desc='You are not currently participating in this game. You can try to join it using the join-game command.'
        )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)
        
    except LookupError:
        try:
            remaining, total = await asyncio.to_thread(fe.pick_capacity, user_id, game_id)
            game = (await asyncio.to_thread(fe.game_info, game_id, False)).game
            notice = _prestart_notice(game)
            body = (
                f'You have not bought any stocks in game #{game_id}. '
                f'Use `/buy-stock` to make your first pick.\n'
                f'**Picks remaining:** {remaining} of {total}\n'
                f'**Allocated per pick:** ${float(game.start_money) / total:,.2f}'
            )
            embed = discord.Embed(
                title='No Stocks Yet',
                description=f"{notice}\n\n{body}" if notice else body,
                color=discord.Color.blue(),
            )
        except (DoesntExistError, LookupError):
            embed = simple_embed(status='failed', title='Not in Game', desc=f'You are not participating in game #{game_id}.')
        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)
        
    except Exception as e:
        logger.exception(f'User: {interaction.user.id} tried to generate portfolio image for game: {game_id}. Error: {e}')
        embed = simple_embed(
            status='failed',
            title='Error Generating Portfolio',
            desc='An unexpected error occurred while generating your portfolio image'
        )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)


# GAME INFO RELATED-

@bot.tree.command(name="game-info", description="View information about a game")
@app_commands.autocomplete(game_id=ac.game_info_autocomplete)
@app_commands.describe(
    game_id="ID of the game to view",
)
async def game_info(
    interaction: discord.Interaction,
    game_id: str,
):
    await interaction.response.defer(ephemeral=ephemeral_test)

    try:
        game_info_obj = await asyncio.to_thread(fe.game_info, game_id, True)
        game = game_info_obj.game
        if not await asyncio.to_thread(_user_can_view_game_info, game, interaction.user.id):
            await interaction.followup.send(
                embed=simple_embed(
                    status='failed',
                    title='Private Game',
                    desc='You do not have access to view this private game.',
                ),
                ephemeral=ephemeral_test,
            )
            return
        leaderboard = game_info_obj.leaderboard or []
        view = UserLeaderboardView(
            interaction,
            [
                _leaderboard_game_data(
                    game,
                    leaderboard,
                    embed=_game_info_embed(game, len(leaderboard)),
                )
            ],
            show_game_controls=False,
        )
        await view.prepare()
        embed, file = view._page_payload()
        await interaction.followup.send(
            embed=embed,
            file=file,
            view=view,
            ephemeral=ephemeral_test,
        )
    except Exception as exc:
        logger.exception("Failed to get game info for %s", game_id, exc_info=exc)
        embed = discord.Embed(
            title="Failed to get info",
            description=f"Game with ID {game_id} does not exist or an error occurred.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)

# TODO add buttons for joining games?
# TODO add a joinable parameter?
def _format_listed_game(
    game,
    player_count: int,
    *,
    status_emoji: str | None = None,
) -> tuple[str, str]:
    """Shared title/body formatting for /game-list and /my-games."""
    pick_line = (
        f'> **Pick date:** {game.pick_date}'
        if game.pick_date
        else '> **Pick date:** buy anytime'
    )
    end_line = f'\n> **End date:** `{game.end_date}`' if game.end_date else ''
    recurring_tag = ' 🔁' if game.template_id is not None else ''
    private_tag = ' 🔒' if game.private_game else ''
    emoji_prefix = f'{status_emoji} ' if status_emoji else ''
    title = f"{emoji_prefix}{game.name[:name_cutoff]}{recurring_tag}{private_tag}: [{game.id}]"
    body = (
        f'> **Owner:** <@{game.owner_id}>\n'
        f'> **Players:** {player_count}\n'
        f'> **Picks:** {game.pick_count}\n'
        f'> **Start date:** `{game.start_date}`\n'
        f'{pick_line}'
        f'{end_line}'
    )
    return title, body


@bot.tree.command(name="game-list", description="View a list of all games") # TODO rename to list-games, all-games, or games-list?
@app_commands.describe(
    # page_length="The length of the list per page. Defaults to 6",  # debug only
    owner="Only show games created by this user (use the bot to list recurring games)",
)
async def game_list(
    interaction: discord.Interaction,
    # page_length: app_commands.Range[int, 1, 25] = 6,  # commented out; keep default below
    owner: discord.User | None = None,
):
    page_length = 6  # default page size (page_length option disabled for production)
    embed = discord.Embed()
    error = False
    try:
        ranked = await asyncio.to_thread(
            fe.list_games_ranked,
            include_open=True,
            include_active=True,
            owner_id=owner.id if owner else None,
        )

        title = "Currently running games"
        if owner is not None:
            title = f"Games owned by {owner.display_name}"
        embed = discord.Embed(title=title, description="")
        formatted_games = [
            _format_listed_game(game, player_count)
            for game, player_count in ranked
        ]
        await Pagination(interaction, page_len=page_length, embed=embed, games=formatted_games, ephemeral=ephemeral_test).navigate()

    except LookupError as e:
        error = True
        embed.title = 'No games found'
        if owner is not None:
            embed.description = f'There are no public open or active games owned by {owner.mention}'
        else:
            embed.description = 'There are no public open or active games'
        embed.color = discord.Color.red()
        
    except Exception as e:
        error = True
        logger.exception(f'Error when loading game list. Page length: {page_length}', exc_info=e)
        embed.title = 'Error'
        embed.description = f'An unexpected error ocurred while trying to load games\nReport this!'
    
    if error:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral_test)

@bot.tree.command(name="my-games", description="View your games (same layout as game-list)")
async def my_games(
    interaction: discord.Interaction
):
    page_length = 6
    embed = discord.Embed()
    error = False
    try:
        today = await asyncio.to_thread(fe.gl._today_et)
        ranked = await asyncio.to_thread(
            fe.list_my_games_ranked,
            interaction.user.id,
            include_ended=True,
            today=today,
        )
        embed = discord.Embed(title="Your games", description="")
        formatted_games = [
            _format_listed_game(
                game,
                player_count,
                status_emoji=fe.game_status_emoji(game, today),
            )
            for game, player_count in ranked
        ]
        await Pagination(
            interaction,
            page_len=page_length,
            embed=embed,
            games=formatted_games,
            ephemeral=ephemeral_test,
        ).navigate()

    except LookupError:
        error = True
        embed.title = 'No games found'
        embed.description = 'You are not currently in any games.'
        embed.color = discord.Color.orange()
    except Exception as e:
        error = True
        logger.exception(f'User: {interaction.user.id} tried to get their games. Error: {e}')
        embed.title = 'Error'
        embed.description = 'Unable to retrieve your games. Please try again.'
        embed.color = discord.Color.red()

    if error:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral_test)

@bot.tree.command(name="user-stats", description="Shows global statistics of a user. Shows yours by default.")
@app_commands.describe(
    user="The ID of the user you want to see stats for"
)
async def user_stats(
    interaction: discord.Interaction,
    user: discord.User | None
):
    await interaction.response.defer(ephemeral=ephemeral_test) # Defer the response to allow time for the update
    try:
        discord_user: discord.User | discord.Member = user if user else interaction.user
        user_title = f"{discord_user.display_name}{f' ({discord_user.name})' if discord_user.display_name != discord_user.name else ''}"
        
        user_stats = await asyncio.to_thread(fe.get_user, discord_user.id)

        embed = discord.Embed(title=user_title, description="Global Statistics")
        embed.set_thumbnail(url=discord_user.display_avatar)
        embed.add_field(name="Total wins:", value=user_stats.overall_wins)
        embed.add_field(name="Change Dollars/Change %", value=f"{user_stats.change_dollars}/{user_stats.change_percent}")
        embed.color = discord.Color.blue()
        embed.set_footer(text="Only completed (ended) games are included in these stats.")

        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)
    except LookupError:
        embed = discord.Embed(title="User not found", description="User does not exist in our system!")
        embed.color = discord.Color.red()
        await interaction.followup.send(embed=embed, ephemeral=ephemeral_test)

# ABOUT, LOGS AND HELP COMMANDS

@bot.tree.command(name="about", description="About the bot and its creators")
async def about(
    interaction: discord.Interaction,
):
    creators = "<@163784331804934144>: Project Leader, Coordinated Strategic Management Lead, Frontend Dev, Backend Dev, gave the idea for the about command" \
    "\n<@329374393715392520>: Frontend Dev, Bot Dev, made really big bot commits" \
    "\n<@1240817181692792934>: Bot Dev, made the about command, strategy consultant"

    embed = discord.Embed(title="About the bot", description="[StockBot](https://github.com/ItsJustAGitHubMichealWhosGonnaSeeIt5Ppl/StockGame) is a discord bot that simulates the purchase of stocks and runs them in a gamified format. Originally built for the Lemonade Stand community.")
    embed.add_field(name="Creators", value=creators)
    embed.add_field(name="Special Thanks", value="<@394012218729168907>: Gave the idea\n<@204414583203430400>: Chaotic Project Tester")
    await interaction.response.send_message(embed=embed, ephemeral=ephemeral_test)

@bot.tree.command(name="logs", description="Download bot logs")
@app_commands.default_permissions()
async def logs(
  interaction: discord.Interaction,
  kind: Literal['debug', 'error'] = 'debug',
):
    if not is_moderator(interaction):
        await interaction.response.send_message(
            embed=simple_embed(
                status='failed',
                title='Not Allowed',
                desc='Only moderators in the home server can download bot logs.',
            ),
            ephemeral=True,
        )
        return
    title = "Logs"
    status = 'success'
    path = latest_log_path(kind)
    if path is None or not os.path.isfile(path):
        await interaction.response.send_message(
            embed=simple_embed(status='failed', title='No Logs', desc=f'No {kind} log file found yet.'),
            ephemeral=True,
        )
        return
    # Discord attachment limit ~25MB; truncate from end if needed for safety
    max_bytes = 8 * 1024 * 1024
    size = os.path.getsize(path)
    if size > max_bytes:
        with open(path, 'rb') as f:
            f.seek(size - max_bytes)
            data = f.read()
        logfile = discord.File(fp=io.BytesIO(data), filename=f'log-{kind}-latest.log')
    else:
        logfile = discord.File(fp=path, filename=f'log-{kind}-latest.log')
    await interaction.response.send_message(
        embed=simple_embed(status=status, title=title, desc=f'Sending latest {kind} log.'),
        file=logfile,
        ephemeral=ephemeral_test,
    )

def _quick_start_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Stock Game Bot — Quick Start",
        description=(
            "New here? You only need four commands to start playing. "
            "Use **Advanced** below whenever you want the full command guide."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="1. Find a game",
        value="Run `/game-list` to browse available games and see when they start.",
        inline=False,
    )
    embed.add_field(
        name="2. Join it",
        value="Run `/join-game` and choose a game. Private games may require approval.",
        inline=False,
    )
    embed.add_field(
        name="3. Make your picks",
        value=(
            "Run `/buy-stock` for each company you want. Purchases become active "
            "according to that game's schedule and rules."
        ),
        inline=False,
    )
    embed.add_field(
        name="4. Follow the competition",
        value=(
            "Use `/my-stocks` for your portfolio, `/leaderboard` for rankings, "
            "and `/game-info` for the game's rules and dates."
        ),
        inline=False,
    )
    embed.set_footer(text="Tip: Discord will autocomplete command options as you type.")
    return embed


def _regular_help_embed(
    *,
    owns_game: bool = False,
    owns_private_game: bool = False,
    moderator: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title="Stock Game Bot — Command Guide",
        description="Commands are grouped by what you want to do.",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Find and join games",
        value=(
            "`/game-list` — Browse public games and recurring competitions.\n"
            "`/game-info` — View a game's rules, dates, settings, and leaderboard.\n"
            "`/join-game` — Join a public game or request access to a private one.\n"
            "`/my-games` — List every game you currently or previously played.\n"
            "`/leave-game` — Leave one of your games."
        ),
        inline=False,
    )
    embed.add_field(
        name="Play and track results",
        value=(
            "`/buy-stock` — Add a stock pick to one of your games.\n"
            "`/remove-stock` — Cancel a purchase that is still pending.\n"
            "`/my-stocks` — View your portfolio, performance, and current rank.\n"
            "`/leaderboard` — Browse rankings for your games or accessible games.\n"
            "`/user-stats` — View your or another player's overall statistics."
        ),
        inline=False,
    )
    embed.add_field(
        name="Create games",
        value=(
            "`/create-game` — Build a game with a guided setup.\n"
            "`/create-game-advanced` — Create a game by entering every setting directly."
        ),
        inline=False,
    )
    if owns_game:
        embed.add_field(
            name="Game owner commands",
            value=(
                "`/invite` — Invite someone to your game through Discord.\n"
                "`/manage-game` — Change settings on an existing game you own.\n"
                "`/delete-game` — Permanently delete a game you own."
            ),
            inline=False,
        )
    if owns_private_game:
        embed.add_field(
            name="Private game commands",
            value=(
                "`/manage-pending` — Approve or deny requests to join a private game.\n"
                "`/kick-player` — Remove a player from your private game."
            ),
            inline=False,
        )
    if moderator:
        embed.add_field(
            name="Moderator tools",
            value=(
                "`/create-recurring-game` — Schedule a repeating competition and optional leaderboard push.\n"
                "`/manage-recurring-games` — Pause, resume, delete, or configure recurring games.\n"
                "`/update` — Force an immediate price, portfolio, and leaderboard update.\n"
                "`/logs` — Download bot logs for troubleshooting."
            ),
            inline=False,
        )
    embed.add_field(
        name="More information",
        value=(
            "`/about` — Learn about StockBot and its creators.\n"
            "`/help` — Open this guide again."
        ),
        inline=False,
    )
    if moderator:
        embed.set_footer(
            text="Moderator commands default to admins; grant roles under Server Settings → Integrations."
        )
    else:
        embed.set_footer(text="Need more help? Ask a server administrator.")
    return embed


def _should_show_quick_start(user_id: int) -> bool:
    """Show onboarding when the player has no current games or no ended history."""
    try:
        participations = fe.be.get_many_participants(user_id=user_id)
    except LookupError:
        return True

    has_current_game = False
    has_ended_game = False
    for participation in participations:
        if participation.status not in ("active", "pending"):
            continue
        try:
            game = fe.be.get_game(participation.game_id)
        except LookupError:
            continue
        if game.status == "ended":
            has_ended_game = True
        else:
            has_current_game = True
        if has_current_game and has_ended_game:
            return False
    return not has_current_game or not has_ended_game


class QuickStartHelpView(InitiatorOnlyView):
    def __init__(
        self,
        initiator_id: int,
        *,
        owns_game: bool,
        owns_private_game: bool,
        moderator: bool,
    ):
        super().__init__(initiator_id, timeout=300)
        self.owns_game = owns_game
        self.owns_private_game = owns_private_game
        self.moderator = moderator

    @discord.ui.button(label="Advanced", style=discord.ButtonStyle.secondary)
    async def advanced(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await interaction.response.edit_message(
            embed=_regular_help_embed(
                owns_game=self.owns_game,
                owns_private_game=self.owns_private_game,
                moderator=self.moderator,
            ),
            view=None,
        )

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except discord.HTTPException:
            logger.debug("Could not remove the expired help controls.", exc_info=True)


@bot.tree.command(name="help", description="Get help with StockBot")
async def help(interaction: discord.Interaction):
    moderator = is_moderator(interaction)
    owns_game, owns_private_game = await asyncio.to_thread(
        fe.user_owns_any_game, interaction.user.id
    )
    show_quick_start = await asyncio.to_thread(
        _should_show_quick_start, interaction.user.id
    )
    if show_quick_start:
        view = QuickStartHelpView(
            interaction.user.id,
            owns_game=owns_game,
            owns_private_game=owns_private_game,
            moderator=moderator,
        )
        await interaction.response.send_message(
            embed=_quick_start_help_embed(),
            view=view,
            ephemeral=ephemeral_test,
        )
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            logger.debug("Could not retain the quick-start help message.", exc_info=True)
        return

    await interaction.response.send_message(
        embed=_regular_help_embed(
            owns_game=owns_game,
            owns_private_game=owns_private_game,
            moderator=moderator,
        ),
        ephemeral=ephemeral_test,
    )

# Run the bot using the token
if __name__ == '__main__':
    if TOKEN:
        try:
            attach_critical_dm_bot(bot)
            bot.run(TOKEN, log_handler=None)
        except discord.errors.LoginFailure:
            logger.critical(
                "Discord login failed: invalid DISCORD_TOKEN. Check .env / secrets.",
                exc_info=True,
            )
        except discord.errors.PrivilegedIntentsRequired:
            logger.critical(
                "Discord privileged intents required. Enable Message Content / Members "
                "in the Discord Developer Portal.",
                exc_info=True,
            )
        except Exception as e:
            logger.critical("Bot crashed while starting/running: %s", e, exc_info=True)
    else:
        logger.critical("DISCORD_TOKEN environment variable not found. Bot cannot start.")
