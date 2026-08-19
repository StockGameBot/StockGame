# BUILT-IN
from datetime import datetime, date
from typing import Optional, Literal, TypeVar

# EXTERNAL
from pydantic import BaseModel, Field, PositiveInt, PositiveFloat, field_validator, TypeAdapter, AliasChoices, ConfigDict


# Statuses
MainStatus = Literal['success', 'error']
GameStatus = Literal['open', 'active', 'ended']
ParticipantStatus = Literal['pending', 'active', 'inactive']
PickStatus = Literal['pending_buy', 'owned', 'pending_sell', 'sold']
UpdateFrequency = Literal['daily', 'hourly', 'minute', 'realtime', 'alpaca']
PydanticModelType = TypeVar('PydanticModelType', bound=BaseModel)
GameTemplateStatus = Literal['enabled', 'disabled']


class Status(BaseModel): # Status item
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    status: str
    reason: str
    result: Optional[str | int | dict | tuple | Exception] = None
    more_info: Optional[str | int | dict | tuple | Exception] = None

# User 
class User(BaseModel):
    id: int = Field(validation_alias=AliasChoices('user_id'))
    display_name: Optional[str] = None
    source: Optional[str] = None
    overall_wins: int
    change_dollars: Optional[float] = None
    change_percent: Optional[float] = None
    permissions: int = 210
    datetime_created: datetime
    last_updated: Optional[datetime] = None
    
Users = TypeAdapter(list[User])

# Game
class Game(BaseModel):
    id: str | int = Field(validation_alias=AliasChoices('game_id')) # After v0.0.5 game IDs should be strings
    template_id: Optional[int] = None
    name: str = Field(max_length=35, min_length=1) # Prevent blank names
    owner_id: int = Field(validation_alias=AliasChoices('owner_user_id'))
    start_money: PositiveFloat 
    pick_count: PositiveInt
    pick_date: Optional[date] = None # YYYY-MM-DD
    draft_mode: bool = False
    private_game: bool = False
    allow_selling: bool = False
    update_frequency: UpdateFrequency = 'alpaca'
    start_date: date # YYYY-MM-DD
    end_date: Optional[date] = None # YYYY-MM-DD
    status: GameStatus = 'open'
    current_value: Optional[float] = Field(default=None, validation_alias=AliasChoices('aggregate_value'))
    change_dollars: Optional[float] = None
    change_percent: Optional[float] = None
    leaderboard_message_id: Optional[str] = None
    top_roles_applied: bool = False
    datetime_created: datetime # YYYY-MM-DD HH:MM:SS
    last_updated: Optional[datetime] = Field(default=None, validation_alias=AliasChoices('datetime_updated', 'last_updated')) # YYYY-MM-DD HH:MM:SS

    @field_validator('name') 
    def game_name(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError('Game name must not be blank.')
        return value

Games = TypeAdapter(list[Game])

# Game Template
class GameTemplate(BaseModel):
    id: int = Field(validation_alias=AliasChoices('template_id'))
    name: str = Field(max_length=35, min_length=1, validation_alias=AliasChoices('game_name')) # Prevent blank names
    status: GameTemplateStatus
    owner_id: int = Field(validation_alias=AliasChoices('owner_user_id'))
    start_money: PositiveFloat 
    pick_count: PositiveInt
    pick_date: Optional[int] = None  # Optional[date] = None # YYYY-MM-DD
    draft_mode: bool = False
    private_game: bool = False
    allow_selling: bool = False
    update_frequency: UpdateFrequency = 'alpaca'
    start_date: date # YYYY-MM-DD
    create_days_in_advance: int
    recurring_period: int
    game_length: int
    push_leaderboard: bool = False
    leaderboard_channel_id: Optional[str] = None
    auto_top_roles: bool = False
    datetime_created: datetime # YYYY-MM-DD HH:MM:SS
    last_updated: Optional[datetime] = None # YYYY-MM-DD HH:MM:SS

    @field_validator('name') 
    def game_name(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError('Game name must not be blank.')
        return value

GameTemplates = TypeAdapter(list[GameTemplate])


class TemplateRoleHolder(BaseModel):
    model_config = ConfigDict(extra='ignore')

    template_id: int
    rank: int
    user_id: int
    game_id: str
    datetime_awarded: datetime


TemplateRoleHolders = TypeAdapter(list[TemplateRoleHolder])

# Stock
class Stock(BaseModel):
    id: int = Field(validation_alias=AliasChoices('stock_id'))
    ticker: str
    exchange: str
    company: str = Field(validation_alias=AliasChoices('company_name'))

    @field_validator('ticker', 'exchange')
    def string_exists(cls, value, field):
        if isinstance(value, str) and not value.strip():
            raise ValueError(f'{field.name}  must not be blank.')
        return value
        
    @field_validator('exchange')
    def exchange_fix(cls, value: str) -> str:
        if value:
            return value.lower()
        return value
    
    @field_validator('ticker')
    def ticker_fix(cls, value: str) -> str:
        if value:
            return value.upper()
        return value
    
Stocks = TypeAdapter(list[Stock])


# Stock prices
class StockPrice(BaseModel):
    id: int = Field(validation_alias=AliasChoices('price_id'))
    stock_id: int
    price: float
    datetime: datetime # YYYY-MM-DD HH:MM:SS

StockPrices = TypeAdapter(list[StockPrice])


class GameParticipant(BaseModel):
    id: int = Field(validation_alias=AliasChoices('participation_id'))
    user_id: int
    game_id: int | str
    status: ParticipantStatus = 'active'
    datetime_joined: datetime # YYYY-MM-DD HH:MM:SS 
    current_value: Optional[float] = None
    change_dollars: Optional[float] = None
    change_percent: Optional[float] = None
    days_in_first: int = 0
    last_updated: Optional[datetime] = Field(default=None, validation_alias=AliasChoices('datetime_updated', 'last_updated')) # YYYY-MM-DD HH:MM:SS
    
GameParticipants = TypeAdapter(list[GameParticipant])


class GameInvite(BaseModel):
    model_config = ConfigDict(extra='ignore')

    id: int = Field(validation_alias=AliasChoices('invite_id'))
    game_id: str
    user_id: int
    inviter_id: int
    dm_channel_id: Optional[int] = None
    dm_message_id: Optional[int] = None
    status: str = 'pending'
    datetime_created: datetime
    datetime_updated: Optional[datetime] = None


GameInvites = TypeAdapter(list[GameInvite])


class StockPick(BaseModel):
    model_config = ConfigDict(extra='ignore') # Ignore extra data
    
    id: int = Field(validation_alias=AliasChoices('pick_id'))
    participation_id: int
    stock_id: int
    shares: Optional[float] = None
    start_value: Optional[float] = None
    current_value: Optional[float] = None
    change_dollars: Optional[float] = None
    change_percent: Optional[float] = None
    status: PickStatus = 'pending_buy'
    stock_ticker: Optional[str] = Field(default=None, validation_alias=AliasChoices('ticker')) # Allow ticker to be added in here.  Purely for ease of use
    company_name: Optional[str] = None
    datetime_created: datetime # YYYY-MM-DD HH:MM:SS
    last_updated: Optional[datetime] = Field(default=None, validation_alias=AliasChoices('datetime_updated', 'last_updated')) # YYYY-MM-DD HH:MM:SS

StockPicks = TypeAdapter(list[StockPick])


class MyGames(BaseModel):
    user: User
    games: list[Game]


class GameLeaderboard(BaseModel):
    user_id: int
    current_value: float
    joined: datetime
    change_dollars: float
    change_percent: float
    days_in_first: int = 0
    display_name: Optional[str] = None
    last_updated: datetime | None = None

class GameInfo(BaseModel):
    game: Game
    leaderboard: Optional[list[GameLeaderboard]] = None
    