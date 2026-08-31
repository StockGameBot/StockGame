# Views for the Discord Bot
# https://stackoverflow.com/a/76250596

import discord
import datetime
from collections.abc import Sequence
from typing import Callable, Optional, List, Dict, Any, Union, TYPE_CHECKING, cast
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from helpers.affiliations import format_dollar_gain

if TYPE_CHECKING:
    from helpers.datatype_validation import GameInfo

class Pagination(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, page_len:int, embed: discord.Embed, games: Sequence[tuple[str, str] | str], mode: str = 'field', ephemeral: bool = True):
        # Mode field or codeblock
        self.interaction = interaction
        self.games = list(games) # Formatted pages
        self.embed = embed
        self.page_len = page_len if page_len <= 25 else 25 # Maximum page length must be 25
        self.total_pages =  self.compute_total_pages(total_results=len(self.games), results_per_page=self.page_len)
        self.index = 0 # THIS IS STARTING AT 0 ADD 1 TO SHOW VISUAL
        self.ephemeral = ephemeral
        self.mode = mode
        super().__init__(timeout=100)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user == self.interaction.user:
            return True
        else:
            emb = discord.Embed(
                description=f"Only the author of the command can perform this action.",
                color=16711680
            )
            await interaction.response.send_message(embed=emb, ephemeral=self.ephemeral)
            return False
        
    def get_page(self): # Return an embed object of current page
        self.embed.set_footer(text=f"Page {self.index + 1} of {self.total_pages} | Dates are formatted as YYYY-MM-DD") # Set a footer
        emb = self.embed.copy()
        if self.mode == 'field':
            for game in self.games[self.page_len * self.index: self.page_len * (self.index +1)]: # Get only the subset of games we're after
                if not isinstance(game, tuple):
                    raise TypeError('Field pagination requires (name, value) tuples.')
                emb.add_field(name=game[0],value=game[1]) # Fill out the embed!
        else: # Codeblock mode
            codeblock_lines = [str(line) for line in self.games[self.page_len * self.index: self.page_len * (self.index +1)]]
            emb.add_field(name='', value='```{lines}```'.format(lines='\n'.join(codeblock_lines)))
                
        return emb
    
    async def navigate(self):
        emb = self.get_page() 
        if self.total_pages == 1:
            await self.interaction.response.send_message(embed=emb, ephemeral=self.ephemeral)
        elif self.total_pages > 1:
            self.update_buttons()
            await self.interaction.response.send_message(embed=emb, view=self, ephemeral=self.ephemeral)

    async def edit_page(self, interaction: discord.Interaction):
        emb = self.get_page()
        self.update_buttons()
        await interaction.response.edit_message(embed=emb, view=self)

    def update_buttons(self):
        previous = cast(discord.ui.Button, self.children[0])
        next_button = cast(discord.ui.Button, self.children[1])
        end_button = cast(discord.ui.Button, self.children[2])
        if self.index > self.total_pages // 2:
            end_button.emoji = "⏮️"
        else:
            end_button.emoji = "⏭️"
            
        previous.disabled = self.index == 0
        next_button.disabled = self.index +1 == self.total_pages

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.blurple)
    async def previous(self, interaction: discord.Interaction, button):
        self.index -= 1
        await self.edit_page(interaction)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.blurple)
    async def next(self, interaction: discord.Interaction, button):
        self.index += 1
        await self.edit_page(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.blurple)
    async def end(self, interaction: discord.Interaction, button):
        if self.index <= self.total_pages//2:
            self.index = self.total_pages -1
        else:
            self.index = 0
        await self.edit_page(interaction)

    async def on_timeout(self):
        # remove buttons on timeout
        message = await self.interaction.original_response()
        await message.edit(view=None)

    @staticmethod
    def compute_total_pages(total_results: int, results_per_page: int) -> int:
        # Divide total results (-1) by results per page,  +1
        return ((total_results - 1) // results_per_page) + 1


class LeaderboardImageGenerator:
    """
    A class to generate leaderboard images for Discord bot commands.
    Handles image creation, styling, and returns BytesIO buffers for Discord file uploads.
    """
    
    def __init__(self, 
                 width: int = 750,  # Increased width to accommodate new columns
                 base_height: int = 180,
                 row_height: int = 50,
                 theme: str = 'discord_dark'):
        """
        Initialize the LeaderboardImageGenerator.
        
        Args:
            width (int): Image width in pixels
            base_height (int): Base height before adding rows
            row_height (int): Height per leaderboard row
            theme (str): Color theme ('discord_dark', 'light', etc.)
        """
        self.width = width
        self.base_height = base_height
        self.row_height = row_height
        self.theme = theme
        
        # Set color scheme based on theme
        self._set_color_scheme()
        
        # Font sizes
        self.font_sizes = {
            'title': 24,
            'header': 16,
            'text': 14,
            'small': 12
        }
        
        # Load fonts with fallback
        self._load_fonts()
    
    def _set_color_scheme(self):
        """Set colors based on the selected theme."""
        if self.theme == 'discord_dark':
            self.colors = {
                'bg': (47, 49, 54),
                'header': (114, 137, 218),
                'row_bg_1': (54, 57, 63),
                'row_bg_2': (47, 49, 54),
                'text': (255, 255, 255),
                'gold': (255, 215, 0),
                'silver': (192, 192, 192),
                'bronze': (205, 127, 50),
                'footer': (150, 150, 150),
                'positive': (76, 175, 80),  # Green for positive gains
                'negative': (244, 67, 54)   # Red for negative gains
            }
        elif self.theme == 'light':
            self.colors = {
                'bg': (255, 255, 255),
                'header': (66, 139, 202),
                'row_bg_1': (249, 249, 249),
                'row_bg_2': (255, 255, 255),
                'text': (51, 51, 51),
                'gold': (255, 215, 0),
                'silver': (192, 192, 192),
                'bronze': (205, 127, 50),
                'footer': (128, 128, 128),
                'positive': (76, 175, 80),  # Green for positive gains
                'negative': (244, 67, 54)   # Red for negative gains
            }
        else:
            # Default to discord_dark
            self._set_color_scheme_default()
    
    def _set_color_scheme_default(self):
        """Set default color scheme."""
        self.theme = 'discord_dark'
        self._set_color_scheme()
    
    def _load_fonts(self):
        """Load fonts with fallback to default."""
        self.fonts = {}
        # Try multiple font paths for cross-platform compatibility
        # Windows paths
        font_paths = [
            'arial.ttf',
            'Arial.ttf',
            'arial.ttc',
            # Linux paths
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/ttf-dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/TTF/DejaVuSans.ttf',
            # macOS paths (if needed)
            '/System/Library/Fonts/Helvetica.ttc',
            '/Library/Fonts/Arial.ttf',
        ]
        
        for size_name, size in self.font_sizes.items():
            font_loaded = False
            for font_path in font_paths:
                try:
                    self.fonts[size_name] = ImageFont.truetype(font_path, size)
                    font_loaded = True
                    break
                except (OSError, IOError):
                    continue
            
            if not font_loaded:
                # Fallback to default font
                self.fonts[size_name] = ImageFont.load_default()
    
    def create_leaderboard_image(self, 
                               game_data: Dict[str, Any], 
                               leaderboard_data: List[Dict[str, Any]],
                               show_footer: bool = True,
                               custom_title: Optional[str] = None) -> BytesIO:
        """
        Create a leaderboard image from game and leaderboard data.
        
        Args:
            game_data (Dict): Game information containing name, id, owner, etc.
            leaderboard_data (List[Dict]): List of player data dictionaries
            show_footer (bool): Whether to show the bot footer
            custom_title (str, optional): Override the default title
            
        Returns:
            BytesIO: Buffer containing the PNG image
        """
        # Calculate image height
        height = self.base_height + (len(leaderboard_data) * self.row_height)
        
        # Create image
        img = Image.new('RGB', (self.width, height), self.colors['bg'])
        draw = ImageDraw.Draw(img)
        
        y_offset = 20
        
        # Draw title
        title_text = custom_title or f"{game_data.get('name', 'Game')} (ID: {game_data.get('id', 'N/A')})"
        y_offset = self._draw_centered_text(draw, title_text, y_offset, self.fonts['title'], self.colors['text'])
        y_offset += 20
        
        # Draw leaderboard
        y_offset = self._draw_leaderboard_header(draw, y_offset)
        y_offset = self._draw_leaderboard_rows(draw, leaderboard_data, y_offset)
        
        # Add footer if requested
        if show_footer:
            self._draw_footer(draw, height)
        
        # Save to BytesIO buffer
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        return buffer
    
    def _draw_centered_text(self, draw: ImageDraw.ImageDraw, text: str, y: int, font, color) -> int:
        """Draw centered text and return new y position."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (self.width - text_width) // 2
        draw.text((x, y), text, fill=color, font=font)
        return int(y + text_height + 10)
    
    def _draw_leaderboard_header(self, draw: ImageDraw.ImageDraw, y_offset: int) -> int:
        """Draw leaderboard header."""
        header_rect = [0, y_offset, self.width, y_offset + 40]
        draw.rectangle(header_rect, fill=self.colors['header'])
        
        # Header text - updated with new columns
        headers = [
            (20, "Rank"),
            (100, "Investor"),
            (300, "Portfolio"),
            (450, "$ Gain"),
            (550, "% Gain"),
            (650, "Joined")
        ]
        
        for x, header_text in headers:
            draw.text((x, y_offset + 10), header_text, fill=self.colors['text'], font=self.fonts['header'])
        
        return y_offset + 40

    def _truncate_to_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font,
        max_width: int,
    ) -> str:
        if not text:
            return ""
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text
        ellipsis = "..."
        low = 0
        high = len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if draw.textbbox((0, 0), text[:mid] + ellipsis, font=font)[2] <= max_width:
                low = mid
            else:
                high = mid - 1
        return text[:low] + ellipsis if low else ellipsis
    
    def _draw_leaderboard_rows(self, draw: ImageDraw.ImageDraw, leaderboard_data: List[Dict], y_offset: int) -> int:
        """Draw leaderboard rows."""
        for idx, player_data in enumerate(leaderboard_data):
            # Alternating row colors
            row_color = self.colors['row_bg_1'] if idx % 2 == 0 else self.colors['row_bg_2']
            row_rect = [0, y_offset, self.width, y_offset + self.row_height]
            draw.rectangle(row_rect, fill=row_color)
            
            # Rank indicator with special colors for top 3
            rank = int(player_data.get('rank', idx + 1))
            rank_text = f"{rank}."
            rank_color = self._get_rank_color(rank - 1)
            draw.text((20, y_offset + 15), rank_text, fill=rank_color, font=self.fonts['text'])
            
            # Player name (x=100 .. Portfolio column at x=300)
            player_name = player_data.get('display_name', f"ID({player_data.get('user_id', 'Unknown')})")
            player_name = self._truncate_to_width(
                draw, str(player_name), self.fonts['text'], max_width=190,
            )
            draw.text((100, y_offset + 15), player_name, fill=self.colors['text'], font=self.fonts['text'])
            
            # Portfolio value
            current_value = player_data.get('current_value', 0)
            portfolio_value = f"${float(current_value):,.2f}"
            draw.text((300, y_offset + 15), portfolio_value, fill=self.colors['text'], font=self.fonts['text'])
            
            # Dollar gain/loss with color coding
            change_dollars = player_data.get('change_dollars', 0)
            dollar_change = format_dollar_gain(float(change_dollars))
            dollar_color = self.colors['positive'] if change_dollars >= 0 else self.colors['negative']
            draw.text((450, y_offset + 15), dollar_change, fill=dollar_color, font=self.fonts['text'])
            
            # Percentage gain/loss with color coding
            change_percent = player_data.get('change_percent', 0)
            percent_change = f"{float(change_percent):+.2f}%"  # + sign for positive, - for negative
            percent_color = self.colors['positive'] if change_percent >= 0 else self.colors['negative']
            draw.text((550, y_offset + 15), percent_change, fill=percent_color, font=self.fonts['text'])
            
            # Join date
            joined = player_data.get('joined', '')
            if isinstance(joined, str):
                join_date = joined[:10] if len(joined) >= 10 else joined
            else:
                # Handle datetime objects
                join_date = joined.strftime("%Y-%m-%d") if joined else "Unknown"
            draw.text((650, y_offset + 15), join_date, fill=self.colors['text'], font=self.fonts['text'])
            
            y_offset += self.row_height
        
        return int(y_offset)  # Ensure return type is int
    
    def _get_rank_color(self, rank: int) -> tuple:
        """Get color for rank position."""
        if rank == 0:
            return self.colors['gold']
        elif rank == 1:
            return self.colors['silver']
        elif rank == 2:
            return self.colors['bronze']
        else:
            return self.colors['text']
    
    def _draw_footer(self, draw: ImageDraw.ImageDraw, height: int, last_updated: Optional[datetime.datetime] = None):
        """Draw footer text."""
        draw.text((20, height - 25), "Last Updated: " +
                  (last_updated.strftime("%Y-%m-%d %H:%M:%S") if last_updated else "Generated by StockBot"),
                 fill=self.colors['footer'], font=self.fonts['small'])

# Usage example functions (can be imported and used in your bot commands)
def create_game_leaderboard_image(game_data: Dict[str, Any], 
                                leaderboard_data: List[Dict[str, Any]],
                                theme: str = 'discord_dark') -> BytesIO:
    """
    Convenience function to create a game leaderboard image.
    
    Args:
        game_data: Game information dictionary
        leaderboard_data: List of player data dictionaries
        theme: Color theme to use
        
    Returns:
        BytesIO buffer containing the PNG image
    """
    generator = get_leaderboard_generator(theme) if theme == "discord_dark" else LeaderboardImageGenerator(theme=theme)
    return generator.create_leaderboard_image(game_data, leaderboard_data)


class StockPortfolioImageGenerator:
    """
    A class to generate stock portfolio images for Discord bot commands.
    Handles image creation, styling, and returns BytesIO buffers for Discord file uploads.
    """
    
    def __init__(self, 
                 width: int = 700,
                 base_height: int = 130,
                 row_height: int = 58,
                 theme: str = 'discord_dark'):
        """
        Initialize the StockPortfolioImageGenerator.
        
        Args:
            width (int): Image width in pixels
            base_height (int): Base height before adding rows
            row_height (int): Height per stock row
            theme (str): Color theme ('discord_dark', 'light', etc.)
        """
        self.width = width
        self.base_height = base_height
        self.row_height = row_height
        self.theme = theme
        self.summary_inset = 50
        self.content_inset = 20
        self.table_stat_pad = 12
        self._build_stat_columns()
        
        # Set color scheme based on theme
        self._set_color_scheme()
        
        # Font sizes
        self.font_sizes = {
            'title': 24,
            'header': 16,
            'text': 14,
            'small': 12
        }
        
        # Load fonts with fallback
        self._load_fonts()

    def _build_stat_columns(self) -> None:
        """Lay out five equal stat slots with symmetric inner table padding."""
        inset = self.content_inset
        stat_left = inset + self.table_stat_pad
        stat_right = self.width - inset - self.table_stat_pad
        slot_width = (stat_right - stat_left) // 5
        keys = ("price", "shares", "value", "gain_d", "gain_p")
        self.stat_slots = [
            (stat_left + (index * slot_width), stat_left + ((index + 1) * slot_width))
            for index in range(5)
        ]
        self.columns = {key: self.stat_slots[index][0] for index, key in enumerate(keys)}

    def _draw_stat_cell(
        self,
        draw: ImageDraw.ImageDraw,
        slot_index: int,
        top: int,
        bottom: int,
        text: str,
        font,
        fill,
    ) -> None:
        left, right = self.stat_slots[slot_index]
        self._draw_centered_text_in_rect(
            draw,
            [left, top, right, bottom],
            text,
            font,
            fill,
        )
    
    def _set_color_scheme(self):
        """Set colors based on the selected theme."""
        if self.theme == 'discord_dark':
            self.colors = {
                'bg': (47, 49, 54),
                'header': (114, 137, 218),
                'row_bg_1': (54, 57, 63),
                'row_bg_2': (47, 49, 54),
                'text': (255, 255, 255),
                'footer': (150, 150, 150),
                'positive': (97, 220, 130),   # bright mint — readable on dark rows
                'negative': (255, 118, 108),  # bright coral — readable on dark rows
                'neutral': (255, 255, 255),  # White for neutral/no data
                'ticker_bg': (32, 34, 37),  # Darker background for ticker
                'ticker_box': (58, 66, 95),   # muted indigo-slate (pairs with header blue)
                'company_box': (79, 140, 165),  # steel teal (complements blurple summary)
                'company_box_text': (22, 38, 48),  # dark slate — readable on teal box
                'event_badge_bg': (185, 45, 45),
                'event_badge_text': (255, 255, 255),
                'border': (72, 75, 81),
                'summary_bg': (88, 101, 242)  # Discord blurple for summary
            }
        elif self.theme == 'light':
            self.colors = {
                'bg': (255, 255, 255),
                'header': (66, 139, 202),
                'row_bg_1': (249, 249, 249),
                'row_bg_2': (255, 255, 255),
                'text': (51, 51, 51),
                'footer': (128, 128, 128),
                'positive': (46, 125, 50),
                'negative': (211, 47, 47),
                'neutral': (51, 51, 51),
                'ticker_bg': (240, 240, 240),
                'ticker_box': (66, 103, 178),
                'company_box': (91, 155, 175),
                'company_box_text': (22, 38, 48),
                'event_badge_bg': (185, 45, 45),
                'event_badge_text': (255, 255, 255),
                'border': (200, 200, 200),
                'summary_bg': (66, 139, 202)
            }
        else:
            # Default to discord_dark
            self.theme = 'discord_dark'
            self._set_color_scheme()
    
    def _load_fonts(self):
        """Load fonts with fallback to default."""
        self.fonts = {}
        # Try multiple font paths for cross-platform compatibility
        # Windows paths
        font_paths = [
            'arial.ttf',
            'Arial.ttf',
            'arial.ttc',
            # Linux paths
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/ttf-dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/TTF/DejaVuSans.ttf',
            # macOS paths (if needed)
            '/System/Library/Fonts/Helvetica.ttc',
            '/Library/Fonts/Arial.ttf',
        ]
        
        for size_name, size in self.font_sizes.items():
            font_loaded = False
            for font_path in font_paths:
                try:
                    self.fonts[size_name] = ImageFont.truetype(font_path, size)
                    font_loaded = True
                    break
                except (OSError, IOError):
                    continue
            
            if not font_loaded:
                # Fallback to default font
                self.fonts[size_name] = ImageFont.load_default()
    
    def create_portfolio_image(self, 
                             user_data: Dict[str, Any], 
                             game_data: Dict[str, Any],
                             stock_picks: List[Dict[str, Any]],
                             info,
                             show_footer: bool = True,
                             fund_label: Optional[str] = None) -> BytesIO:
        """
        Create a stock portfolio image from user, game, and stock data.
        
        Args:
            user_data (Dict): User information containing display_name, etc.
            game_data (Dict): Game information containing name, id, etc.
            stock_picks (List[Dict]): List of stock pick dictionaries
            show_footer (bool): Whether to show the bot footer
            
        Returns:
            BytesIO: Buffer containing the PNG image
        """
        # Filter only owned stocks for display
        owned_stocks = [pick for pick in stock_picks if pick.get('status') == 'owned']
        pending_stocks = [pick for pick in stock_picks if pick.get('status') == 'pending_buy']
        all_stocks = {'owned': owned_stocks, 'pending': pending_stocks}
        
        # Calculate image height
        extra_height = 140 if owned_stocks or pending_stocks else 80  # Extra space for summary or no stocks message
        height = self.base_height + ((len(owned_stocks) + len(pending_stocks)) * self.row_height) + extra_height
        
        # Create image
        img = Image.new('RGB', (self.width, height), self.colors['bg'])
        draw = ImageDraw.Draw(img)
        
        y_offset = 20
        
        # Draw title
        user_name = user_data.get('display_name', 'Unknown User')
        game_name = game_data.get('name', 'Unknown Game')
        game_id = game_data.get('id', 'N/A')
        
        title_text = f"{user_name}'s Portfolio"
        subtitle_text = f"Game: {game_name} (ID: {game_id})"

        title_text = self._truncate_to_width(
            draw, title_text, self.fonts['title'], max_width=self.width - 40,
        )
        y_offset = self._draw_centered_text(draw, title_text, y_offset, self.fonts['title'], self.colors['text'])
        y_offset = self._draw_centered_text(draw, subtitle_text, y_offset, self.fonts['text'], self.colors['footer'])
        y_offset += 15
        
        if owned_stocks or pending_stocks:
            # Calculate and draw portfolio summary
            y_offset = self._draw_portfolio_summary(
                draw, all_stocks, y_offset, info, fund_label=fund_label,
            )
            y_offset += 20
            
            # Draw stock table
            y_offset = self._draw_stock_header(draw, y_offset)
            y_offset = self._draw_stock_rows(draw, all_stocks, y_offset, info)
        else:
            # No stocks message
            no_stocks_text = "No stocks currently owned"
            y_offset = self._draw_centered_text(draw, no_stocks_text, y_offset + 40, 
                                              self.fonts['text'], self.colors['footer'])
        
        # Add footer if requested - position it below the content with proper spacing
        if show_footer:
            # Add padding before footer
            footer_y = y_offset + 20
            # Ensure image is tall enough for footer
            footer_height = footer_y + 30
            if footer_height > height:
                # Resize image to accommodate footer
                new_img = Image.new('RGB', (self.width, footer_height), self.colors['bg'])
                new_img.paste(img)
                img = new_img
                draw = ImageDraw.Draw(img)
            self._draw_footer(draw, footer_height, footer_y, last_updated=stock_picks[0].get('last_updated') if stock_picks else None)

        # Save to BytesIO buffer
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        return buffer
    
    def _draw_centered_text(self, draw: ImageDraw.ImageDraw, text: str, y: int, font, color) -> int:
        """Draw centered text and return new y position."""
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (self.width - text_width) // 2
        draw.text((x, y), text, fill=color, font=font)
        return int(y + text_height + 10)
    
    def _draw_portfolio_summary(
        self,
        draw: ImageDraw.ImageDraw,
        all_stocks: Dict[str, List[Dict]],
        y_offset: int,
        info,
        *,
        fund_label: Optional[str] = None,
    ) -> int:
        """Draw portfolio summary section."""
        owned_stocks = all_stocks['owned']
        pending_stocks = all_stocks['pending']

        # Calculate totals
        total_value = sum(float(stock.get('current_value', 0)) for stock in owned_stocks)
        total_gain_dollars = sum(float(stock.get('change_dollars', 0)) for stock in owned_stocks)
        
        # Calculate percentage gain (avoid division by zero)
        total_invested = total_value - total_gain_dollars
        total_gain_percent = (total_gain_dollars / total_invested * 100) if total_invested != 0 else 0
        
        # Calculate pending stocks amount and money left
        start_money = float(info.game.start_money)
        pick_count = int(info.game.pick_count)
        value_per_pick = start_money / pick_count if pick_count > 0 else 0
        pending_stocks_amount = len(pending_stocks) * value_per_pick
        filled_slots = len(owned_stocks) + len(pending_stocks)
        money_left = start_money - (filled_slots * value_per_pick)
        
        # Summary box - taller to accommodate 5 items in 2 rows
        summary_rect = [50, y_offset, self.width - 50, y_offset + 110]
        draw.rectangle(summary_rect, fill=self.colors['summary_bg'], outline=self.colors['border'])
        
        # Summary text in three columns for first row
        col1_x = 70
        col2_x = 310
        col3_x = 550
        row1_y = y_offset + 12
        row2_y = y_offset + 60
        
        # Row 1: Portfolio value, Gain/Loss, Return %
        draw.text((col1_x, row1_y), "Total Portfolio Value:", fill=self.colors['text'], font=self.fonts['text'])
        draw.text((col1_x, row1_y + 20), f"${total_value:,.2f}", fill=self.colors['text'], font=self.fonts['header'])
        
        gain_color = self.colors['positive'] if total_gain_dollars >= 0 else self.colors['negative']
        draw.text((col2_x, row1_y), "Total Gain/Loss:", fill=self.colors['text'], font=self.fonts['text'])
        draw.text((col2_x, row1_y + 20), format_dollar_gain(total_gain_dollars), fill=gain_color, font=self.fonts['header'])
        
        percent_color = self.colors['positive'] if total_gain_percent >= 0 else self.colors['negative']
        draw.text((col3_x, row1_y), "Total Return:", fill=self.colors['text'], font=self.fonts['text'])
        draw.text((col3_x, row1_y + 20), f"{total_gain_percent:+.2f}%", fill=percent_color, font=self.fonts['header'])
        
        # Row 2: Pending Stocks Amount, Money Left
        draw.text((col1_x, row2_y), "Pending Stocks Amount:", fill=self.colors['text'], font=self.fonts['text'])
        draw.text((col1_x, row2_y + 20), f"${pending_stocks_amount:,.2f}", fill=self.colors['text'], font=self.fonts['header'])
        
        draw.text((col2_x, row2_y), "Money Left to Spend:", fill=self.colors['text'], font=self.fonts['text'])
        draw.text((col2_x, row2_y + 20), f"${money_left:,.2f}", fill=self.colors['text'], font=self.fonts['header'])

        if fund_label:
            fund_text = self._truncate_to_width(
                draw, fund_label, self.fonts['header'], max_width=180,
            )
            draw.text((col3_x, row2_y), "Fund:", fill=self.colors['text'], font=self.fonts['text'])
            draw.text((col3_x, row2_y + 20), fund_text, fill=self.colors['text'], font=self.fonts['header'])
        
        return y_offset + 110
    
    def _portfolio_company_name(self, stock: Dict[str, Any]) -> Optional[str]:
        """Return a display company name when it adds information beyond the ticker."""
        ticker = str(stock.get("stock_ticker") or stock.get("ticker") or "")
        raw = stock.get("company_name") or stock.get("company") or ""
        name = str(raw).strip()
        if not name:
            return None
        if name.upper() == ticker.upper():
            return None
        return name

    def _truncate_to_width(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font,
        max_width: int,
    ) -> str:
        if not text:
            return ""
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text
        ellipsis = "..."
        low = 0
        high = len(text)
        while low < high:
            mid = (low + high + 1) // 2
            candidate = f"{text[:mid]}{ellipsis}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                low = mid
            else:
                high = mid - 1
        return f"{text[:low]}{ellipsis}" if low > 0 else ellipsis

    def _text_size(self, draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])

    def _draw_centered_text_in_rect(
        self,
        draw: ImageDraw.ImageDraw,
        rect: list[int],
        text: str,
        font,
        fill,
    ) -> None:
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        draw.text((cx, cy), text, fill=fill, font=font, anchor="mm")

    def _draw_left_text_in_rect(
        self,
        draw: ImageDraw.ImageDraw,
        rect: list[int],
        text: str,
        font,
        fill,
        *,
        pad_x: int = 8,
    ) -> None:
        cy = (rect[1] + rect[3]) // 2
        draw.text((rect[0] + pad_x, cy), text, fill=fill, font=font, anchor="lm")

    def _stock_display_name(self, stock: Dict[str, Any], *, pending: bool = False) -> str:
        """Format ``<Ticker> | <Company Name>`` for the row title line."""
        ticker = str(stock.get("stock_ticker", "N/A"))
        if pending:
            ticker = f"{ticker}*"
        company = self._portfolio_company_name(stock)
        if company:
            return f"{ticker} | {company}"
        return ticker

    def _draw_stock_title(
        self,
        draw: ImageDraw.ImageDraw,
        y_offset: int,
        stock: Dict[str, Any],
        *,
        pending: bool = False,
    ) -> None:
        """Draw ticker and company label boxes above stat columns."""
        inset = self.content_inset
        title_left = inset + self.table_stat_pad
        inner_right = self.width - inset
        title_right = inner_right - self.table_stat_pad
        inner_width = title_right - title_left
        bar_top = y_offset + 6
        bar_height = 22
        gap = 4
        pad_x = 8
        ticker_color = self.colors["ticker_box"]
        company_color = self.colors["company_box"]

        ticker = str(stock.get("stock_ticker", "N/A"))
        if pending:
            ticker = f"{ticker}*"
        company = self._portfolio_company_name(stock)
        event_label = str(stock.get("event_label") or "").strip()

        badge_w = 0
        badge_label = ""
        if event_label:
            badge_label = self._truncate_to_width(
                draw, event_label, self.fonts["text"], max(inner_width // 3, 80)
            )
            badge_w = self._text_size(draw, badge_label, self.fonts["text"])[0] + pad_x * 2
            badge_w = min(max(badge_w, 60), inner_width // 2)

        if company:
            max_ticker_inner = min(120, inner_width // 3)
            ticker_label = self._truncate_to_width(
                draw,
                ticker,
                self.fonts["text"],
                max(max_ticker_inner - pad_x * 2, 24),
            )
            ticker_w = self._text_size(draw, ticker_label, self.fonts["text"])[0] + pad_x * 2
            ticker_w = max(min(ticker_w, max_ticker_inner), 52)

            company_left = title_left + ticker_w + gap
            reserved = badge_w + (gap if badge_w else 0)
            max_company_inner = max(title_right - company_left - reserved - pad_x * 2, 24)
            company_label = self._truncate_to_width(
                draw,
                company,
                self.fonts["text"],
                max_company_inner,
            )
            company_w = self._text_size(draw, company_label, self.fonts["text"])[0] + pad_x * 2

            ticker_rect = [title_left, bar_top, title_left + ticker_w, bar_top + bar_height]
            company_rect = [company_left, bar_top, company_left + company_w, bar_top + bar_height]

            draw.rectangle(ticker_rect, fill=ticker_color)
            draw.rectangle(company_rect, fill=company_color)

            self._draw_centered_text_in_rect(
                draw,
                ticker_rect,
                ticker_label,
                self.fonts["text"],
                self.colors["text"],
            )
            self._draw_left_text_in_rect(
                draw,
                company_rect,
                company_label,
                self.fonts["text"],
                self.colors["company_box_text"],
                pad_x=pad_x,
            )
            if badge_w:
                badge_left = company_rect[2] + gap
                badge_rect = [badge_left, bar_top, badge_left + badge_w, bar_top + bar_height]
                draw.rectangle(badge_rect, fill=self.colors["event_badge_bg"])
                self._draw_centered_text_in_rect(
                    draw,
                    badge_rect,
                    badge_label,
                    self.fonts["text"],
                    self.colors["event_badge_text"],
                )
            return

        ticker_label = self._truncate_to_width(
            draw,
            ticker,
            self.fonts["text"],
            inner_width - pad_x * 2,
        )
        ticker_w = self._text_size(draw, ticker_label, self.fonts["text"])[0] + pad_x * 2
        ticker_w = min(max(ticker_w, 52), inner_width)
        ticker_rect = [title_left, bar_top, title_left + ticker_w, bar_top + bar_height]
        draw.rectangle(ticker_rect, fill=ticker_color)
        self._draw_centered_text_in_rect(
            draw,
            ticker_rect,
            ticker_label,
            self.fonts["text"],
            self.colors["text"],
        )

    def _draw_stock_header(self, draw: ImageDraw.ImageDraw, y_offset: int) -> int:
        """Draw stock table header."""
        inset = self.content_inset
        header_rect = [inset, y_offset, self.width - inset, y_offset + 35]
        draw.rectangle(header_rect, fill=self.colors['header'])
        
        headers = ["Price", "Shares", "Value", "$ Gain", "% Gain"]
        header_bottom = y_offset + 35
        for index, header_text in enumerate(headers):
            self._draw_stat_cell(
                draw,
                index,
                y_offset,
                header_bottom,
                header_text,
                self.fonts["header"],
                self.colors["text"],
            )
        
        return y_offset + 35
    
    def _draw_stock_rows(self, draw: ImageDraw.ImageDraw, all_stocks: Dict[str, List[Dict]], y_offset: int, info) -> int:
        """Draw stock rows."""
        owned_stocks = all_stocks['owned']
        pending_stocks = all_stocks['pending']
        
        # Calculate value per pick for pending stocks
        start_money = float(info.game.start_money)
        pick_count = int(info.game.pick_count)
        value_per_pick = start_money / pick_count if pick_count > 0 else 0

        # Draw owned stocks
        for idx, stock in enumerate(owned_stocks):
            stats_y = y_offset + 34
            # Alternating row colors
            row_color = self.colors['row_bg_1'] if idx % 2 == 0 else self.colors['row_bg_2']
            inset = self.content_inset
            row_rect = [inset, y_offset, self.width - inset, y_offset + self.row_height]
            draw.rectangle(row_rect, fill=row_color)
            
            # Extract stock data with safe defaults
            shares = float(stock.get('shares', 0))
            current_value = float(stock.get('current_value', 0))
            change_dollars = float(stock.get('change_dollars', 0))
            change_percent = float(stock.get('change_percent', 0))
            
            # Calculate share price
            share_price = current_value / shares if shares > 0 else 0
            
            self._draw_stock_title(draw, y_offset, stock)
            
            stats_bottom = y_offset + self.row_height - 6
            # Draw price
            price_text = f"${share_price:,.2f}"
            self._draw_stat_cell(
                draw, 0, stats_y, stats_bottom, price_text, self.fonts["text"], self.colors["text"]
            )
            
            # Draw shares
            shares_text = f"{shares:,.2f}" if shares else "0"
            self._draw_stat_cell(
                draw, 1, stats_y, stats_bottom, shares_text, self.fonts["text"], self.colors["text"]
            )
            
            # Draw value
            value_text = f"${current_value:,.2f}"
            self._draw_stat_cell(
                draw, 2, stats_y, stats_bottom, value_text, self.fonts["text"], self.colors["text"]
            )
            
            # Draw dollar gain (color coded)
            dollar_text = format_dollar_gain(change_dollars)
            dollar_color = self.colors['positive'] if change_dollars >= 0 else self.colors['negative']
            self._draw_stat_cell(
                draw, 3, stats_y, stats_bottom, dollar_text, self.fonts["text"], dollar_color
            )
            
            # Draw percentage gain (color coded)
            percent_text = f"{change_percent:+.2f}%"
            percent_color = self.colors['positive'] if change_percent >= 0 else self.colors['negative']
            self._draw_stat_cell(
                draw, 4, stats_y, stats_bottom, percent_text, self.fonts["text"], percent_color
            )
            
            y_offset += self.row_height
        
        # Draw pending stocks
        for idx, stock in enumerate(pending_stocks):
            stats_y = y_offset + 34
            # Continue alternating pattern from owned stocks
            total_owned = len(owned_stocks)
            row_color = self.colors['row_bg_1'] if (total_owned + idx) % 2 == 0 else self.colors['row_bg_2']
            inset = self.content_inset
            row_rect = [inset, y_offset, self.width - inset, y_offset + self.row_height]
            draw.rectangle(row_rect, fill=row_color)
            
            # Pending stocks use current_value if available, otherwise value_per_pick
            pending_value = value_per_pick
            
            # Try to get price if available (pending stocks might have price data)
            shares = stock.get('shares')
            if shares and float(shares) > 0:
                share_price = pending_value / float(shares)
            else:
                share_price = 0
            
            self._draw_stock_title(draw, y_offset, stock, pending=True)
            
            stats_bottom = y_offset + self.row_height - 6
            # Draw price (show N/A if not available)
            if share_price > 0:
                price_text = f"${share_price:,.2f}"
            else:
                price_text = "N/A"
            self._draw_stat_cell(
                draw, 0, stats_y, stats_bottom, price_text, self.fonts["text"], self.colors["footer"]
            )
            
            # Draw shares - show "Pending" instead
            self._draw_stat_cell(
                draw, 1, stats_y, stats_bottom, "Pending", self.fonts["text"], self.colors["footer"]
            )
            
            # Draw value (the allocated amount)
            value_text = f"${pending_value:,.2f}"
            self._draw_stat_cell(
                draw, 2, stats_y, stats_bottom, value_text, self.fonts["text"], self.colors["text"]
            )
            
            # Draw dollar gain - show "-" for pending
            self._draw_stat_cell(
                draw, 3, stats_y, stats_bottom, "-", self.fonts["text"], self.colors["footer"]
            )
            
            # Draw percentage gain - show "-" for pending
            self._draw_stat_cell(
                draw, 4, stats_y, stats_bottom, "-", self.fonts["text"], self.colors["footer"]
            )
            
            y_offset += self.row_height
        
        return y_offset
    
    def _draw_footer(self, draw: ImageDraw.ImageDraw, height: int, footer_y: int, last_updated: Optional[datetime.datetime] = None):
        """Draw footer text."""
        draw.text((self.content_inset, footer_y), "Last Updated: " +
                  (last_updated.strftime("%Y-%m-%d %H:%M:%S") if last_updated else "Generated by StockBot"), 
                 fill=self.colors['footer'], font=self.fonts['small'])


# Convenience function to create portfolio image
def create_portfolio_image(user_data: Dict[str, Any],
                          game_data: Dict[str, Any], 
                          stock_picks: List[Dict[str, Any]],
                          info: Any = None,
                          theme: str = 'discord_dark') -> BytesIO:
    """
    Convenience function to create a stock portfolio image.
    
    Args:
        user_data: User information dictionary
        game_data: Game information dictionary  
        stock_picks: List of stock pick dictionaries
        info: GameInfo or compatible object with .game.start_money / .game.pick_count
        theme: Color theme to use
        
    Returns:
        BytesIO buffer containing the PNG image
    """
    generator = get_portfolio_generator(theme) if theme == "discord_dark" else StockPortfolioImageGenerator(theme=theme)
    return generator.create_portfolio_image(user_data, game_data, stock_picks, info=info)


_LEADERBOARD_GENERATOR: LeaderboardImageGenerator | None = None
_PORTFOLIO_GENERATOR: StockPortfolioImageGenerator | None = None


def get_leaderboard_generator(theme: str = "discord_dark") -> LeaderboardImageGenerator:
    """Shared discord_dark leaderboard generator (fonts loaded once)."""
    global _LEADERBOARD_GENERATOR
    if theme != "discord_dark":
        return LeaderboardImageGenerator(theme=theme)
    if _LEADERBOARD_GENERATOR is None:
        _LEADERBOARD_GENERATOR = LeaderboardImageGenerator(theme="discord_dark")
    return _LEADERBOARD_GENERATOR


def get_portfolio_generator(theme: str = "discord_dark") -> StockPortfolioImageGenerator:
    """Shared discord_dark portfolio generator (fonts loaded once)."""
    global _PORTFOLIO_GENERATOR
    if theme != "discord_dark":
        return StockPortfolioImageGenerator(theme=theme)
    if _PORTFOLIO_GENERATOR is None:
        _PORTFOLIO_GENERATOR = StockPortfolioImageGenerator(theme="discord_dark")
    return _PORTFOLIO_GENERATOR
