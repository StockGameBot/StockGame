# BUILT-IN
from datetime import datetime, timedelta, date
import logging
import os
import random
import string
import re
from typing import Any, Literal, Optional, Type, cast, get_args

# EXTERNAL
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from pydantic import TypeAdapter
from pydantic import ValidationError
import pytz

# INTERNAL
import helpers.datatype_validation as dtv
import helpers.exceptions as bexc
from helpers.alpaca_client import AlpacaMarketData, to_alpaca_symbol, to_db_ticker
from helpers.sqlhelper import SqlHelper, _iso8601, Status
from helpers.db_backup import maybe_daily_backup, maybe_hourly_backup
from db_schema import create as create_db

load_dotenv() 

version = "???" #TODO should frontend and backend have different versions?

class Backend:
    # Raise Exceptions if bad data is passed in
    # Most of these expect that the data being sent has been checked or otherwise verified.  End users should not interact directly with this
    def __init__(self, db_name:str):
        """Methods for interacting directly with the database
        
        Methods in this class will only perform basic validation of what is sent to prevent the database from being damaged.  These methods should never be directly interacted with by users.  The `Frontend()` class should be used instead.
                
        Updating stock prices, picks, etc. are handled in `GameLogic()`
        
        Args:
            db_name (str): Database name.
        """
        
        create_db(db_name) # Try to create DB
        self.logger = logging.getLogger('StockBackend')
        self.sql = SqlHelper(db_name)
        self.logger.info('Initiated new Backend instance.')
        

    # # INTERNAL # #
    def _single_get(self, model:Type[dtv.PydanticModelType], resp:Status)-> dtv.PydanticModelType: # Handle single gets
        """Check, validate, and format single get requests

        Args:
            model (Type[dtv.PydanticModelType]): Model to validate item against.
            resp (Status): Status object from sqlhelper.

        Raises:
            LookupError(Item not found.):  Raised if no items are found.
            LookupError('Expected one item, but got x): Raised if more than one item is found.
            Exception(Failed to get item.(more info)): Raised if another issue is encountered.

        Returns:
            dtv.PydanticModelType: Will return the validated and formatted object for item.
        """
        
        if resp.status == 'success':
            assert isinstance(resp.result, tuple) # Do this here because its not a tuple if its not successful
            if len(resp.result) == 1: # Single object (expected)
                return model.model_validate(resp.result[0])
            else:
                raise LookupError(f'Expected one item, but got {len(resp.result)}.')
            
        elif resp.reason == 'NO ROWS RETURNED': # Response is not success so can just check what the error is
            # Expected control flow for existence checks (e.g. add_stock before insert).
            self.logger.debug('Get item not found. %s', resp)
            raise LookupError(f'Item not found.')
        else:
            raise Exception('Failed to get item.', resp)
        
    def _many_get(self, typeadapter:TypeAdapter, resp:Status)-> tuple:
        """Check, validate, and format multi get requests

        Args:
            typeadapter (TypeAdapter): Wrapper for multiple objects that need to be validated
            resp (Status): Status object from sqlhelper.

        Raises:
            LookupError(No items found):  Raised if no items are found.
            Exception(Failed to get items.(more info)): Raised if another issue is encountered.

        Returns:
            tuple: Tuple of formatted objects.
        """
        if resp.status == 'success':
            assert isinstance(resp.result, tuple) #  Real and true
            return tuple(typeadapter.validate_python(resp.result))
        elif resp.reason == 'NO ROWS RETURNED': # Response is not success so can just check what the error is
            raise LookupError('No items found')
        else:
            raise Exception(f'Failed to get items.', resp)
        
    def _validate_date(self, date:str, format:str='%Y-%m-%d')-> bool: # #TODO is this really needed anymore?
        """Attempt to validate a string formatted date

        Args:
            date (str): Unvalidated date.
            format (str, optional): datetime formatting string. Defaults to '%Y-%m-%d'.

        Returns:
            bool: True if valid.
        """
        try:
            validated: datetime = datetime.strptime(date, format)
            return True
        except ValueError:
            return False
        
    def _update_single(self, table:str, id_column:str, item_id:int | str, **update_columns):            
        resp = self.sql.update(table=table, filters={id_column: item_id}, items=update_columns) 
        if resp.status != 'success': #TODO errors
            if resp.reason == 'NO ROWS EFFECTED':
                raise bexc.DoesntExistError(table=table, item=item_id)
            if resp.reason == 'SQLITE_CONSTRAINT_CHECK':
                raise ValueError(resp.result) # Pass on the result
            else:
                raise Exception(f'Failed to update item {item_id} in table {table}.', resp) # Worst case error where nothing was caught
        
    def _delete_single(self, table:str, id_column:str, item_id:int | str):            
        resp = self.sql.delete(table=table, filters={id_column: item_id}) 
        if resp.status != 'success': #TODO errors
            if resp.reason == 'NO ROWS EFFECTED':
                raise bexc.DoesntExistError(table=table, item=item_id)
            else:
                raise Exception(f'Failed to delete item {item_id} in table {table}.', resp) # Worst case error where nothing was caught
    
    # # USER ACTIONS # #
    def add_user(self, user_id:int, source:str, display_name:Optional[str]=None, permissions:int = 210):
        """Add a user
        
        Add a single user to the database

        Args:
            user_id (int): UNIQUE ID to identify user.
            source (str): Source of user.  EG: Discord.
            display_name (str): Username/Displayname for user.
            permissions (int, optional): User permissions (see). - UNUSED in V1.0.0
        """
        
        self.logger.debug(f'Adding user {user_id} to database.  source: {source}, display_name: {display_name}.')
        items = {
            'user_id': user_id,
            'display_name':display_name,
            'source': source,
            'permissions': permissions,
            'datetime_created': _iso8601()
            }
        
        resp = self.sql.insert(table='users', items=items)
        if resp.status != 'success': #TODO errors
            if resp.reason == 'SQLITE_CONSTRAINT_PRIMARYKEY': # User already in the database
                self.logger.debug(f'User {user_id} already registered.')
                raise bexc.UserExistsError(user_id=user_id)
            self.logger.error(f'Failed to add user: {user_id}. Reason: {resp}')
            if resp.reason == 'SQLITE_MISMATCH': # Invalid data in one of the fields
                raise bexc.WrongTypeError(table='users')
            else: # Can't think of any other issues you could have with this honestly
                raise Exception(f'Failed to add user.', resp)
        else:
            self.logger.debug(f'Added user {user_id}.')
            
        
    def get_user(self, user_id:int) -> dtv.User:
        """Get a single user

        Args:
            user_id (int): User ID.

        Returns:
            dict: User information.
        """
        self.logger.debug(f'Getting user: {user_id}.')
        resp = self.sql.get(table='users', filters={'user_id': user_id})
        return self._single_get(model=dtv.User, resp=resp)
    
    def get_many_users(self, display_name:Optional[str]=None, source:Optional[str]=None, permissions:Optional[int]=None, ids_only:bool=False) -> tuple[dtv.User, ...] | tuple[int, ...]: 
        """Get multiple users

        Args:
            display_name (Optional[str], optional): Filter by display name.
            source (Optional[str], optional): Filter by source.
            permissions (Optional[int], optional): Filter by permission.
            ids_only (bool, optional): Return only user IDs. Defaults to False.

        Returns:
            tuple: Matching users.
        """
        #TODO implement source and permission filtering
        
        filters = {
            'display_name': display_name,
            'source': source,
            'permissions': permissions
            }
        
        resp = self.sql.get(table='users', filters=filters)
        users = self._many_get(typeadapter=dtv.Users, resp=resp)
        if ids_only:
            ids = tuple([user.id for user in users])
            return ids 
        else:
            return users
         
    def update_user(self, user_id: int, source:Optional[str]=None, display_name:Optional[str]=None, overall_wins:Optional[int]=None, change_dollars:Optional[float]=None, change_percent:Optional[float]=None, permissions:Optional[int]=None):
        
        """Update an existing user
        
        Must provide atleast one arg to update in addition to the user_id

        Args:
            user_id (int): User ID.
            display_name (str, optional): Display name.
            permissions (str, optional): Permissions.
            overall_wins (int, optional): Total game wins.
            change_dollars (float, optional): Overall change dollars across all games (completed only).
            change_percent (float, optional): Overall change percent across all games (completed only).
        
        Raises:
            ValueError(Atleast one arg must be changed.): Raised if no args besides user_id are passed.
        """
        
        if all(value is None for value in (display_name, permissions, source, overall_wins, change_dollars, change_percent)):
            raise ValueError('Atleast one arg must be changed.')
        
        self._update_single(
            table="users",
            id_column='user_id',
            item_id=user_id,
            source=source,
            overall_wins=overall_wins,
            change_dollars=change_dollars,
            change_percent=change_percent,
            display_name=display_name,
            permissions=permissions,
            last_updated=_iso8601()
        )
        
    def remove_user(self, user_id:int): 
        """Remove a user

        Args:
            user_id (int): User ID.
        """
        
        self._delete_single(table="users", id_column='user_id', item_id=user_id)
    
    
    def generate_alnum_id(self) -> str:
        chars = chars = string.ascii_uppercase + string.digits
        id = ''.join(random.choices(chars, k=5))
        try:
            self.get_game(id)
            return self.generate_alnum_id()
        except LookupError:
            return id

    # # GAME ACTIONS # #
    def add_game(self, user_id:int, name:str, start_date:str | date, end_date:Optional[str | date]=None, starting_money:float=10000.00, pick_date:Optional[str | date]=None, private_game:bool=False, total_picks:int=10, exclusive_picks:bool=False, sell_during_game:bool=False, update_frequency:dtv.UpdateFrequency='alpaca', template_id:Optional[int]=None) -> str:
        """Add a new game
        
        WARNING: If using realtime, expect issues

        Args:
            user_id (int): Game creators user ID.
            name (str): Name for this game.  Maximum 35 chatacters.
            start_date (str): Start date.  Format: `YYYY-MM-DD`.
            end_date (str, optional): End date.  Format: `YYYY-MM-DD`.  Leave blank for infinite game.
            starting_money (float, optional): Starting money. Defaults to $10000.00.
            pick_date (str, optional): Deadline to buy/pick stocks (`YYYY-MM-DD`). If omitted, players can buy anytime.
            private_game(bool, optional): Whether the game is private (True).  Defaults to public (False).
            total_picks (int, optional): Amount of stocks each user picks. Defaults to 10.
            exclusive_picks (bool, optional): Whether multiple users can pick the same stock. If enabled, pick date must be on or before start date.
            sell_during_game (bool, optional): Whether users can sell during the game. Defaults to False.
            update_frequency (str, optional): Price-update tag (`alpaca`, `daily`, etc.). Defaults to 'alpaca'.
            
        Returns:
            str: Identifier of the newly created game.
        """
        
        # Date formatting validation
        
        if isinstance(start_date, date): # TODO this might crash idk
            try:
                start_date = start_date.strftime('%Y-%m-%d') # Convert date
            except Exception as e:
                #TODO find valid errors here
                raise e
        
        if isinstance(pick_date, date): # TODO this might crash idk
            try:
                pick_date = pick_date.strftime('%Y-%m-%d') # Convert date
            except Exception as e:
                #TODO find valid errors here
                raise e
            
        if isinstance(end_date, date): # TODO this might crash idk
            try:
                end_date = end_date.strftime('%Y-%m-%d') # Convert date
            except Exception as e:
                #TODO find valid errors here
                raise e
        
        if end_date: # Enddate stuff
            if not self._validate_date(end_date): # It will be a string here
                raise bexc.InvalidDateFormatError('Invalid `end_date` format.')
            if datetime.strptime(start_date, "%Y-%m-%d").date() > datetime.strptime(end_date, "%Y-%m-%d").date():
                raise ValueError('`end_date` must be after `start_date`.')
            
            
        if not self._validate_date(start_date):
            raise bexc.InvalidDateFormatError('Invalid `start_date` format.')
            
        if pick_date and not self._validate_date(pick_date):
            raise bexc.InvalidDateFormatError('Invalid `pick_date` format.')
        #TODO should we check if pick_date is after end_date?  Doesn't cause an issue, just kinda silly
        
        if exclusive_picks: # Draftmode checks
            if not pick_date:
                raise TypeError('`pick_date` required when `exclusive_picks` is enabled.')
            if datetime.strptime(start_date, "%Y-%m-%d").date() < datetime.strptime(pick_date, "%Y-%m-%d").date(): # Date format is already validated
                raise ValueError('`start_date` must be after `pick_date` when `exclusive_picks` is enabled.')
    
        # Misc
        if update_frequency not in get_args(dtv.UpdateFrequency): #TODO can this use dtv.UpdateFrequency?
            raise ValueError(f'Invalid update frequency {update_frequency}')
        if starting_money < 1.0:
            raise ValueError('`starting_money` must be atleast `1.0`.')
        if total_picks < 1:
            raise ValueError('`total_picks` must be atleast `1`.')
        
        game_id = self.generate_alnum_id()

        items = {
            'name': name,
            'game_id': game_id,
            'template_id': template_id,
            'owner_user_id': user_id,
            'start_money': starting_money,
            'pick_count': total_picks,
            'draft_mode': exclusive_picks,
            'pick_date': pick_date,
            'private_game': private_game,
            'allow_selling': sell_during_game,
            'update_frequency': update_frequency.lower() if update_frequency else None, # Make sure its lowercase
            'start_date': start_date,
            'end_date': end_date,  # is this needed?, no but I like it.
            'datetime_created': _iso8601()
            }

        resp = self.sql.insert(table='games', items=items)
        if resp.status != 'success': #TODO errors
            if resp.reason == 'SQLITE_CONSTRAINT_UNIQUE' and str(resp.result).strip() == 'games.name':
                raise bexc.AlreadyExistsError(table='games', duplicate=name, message='Cannot add multiple games with the same name')

            raise Exception(f'Failed to add game.', resp) 
        return game_id
    
    def get_game(self, game_id:int | str)-> dtv.Game: # Its always a Games object, but its being a fucking baby
        """Get a single game by ID

        Args:
            game_id (int): Game ID.

        Returns:
            dict: Game information.
        """
        
        self.logger.debug(f'Getting game: {game_id}')
        tobsi_loop = 0 # Issue originally found by @tobsi on discord
        while tobsi_loop < 4: # Should allow it to fix some issues
            tobsi_loop += 1
            resp = self.sql.get(table='games',filters={'game_id': game_id})
            try:
                return self._single_get(model=dtv.Game, resp=resp)
            except ValidationError as exc: # Something has gone terribly wrong
                self.logger.exception(f'Game exists, but validation failed', exc_info=exc)
                # Reset values back to their defaults #TODO add more
                fixes = {} # Empty dictionary
                if 'update_frequency' in str(exc):
                    self.logger.debug(f'Setting update_frequency to \'daily\' for game: {game_id}')
                    fixes['update_frequency'] = 'daily' 
                
                if 'name' in str(exc):
                    self.logger.debug(f'Shortening name to 35 characters for game: {game_id}')
                    if not isinstance(resp.result, tuple) or not resp.result or not isinstance(resp.result[0], dict):
                        raise ValidationError('Unable to recover invalid game name without a row result.')
                    row = cast(dict[str, Any], resp.result[0])
                    name = re.sub(r'[\(\)\[\]/`\\/{}]', '', str(row['name'])) # Clean the name more
                    if tobsi_loop != 0: # We've been here before, add the game ID to the name
                        fixes['name'] = str(str(game_id) + name)[:35] # name string at 35 characters and get rid of shit.  If it fails, remove an extra character
                    else:
                        fixes['name'] = name[:35] # name string at 35 characters and get rid of shit.  If it fails, remove an extra character
                
                if 'status' in str(exc):
                    self.logger.debug(f'Setting status to \'open\' for game: {game_id}')
                    fixes['status'] = 'open' 
                    
                if 'end_date' in str(exc):
                    self.logger.debug(f'Removing end date for game: {game_id}')
                    fixes['end_date'] = 'NULL' 
                
                if len(fixes) == 0:
                    raise ValidationError(str(exc) + 'Unable to fix automatically') # Throw the same error
                else: # Apply fixes
                    apply = self.sql.update(table='games', filters={'game_id': game_id}, items=fixes)
                    if apply.status !='success': 
                        self.logger.error(f'Fix to game: {game_id} failed.  More info: {apply}')

        raise ValidationError('Failed to recover from a validation error loop.')
    
    def get_many_games(self, name:Optional[str]=None, owner_id:Optional[int]=None, include_public:bool=True, include_private:bool=False, include_open:bool=True, include_active:bool=True, include_ended:bool=False)-> tuple[dtv.Game]: # List all games
        """Get multiple games

        Args:
            name (Optional[str], optional): Filter by name.
            owner_id (Optional[int], optional): Filter by owner ID.
            include_public (bool, optional): Include public games in results. Defaults to True.
            include_private (bool, optional): Include private games in results. Defaults to False.
            include_open (bool, optional): Include open games in results. Defaults to True.
            include_active (bool, optional): Include active games in results. Defaults to True.
            include_ended (bool, optional): Include ended games in results. Defaults to False.

        Returns:
            tuple: Matching games.
        """
           
        query = """WHERE private_game IN ({privacy})
        AND STATUS IN ({statuses})
        """
        
        values =[]
        if name:
            query += 'AND name LIKE ?'
            values.append(name)
        if owner_id:
            query += 'AND owner_user_id = ?'
            values.append(owner_id)
        
        privacy = [] # privacy

        if include_public:
            privacy.append('0')
        if include_private:
            privacy.append('1')
        
        statuses = [] # status
        if include_open:
            statuses.append('"open"')
        if include_active:
            statuses.append('"active"')
        if include_ended:
            statuses.append('"ended"')
        
        repair= 0
        e = ''
        while repair < 2: # Try this twice
            resp = self.sql.get(table='games', filters=(query.format(statuses='' +','.join(statuses), privacy='' +','.join(privacy)), values))
            repair +=1
            try:
                return self._many_get(typeadapter=dtv.Games, resp=resp)
            except ValidationError as e: # Something bad happened
                self.repair_games() # Repair games and loop again
        
        raise Exception('Failed to repair games', e)
        
    def update_game(self, game_id:int | str, owner:Optional[int]=None, name:Optional[str]=None, start_date:Optional[str]=None, end_date:Optional[str]=None, status:Optional[str]=None, starting_money:Optional[float]=None, pick_date:Optional[str]=None, private_game:Optional[bool]=None, total_picks:Optional[int]=None, exclusive_picks:Optional[bool]=None, sell_during_game:Optional[bool]=None, update_frequency:Optional[dtv.UpdateFrequency]=None, aggregate_value:Optional[float]=None, change_dollars:Optional[float]=None, change_percent:Optional[float]=None, leaderboard_message_id:Optional[str]=None, clear_leaderboard_message:bool=False, top_roles_applied:Optional[bool]=None, leaderboard_final_pushed:Optional[bool]=None, clear_end_date:bool=False, clear_pick_date:bool=False):
        """Update an existing game
        
        Args:
            game_id (int | str): Game ID.
            owner (Optional[int], optional): New owner ID. 
            name (Optional[str], optional): New game name.  Maximum 35 chatacters.
            start_date (Optional[str], optional): New start date.  Format: `YYYY-MM-DD`.  Cannot be changed once game has started.
            end_date (Optional[str], optional): New end date.  Format: `YYYY-MM-DD`.
            status (Optional[str], optional): Status ('open', 'active', 'ended').  Once start date has passed, game will become 'active'.  Shouldn't be changed manually.
            starting_money (Optional[float], optional): Starting money.  Cannot be changed once game has started.
            pick_date (Optional[str], optional): Pick date.  Format: `YYYY-MM-DD`.  Cannot be changed once game has started.
            private_game (Optional[bool], optional): Game privacy. 
            total_picks (Optional[int], optional): Total picks.  Cannot be changed once game has started.
            exclusive_picks (Optional[bool], optional): Whether multiple users can pick the same stock.  Cannot be changed once game has started.
            sell_during_game (Optional[bool], optional): Whether users can sell stocks during game.
            update_frequency (Optional[str], optional): Price update frequency ('daily', 'hourly', 'minute', 'realtime'. 
            aggregate_value (float, optional): Total value of all game participants stocks.  Shouldn't be changed manually.
            change_dollars (float, optional): aggregate_value - (starting_money * total participants).  Rounded to two decimal points.
            change_percent (float, optional): change_dollars in percent format.  Rounded to two decimal points.
            leaderboard_message_id (Optional[str], optional): Comma-separated Discord message ids for recurring leaderboard push pages.
            clear_leaderboard_message (bool, optional): Clear stored leaderboard message id.
            top_roles_applied (Optional[bool], optional): Mark recurring auto top-role processing complete.
            leaderboard_final_pushed (Optional[bool], optional): Mark final standings push complete.
            clear_end_date (bool, optional): Remove the optional end date.
            clear_pick_date (bool, optional): Remove the optional pick deadline.
        """

        game = self.get_game(game_id) # Error will be thrown if game can't be found, so anything returned is a game
        if start_date and not self._validate_date(start_date):
            raise bexc.InvalidDateFormatError('Invalid `start_date` format.')
        
        if game.start_date < datetime.today().date():
            if any(value is not None for value in (start_date, starting_money, pick_date, exclusive_picks)) or clear_pick_date:
                raise ValueError('Cannot update `start_date`, `starting_money`, `pick_date`, or `exclusive_picks` once game has started.')
            
        if end_date: # Enddate stuff
            if not self._validate_date(end_date):
                raise bexc.InvalidDateFormatError('Invalid `end_date` format.')
            if game.start_date > datetime.strptime(end_date, "%Y-%m-%d").date():
                raise ValueError('`end_date` must be after `start_date`.')
            
        if pick_date and not self._validate_date(pick_date):
            raise bexc.InvalidDateFormatError('Invalid `pick_date` format.')
        
        if update_frequency and update_frequency not in get_args(dtv.UpdateFrequency):
            raise ValueError(f'Invalid update frequency {update_frequency}')
        if starting_money and starting_money < 1.0:
            raise ValueError('`starting_money` must be atleast `1.0`.')
        if total_picks and total_picks < 1:
            raise ValueError('`total_picks` must be atleast `1`.')
        
        try:
            self._update_single(
                table='games', 
                id_column='game_id', 
                item_id=game_id,
                name=name,
                owner_user_id = owner,
                start_money = starting_money,
                pick_count = total_picks,
                draft_mode = exclusive_picks,
                pick_date = 'NULL' if clear_pick_date else pick_date,
                private_game = private_game,
                allow_selling = sell_during_game,
                status = status,
                update_frequency = update_frequency.lower() if update_frequency else None,
                start_date = start_date,
                end_date = 'NULL' if clear_end_date else end_date,
                aggregate_value = aggregate_value,
                change_dollars = round(change_dollars, 2) if change_dollars is not None else None,
                change_percent = round(change_percent, 2) if change_percent is not None else None,
                leaderboard_message_id = 'NULL' if clear_leaderboard_message else leaderboard_message_id,
                top_roles_applied = int(top_roles_applied) if top_roles_applied is not None else None,
                leaderboard_final_pushed = int(leaderboard_final_pushed) if leaderboard_final_pushed is not None else None,
                last_updated = _iso8601()
            )
        except ValueError as e: # Raised when Constraint check fails
            if 'CHECK constraint failed:' in str(e):
                raise ValueError(str(e).strip('IntegrityError(\'CHECK constraint failed:').strip(')')) # Pass on just the field that failed #TODO regex

    def remove_game(self, game_id:int | str):
        """Remove a game

        Args:
            game_id (int): Game ID.
        """
        
        self._delete_single(table='games', id_column='game_id', item_id=game_id)
    
    def repair_games(self):
        # Repair games in database
        resp = self.sql.get(table='games', columns=['game_id']) # Get ALL games
        if resp.status == 'success': # Found games
            assert isinstance(resp.result, tuple)
            for game in resp.result: # Go through games and try to fix them
                self.get_game(game['game_id']) 
    
    # # GAME TEMPLATE ACTIONS # #
    def add_game_template(self, user_id:int, name:str, start_date:str, create_days_in_advance:int=0, recurring_period:int=1, game_length:int=1, starting_money:float=10000.00, pick_date:Optional[int]=None, private_game:bool=False, total_picks:int=10, exclusive_picks:bool=False, sell_during_game:bool=False, update_frequency:dtv.UpdateFrequency='alpaca') -> int:
        #TODO support basic variables in the game name
        if not start_date or not self._validate_date(start_date):
            raise bexc.InvalidDateFormatError('Invalid `start_date` format.')
        if recurring_period < 1:
            raise ValueError('`recurring_period` must be at least 1.')
        if create_days_in_advance < 0:
            raise ValueError('`create_days_in_advance` cannot be negative.')
        if game_length < 0:
            raise ValueError('`game_length` cannot be negative.')
        if game_length > 0 and game_length > recurring_period:
            raise ValueError(
                '`game_length` cannot be greater than `recurring_period` '
                '(overlapping active games from the same template).'
            )
        if exclusive_picks and pick_date is None:
            raise ValueError('`pick_date` is required when `exclusive_picks` is enabled.')
        if exclusive_picks and pick_date is not None and pick_date < 0:
            raise ValueError(
                '`pick_date` cannot be after the game start when `exclusive_picks` is enabled.'
            )
        if pick_date is not None and (pick_date < -30 or pick_date > 30):
            raise ValueError('`pick_date` must be between -30 and 30 days relative to each game start.')

        items = {
            'template_name': name,
            'game_name': name,
            'owner_user_id': user_id,
            'start_money': starting_money,
            'pick_count': total_picks,
            'draft_mode': exclusive_picks,
            'pick_date': pick_date,
            'private_game': private_game,
            'allow_selling': sell_during_game,
            'update_frequency': update_frequency.lower() if update_frequency else None, # Make sure its lowercase
            'start_date': start_date,
            'game_length': game_length,  # is this needed?, no but I like it.
            'recurring_period': recurring_period,
            'create_days_in_advance': create_days_in_advance,
            'datetime_created': _iso8601()
            }

        existing = self.sql.get(table='game_templates', filters={'game_name': name})
        if existing.status == 'success':
            raise bexc.AlreadyExistsError(
                table='game_templates',
                duplicate=name,
                message='Cannot add multiple templates with the same name',
            )

        resp = self.sql.insert(table='game_templates', items=items)
        if resp.status != 'success': #TODO errors
            if resp.reason == 'SQLITE_CONSTRAINT_UNIQUE' and 'game_templates.game_name' in str(resp.result):
                raise bexc.AlreadyExistsError(
                    table='game_templates',
                    duplicate=name,
                    message='Cannot add multiple templates with the same name',
                )

            raise Exception(f'Failed to add game.', resp)
        # Prefer lastrowid from insert Status when available
        template_id = getattr(resp, 'result', None)
        if isinstance(template_id, int):
            return template_id
        fetched = self.sql.get(table='game_templates', filters={'game_name': name})
        if fetched.status == 'success' and isinstance(fetched.result, tuple) and fetched.result:
            row = cast(dict[str, Any], fetched.result[0])
            return int(row['template_id'])
        raise Exception('Template created but id could not be resolved.', resp)

    def get_game_template(self, template_id:int):
        #TODO docstring
        resp = self.sql.get(table='game_templates',filters={'template_id': int(template_id)})
        return self._single_get(model=dtv.GameTemplate, resp=resp)
    
    def get_many_game_templates(self, status:Optional[dtv.GameTemplateStatus]) -> tuple[dtv.GameTemplate]:
        filters = {
            'status': status
            }
        
        resp = self.sql.get(table='game_templates', filters=filters)
        templates = self._many_get(typeadapter=dtv.GameTemplates, resp=resp)
        return templates
    
    def update_game_template(
        self,
        template_id: int,
        name: Optional[str] = None,
        status: Optional[dtv.GameTemplateStatus] = None,
        create_days_in_advance: Optional[int] = None,
        recurring_period: Optional[int] = None,
        game_length: Optional[int] = None,
        push_leaderboard: Optional[bool] = None,
        leaderboard_channel_id: Optional[str] = None,
        clear_leaderboard_channel: bool = False,
        auto_top_roles: Optional[bool] = None,
        affiliations_enabled: Optional[bool] = None,
    ):
        """Update the mutable scheduling / push fields on a recurring-game template."""
        if (
            name is None
            and status is None
            and create_days_in_advance is None
            and recurring_period is None
            and game_length is None
            and push_leaderboard is None
            and leaderboard_channel_id is None
            and not clear_leaderboard_channel
            and auto_top_roles is None
            and affiliations_enabled is None
        ):
            raise ValueError('At least one template field must be changed.')
        if status is not None and status not in get_args(dtv.GameTemplateStatus):
            raise ValueError(f'Invalid template status {status}')
        if recurring_period is not None and recurring_period < 1:
            raise ValueError('`recurring_period` must be at least 1.')
        if create_days_in_advance is not None and create_days_in_advance < 0:
            raise ValueError('`create_days_in_advance` cannot be negative.')
        if game_length is not None and game_length < 0:
            raise ValueError('`game_length` cannot be negative.')
        channel_value: Optional[str] = 'NULL' if clear_leaderboard_channel else leaderboard_channel_id
        self._update_single(
            table='game_templates',
            id_column='template_id',
            item_id=template_id,
            template_name=name,
            game_name=name,
            status=status,
            create_days_in_advance=create_days_in_advance,
            recurring_period=recurring_period,
            game_length=game_length,
            push_leaderboard=int(push_leaderboard) if push_leaderboard is not None else None,
            leaderboard_channel_id=channel_value,
            auto_top_roles=int(auto_top_roles) if auto_top_roles is not None else None,
            affiliations_enabled=int(affiliations_enabled) if affiliations_enabled is not None else None,
            last_updated=_iso8601(),
        )

    def remove_game_template(self, template_id:int):
        """Delete a recurring-game template.

        Existing games keep running; their ``template_id`` is cleared so the
        row can be removed without FK conflicts.
        """
        self.get_game_template(template_id)  # Raises LookupError if missing
        clear = self.sql.update(
            table='games',
            items={'template_id': 'NULL'},
            filters={'template_id': int(template_id)},
            force=False,
        )
        if clear.status != 'success' and clear.reason not in ('NO ROWS RETURNED', 'NO ROWS EFFECTED'):
            raise Exception(f'Failed to detach games from template {template_id}.', clear)
        resp = self.sql.delete(table='game_templates', filters={'template_id': int(template_id)})
        if resp.status != 'success':
            if resp.reason == 'NO ROWS RETURNED':
                raise bexc.DoesntExistError(
                    table='game_templates',
                    item=template_id,
                    message=f'Template {template_id} does not exist.',
                )
            raise Exception(f'Failed to delete template {template_id}.', resp)
    
    # # STOCK ACTIONS # #
    def add_stock(self, ticker:str, exchange:str, company_name:str):
        """Add a stock

        Args:
            ticker (str): Stock ticker.  Eg: 'MSFT'.
            exchange (str): Exchange stock is listed on.
            company_name (str): Company name.
        """        
        ticker = ticker.upper()
        try:
            self.get_stock(ticker)
        except LookupError:
            pass
        else:
            raise ValueError(f'Stock with ticker {ticker} already exists.')

        items = {
            'ticker': ticker,
            'exchange': exchange,
            'company_name': company_name
            } # I guess not all stocks have a long name?

        resp = self.sql.insert(table='stocks', items=items)
        if resp.status != 'success': 
            if resp.reason == 'SQLITE_CONSTRAINT_UNIQUE' and 'stocks.ticker' in str(resp.result): 
                raise ValueError(f'Stock with ticker {ticker} already exists.')
            else:
                raise Exception(f'Failed to add stock.', resp)

    def update_stock(
        self,
        ticker_or_id: str | int,
        *,
        company_name: Optional[str] = None,
        exchange: Optional[str] = None,
    ) -> None:
        """Update mutable stock metadata (company name / exchange)."""
        stock = self.get_stock(ticker_or_id)
        updates: dict[str, str] = {}
        if company_name is not None:
            updates['company_name'] = company_name
        if exchange is not None:
            updates['exchange'] = exchange
        if not updates:
            return
        self._update_single(table='stocks', id_column='stock_id', item_id=stock.id, **updates)

    def rename_stock_ticker(self, stock_id: int, new_ticker: str) -> None:
        """Update a stock ticker (corporate name change)."""
        self._update_single(
            table='stocks',
            id_column='stock_id',
            item_id=stock_id,
            ticker=to_db_ticker(new_ticker),
        )

    def set_stock_trade_status(self, stock_id: int, trade_status: str) -> None:
        """Set ``trade_status`` (active, delisted, merged)."""
        self._update_single(
            table='stocks',
            id_column='stock_id',
            item_id=stock_id,
            trade_status=trade_status,
        )

    def insert_staged_corporate_action(
        self,
        *,
        alpaca_ca_id: str,
        action_type: str,
        stock_id: int,
        pick_id: Optional[int],
        share_factor: Optional[float],
        payload: str,
        trade_date: str,
        datetime_staged: str,
    ) -> None:
        try:
            self.sql.insert(
                table='staged_corporate_actions',
                items={
                    'alpaca_ca_id': alpaca_ca_id,
                    'action_type': action_type,
                    'stock_id': stock_id,
                    'pick_id': pick_id,
                    'share_factor': share_factor,
                    'payload': payload,
                    'trade_date': trade_date,
                    'datetime_staged': datetime_staged,
                },
            )
        except Exception as exc:
            if 'UNIQUE' in str(exc).upper():
                return
            raise

    def count_staged_corporate_actions(self, trade_date: str) -> int:
        resp = self.sql.get(
            table='staged_corporate_actions',
            filters={'trade_date': trade_date},
        )
        if resp.status != 'success' or not isinstance(resp.result, tuple):
            return 0
        return len(resp.result)

    def get_staged_corporate_actions(self, trade_date: str) -> list[dict]:
        resp = self.sql.get(
            table='staged_corporate_actions',
            filters={'trade_date': trade_date},
        )
        if resp.status != 'success' or not isinstance(resp.result, tuple):
            return []
        return [row for row in resp.result if isinstance(row, dict)]

    def clear_staged_corporate_actions(self, trade_date: str) -> None:
        self.sql.delete(
            table='staged_corporate_actions',
            filters={'trade_date': trade_date},
        )

    def is_corporate_action_applied(self, alpaca_ca_id: str) -> bool:
        resp = self.sql.get(
            table='applied_corporate_actions',
            filters={'alpaca_ca_id': alpaca_ca_id},
        )
        return resp.status == 'success' and bool(resp.result)

    def record_applied_corporate_action(
        self,
        alpaca_ca_id: str,
        action_type: str,
        stock_id: int,
        process_date: str,
    ) -> None:
        self.sql.insert(
            table='applied_corporate_actions',
            items={
                'alpaca_ca_id': alpaca_ca_id,
                'action_type': action_type,
                'stock_id': stock_id,
                'process_date': process_date,
                'datetime_applied': _iso8601(),
            },
        )

    INVALID_TICKER_TTL_DAYS = 7

    def is_ticker_invalid(self, ticker: str) -> bool:
        """Return True when ``ticker`` is cached as invalid and still blocked."""
        db_ticker = to_db_ticker(ticker)
        resp = self.sql.get(table='invalid_stocks', filters={'ticker': db_ticker})
        if resp.status != 'success' or not isinstance(resp.result, tuple) or not resp.result:
            return False
        row = resp.result[0]
        if not isinstance(row, dict):
            return False
        return str(row['expires_at']) > _iso8601()

    def record_invalid_ticker(self, ticker: str) -> None:
        """Cache a ticker as invalid for :data:`INVALID_TICKER_TTL_DAYS` days."""
        db_ticker = to_db_ticker(ticker)
        now = _iso8601()
        expires_at = (
            datetime.now() + timedelta(days=self.INVALID_TICKER_TTL_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        resp = self.sql.get(table='invalid_stocks', filters={'ticker': db_ticker})
        if resp.status == 'success':
            self.sql.update(
                table='invalid_stocks',
                items={'expires_at': expires_at, 'last_checked': now},
                filters={'ticker': db_ticker},
            )
            return
        self.sql.insert(
            table='invalid_stocks',
            items={
                'ticker': db_ticker,
                'expires_at': expires_at,
                'datetime_created': now,
                'last_checked': now,
            },
        )

    def clear_invalid_ticker(self, ticker: str) -> None:
        """Remove a ticker from the invalid cache after a successful lookup."""
        db_ticker = to_db_ticker(ticker)
        self.sql.delete(table='invalid_stocks', filters={'ticker': db_ticker})
    
    def get_stock(self, ticker_or_id:str | int)-> dtv.Stock:
        """Get a stock

        Args:
            ticker_or_id (str | int): Stock ID (int) or ticker (str).

        Returns:
            dict: Stock information.
        """
        if isinstance(ticker_or_id, int):
            filters = {'stock_id': int(ticker_or_id)}
            resp = self.sql.get(table='stocks', filters=filters)
            return self._single_get(model=dtv.Stock, resp=resp)

        # Accept BRK.B / BRK-B / mixed case - DB may store either class-share form.
        raw = str(ticker_or_id).strip().upper()
        candidates = list(dict.fromkeys([raw, to_db_ticker(raw), to_alpaca_symbol(raw)]))
        last_error: Optional[LookupError] = None
        for candidate in candidates:
            resp = self.sql.get(table='stocks', filters={'ticker': candidate})
            try:
                return self._single_get(model=dtv.Stock, resp=resp)
            except LookupError as e:
                last_error = e
        if last_error is not None:
            raise last_error
        raise LookupError(f'Stock not found: {ticker_or_id}')
        
    def get_many_stocks(self, company_name:Optional[str]=None, exchange:Optional[str]=None, tickers_only:bool=False)-> tuple[dtv.Stock]:
        """Get multiple stocks

        Args:
            company_name (Optional[str], optional): Filter by company name.
            exchange (Optional[str], optional): Filter by exchange.
            tickers_only (bool, optional): Only return tickers. Defaults to False.

        Returns:
            tuple: Matching stocks.
        """
        filters = {
            'company_name': company_name,
            'exchange': exchange
            }

        resp = self.sql.get(table='stocks', filters=filters)
        stocks = self._many_get(typeadapter=dtv.Stocks, resp=resp)
        if tickers_only:
            tickers = tuple(stock.ticker for stock in stocks)
            return tickers
        else:
            return stocks
    
    def remove_stock(self, ticker_or_id:str | int): 
        """Remove a stock

        Args:
            ticker_or_id (str | int): Stock ID (int) or ticker (str).
        """
        if isinstance(ticker_or_id, int): # ID
            self._delete_single(table='stocks', id_column='stock_id', item_id=ticker_or_id)
        else: # Ticker
            self._delete_single(table='stocks', id_column='ticker', item_id=ticker_or_id)

    
    # # STOCK PRICE ACTIONS # #
    def add_stock_price(self, ticker_or_id:str | int, price:float, datetime:Optional[str]=None):
        """Add price data for a stock (should be done at close)

        Args:
            ticker_or_id (str | int): Stock ID (int) or ticker (str).
            price (float): Stock price.
            datetime (str, optional): Price datetime Format:`YYYY-MM-DD HH:MM:SS`.  If not provided, current datetime will be used.
        
        Raises:
            LookupError: Invalid Stock ID/Ticker.
        """
        if datetime and not self._validate_date(datetime, '%Y-%m-%d %H:%M:%S'): #Try to validate date
            raise ValueError('Invalid `datetime` format.')
        elif not datetime:
            datetime = _iso8601() # Current datetime as string if date was not provided
            
        stock_id = self.get_stock(ticker_or_id).id #If stock is invalid, an error will be thrown anyway.
        
        items = {
            'stock_id':int(stock_id), 
            'price': float(price), 
            'datetime': str(datetime)
            }
        
        resp = self.sql.insert(table='stock_prices', items=items)
        if resp.status != 'success': #TODO errors
                raise Exception(f'Failed to add stock price for {ticker_or_id}.', resp)
    
    def get_stock_price(self, price_id:int) -> dtv.StockPrice:
        """Get a single stock price by ID.

        Args:
            price_id (int): Price ID.

        Returns:
            dict: Stock price information.
        """
        resp = self.sql.get(table='stock_prices', filters={'price_id': price_id})
        return self._single_get(model=dtv.StockPrice, resp=resp)
    
    def get_many_stock_prices(self, stock_id:Optional[int]=None, datetime:Optional[str]=None)-> tuple[dtv.StockPrice]: # List stock prices, allow some filtering 
        """List stock prices.

        Args:
            stock_id (str, optional): Filter by a stock ID. Defaults to None.
            date (str, optional): Filter by a date.  Formats:  `YYYY-MM-DD HH:MM:SS`, `YYYY-MM-DD`, `YYYY-MM-DD HH:`, etc..  Will use todays DATE if blank.

        Returns:
            list: Stock price info
        """
        if not datetime:
            datetime = _iso8601('date')
        order = {'datetime': 'DESC'}  # Sort by price date (recent first)
        filters = {
            'stock_id': stock_id, 
            ('LIKE', 'datetime'): datetime + '%' # Match like objects #TODO NOT 100% INJECTION SAFE
            } 

        resp = self.sql.get(table='stock_prices',filters=filters, order=order) 
        return self._many_get(typeadapter=dtv.StockPrices, resp=resp)
    
    
    # # STOCK PICK ACTIONS # #
    def add_stock_pick(self, participant_id:int, stock_id:int,): # This is essentially putting in a buy order. End users should not be interacting with this directly    
        """Add a stock pick

        Args:
            participant_id (int): Participant ID.
            stock_id (int): Stock ID.
        
        Raises:
            bexc.NotAllowedError: reason=Not active.  Player status isn't active, so cannot pick stocks
            get_participant > bexc.DoesntExistError: Player not in game
            bexc.NotAllowedError: reason=Past pick_date.  (only possible if a pick date is set).
            bexc.NotAllowedError: reason=Maximum picks reached.  Player already has the maximum amount of stock picks.
            bexc.AlreadyExistsError: Cannot buy the same stock twice.
            Exception: Some other issues ocurred
        """
        
        player = self.get_participant(participant_id)
        if player.status != 'active':
            raise bexc.NotAllowedError(action='add_stock_pick', reason='Not active', message=f'Player status is {player.status}.  Must be active to pick stocks')

        
        game = self.get_game(game_id=player.game_id) 
        if game.pick_date and game.pick_date < datetime.today().date(): # Check that pick date hasn't passed
            raise bexc.NotAllowedError(action='add_stock_pick', reason='Past pick_date', message='Cannot pick stock once pick date has passed')
        
        try:
            picks = self.get_many_stock_picks(participant_id=participant_id, status=['pending_buy', 'owned', 'pending_sell'])
            if len(picks) >= game.pick_count: 
                raise bexc.NotAllowedError(action='add_stock_pick', reason='Maximum picks reached', message='Player already has maximum amount of picks')
            
        except LookupError as e: # Should only be raised if no stocks are present
            pass
            
        items = {
            'participation_id':participant_id,
            'stock_id':stock_id,
            'datetime_created': _iso8601(),
            'last_updated': _iso8601()
            }
        
        resp = self.sql.insert(table='stock_picks', items=items)
        if resp.status != 'success': #TODO errors
            if resp.reason =='SQLITE_CONSTRAINT_UNIQUE':
                raise bexc.AlreadyExistsError(table='add_picks', duplicate={'participant_id': participant_id, 'stock_id':stock_id }, message='Cannot buy the same stock twice')
                
            raise Exception(f'Failed to add pick.', resp)
    
    def get_stock_pick(self, pick_id:int)-> dtv.StockPick:
        """Get a single stock pick

        Args:
            pick_id (int): Pick ID.

        Returns:
            dict: Single stock pick
        """
        try:
            return self._single_get(model=dtv.StockPick, resp=self.sql.get(table='stock_picks', filters={'pick_id': pick_id}))
        except ValidationError as exc:
            self.logger.exception(f'Pick exists, but validation failed', exc_info=exc)
            fixes = {}
            if 'datetime_created' in str(exc):
                self.logger.debug('Missing pick date, setting to today')
                fixes['datetime_created'] = _iso8601() 
            
            if len(fixes) == 0:
                raise ValidationError(str(exc) + 'Unable to fix automatically') # Throw the same error
            else: # Apply fixes
                apply = self.sql.update(table='stock_picks', filters={'pick_id': pick_id}, items=fixes)
                if apply.status !='success':
                    raise Exception(f'Fix to pick: {pick_id} failed. More info: {apply}')

                return self._single_get(
                    model=dtv.StockPick,
                    resp=self.sql.get(table='stock_picks', filters={'pick_id': pick_id}),
                )

    def get_many_stock_picks(self, participant_id:Optional[int]=None, status:Optional[str | list]=None, stock_id:Optional[int]=None, include_tickers:bool=False)-> tuple[dtv.StockPick]: 
        """List stock picks.  Optionally, filter by a status or participant ID
        
        Stocks will be ordered from best performance to worst (by percent)

        Args:
            participant_id (int, optional): Filter by a participant ID.
            status (str | list, optional): Filter by a status(es) ('pending_buy', 'owned', 'pending_sell', 'sold').
            stock_id(int, optional): Filter by stock ID.
            include_tickers(bool, optional):  Include the ticker when getting the stocks
            
        Returns:
            list: List of stock picks
        """
        valid_statuses = ['pending_buy', 'owned', 'pending_sell', 'sold']
        left_str = None
        filters: dict[Any, Any] = {
            'participation_id': participant_id,
            'stock_id': stock_id
            }
        if status: # validate statuses
            statuses = []
            if isinstance(status, str):
                status = [status]
            for st in status: # Chec kthat 
                if st not in valid_statuses:
                    raise ValueError(f'invalid `status` {st}.')
                statuses.append(f'"{st}"') # Add valid statues
            filters.update({('IN', 'status'): "" + ",".join(statuses)})
            
        if include_tickers: # Run a left_join
            left_str = 'LEFT JOIN stocks ON stocks.stock_id = stock_picks.stock_id\n'#IDK if the \n is needed
        
        resp = self.sql.get(table='stock_picks', left_join=left_str, filters=filters, order={'change_percent': 'DESC', 'change_dollars': 'DESC'})
        return self._many_get(typeadapter=dtv.StockPicks, resp=resp)

    def get_active_picks_for_stock(
        self,
        stock_id: int,
        *,
        game_id: Optional[int | str] = None,
    ) -> tuple[dtv.StockPick, ...]:
        """Picks for ``stock_id`` in active games (pending_buy, owned, pending_sell)."""
        import helpers.datatype_validation as dtv

        game_filter = ""
        params: list[Any] = [stock_id]
        if game_id is not None:
            game_filter = "AND game_id = ?"
            params.append(str(game_id))
        query = f"""
        WHERE stock_id = ?
        AND status IN ("pending_buy", "owned", "pending_sell")
        AND participation_id IN (
            SELECT participation_id FROM game_participants
            WHERE status = "active"
            {game_filter}
        )
        """
        try:
            resp = self.sql.get(table="stock_picks", filters=(query, params))
            return tuple(self._many_get(typeadapter=dtv.StockPicks, resp=resp))
        except LookupError:
            return ()

    def update_stock_pick(self, pick_id:int, current_value:Optional[float]=None, shares:Optional[float]=None, start_value:Optional[float]=None, status:Optional[str]=None, change_dollars:Optional[float]=None, change_percent:Optional[float]=None, event_label:Optional[str]=None, stock_id:Optional[int]=None): #Update a single stock pick
        """Update a stock pick

        Args:
            pick_id (int): Pick ID.
            current_value (float): Current value (shares * current stock price)
            shares (Optional[float], optional): Shares.
            start_value (Optional[float], optional): Starting value of pick.
            status (Optional[str], optional): Status ('pending_buy', 'owned', 'pending_sell', 'sold').
            change_dollars (float, optional): current_value - (games.starting_money).  Rounded to two decimal points.
            change_percent (float, optional): change_dollars in percent format.  Rounded to two decimal points.
        """
        
        kwargs: dict = {'last_updated': _iso8601()}
        if shares is not None:
            kwargs['shares'] = shares
        if start_value is not None:
            kwargs['start_value'] = start_value
        if current_value is not None:
            kwargs['current_value'] = current_value
        if status is not None:
            kwargs['status'] = status
        if change_dollars is not None:
            kwargs['change_dollars'] = round(change_dollars, 2)
        if change_percent is not None:
            kwargs['change_percent'] = round(change_percent, 2)
        if event_label is not None:
            kwargs['event_label'] = event_label
        if stock_id is not None:
            kwargs['stock_id'] = stock_id
        self._update_single(
            table='stock_picks',
            id_column='pick_id',
            item_id=pick_id,
            **kwargs,
        )

    def remove_stock_pick(self, pick_id:int):
        """Remove a stock pick
        
        Will not prevent owned stocks from being removed.

        Args:
            pick_id (int): Pick ID.
        """
        
        self._delete_single(table="stock_picks", id_column='pick_id', item_id=pick_id)
        

    # # GAME PARTICIPATION ACTIONS # #
    def add_participant(self, user_id:int, game_id:int | str, force_active: bool = False):
        """Add a game participant
        
        Cannot add participant to a game that has already started.
                
        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            force_active (bool): Join as active even for private games (e.g. owner invite).
        
        Raises:
            ValueError('`pick_date` has passed.'): The pick date for the game has already passed, so the player cannot be added.
            ValueError('Already in game.'): The participant ID is already in the game.
        """
        game = self.get_game(game_id=game_id)
        if game.start_date < datetime.today().date() and (game.pick_date and game.pick_date < datetime.today().date()):
            raise ValueError('`pick_date` has passed.')
        if force_active or not (game.private_game and game.owner_id != user_id):
            status = 'active'
        else:
            status = 'pending'
        items = {
            'user_id':user_id, 
            'game_id':game_id,
            'status': status,
            'datetime_joined': _iso8601()
            }
    
        resp = self.sql.insert(table='game_participants', items=items)
        if resp.status != 'success': #TODO errors
            if resp.reason == 'SQLITE_CONSTRAINT_UNIQUE' and 'game_participants.user_id, game_participants.game_id' in str(resp.result):
                raise ValueError('Already in game.')
            
            raise Exception(f'Unexpected error while adding player.', resp)
        
    def get_participant(self, participant_id:int)-> dtv.GameParticipant: # Get game player info
        """Get a game participant's information

        Args:
            participant_id (int): Participant ID.

        Returns:
            dict: Participant information.
        """

        resp = self.sql.get(table='game_participants', filters={'participation_id': participant_id})
        try:
            return self._single_get(model=dtv.GameParticipant, resp=resp)
        except LookupError:
            raise bexc.DoesntExistError(table='game_participants', item=participant_id, message='Player not in game')

    def get_many_participants(self, game_id:Optional[int | str]=None, user_id:Optional[int]=None, status:Optional[str]=None, sort_by_value:bool=False)-> tuple[dtv.GameParticipant]:
        """Get multiple participants

        Args:
            game_id (Optional[int], optional): Filter by game ID. 
            user_id (Optional[int], optional): Filter by user ID.
            status (Optional[str], optional): Filter by status ('pending', 'active', 'inactive').
            sort_by_value (bool, optional): Whether results should be sorted by value.

        Returns:
            tuple: Matching participants.
        """
        if status and status not in ['pending', 'active', 'inactive']: # TODO support multiple statuses
            raise ValueError('Invalid status!')
        
        filters = {
            'user_id': user_id,
            'game_id': game_id,
            'status':status
            }
        
        order={'game_id': 'DESC'}
        if sort_by_value:
            order['current_value'] = 'DESC'
        
        resp = self.sql.get(table='game_participants', order=order, filters=filters, ) 
        return self._many_get(typeadapter=dtv.GameParticipants, resp=resp)
    
    def update_participant(self, participant_id:int, status:Optional[str]=None, current_value:Optional[float]=None, change_dollars:Optional[float]=None, change_percent:Optional[float]=None, days_in_first:Optional[int]=None, affiliation:Optional[str]=None, clear_affiliation:bool=False):
        """Update a game participant

        Args:
            participant_id (int): Participant ID.
            status (Optional[str], optional): Status ('pending', 'active', 'inactive').
            current_value (Optional[float], optional): Current portfolio value.
            change_dollars (float, optional): current_value - (starting_money / total_picks).  Rounded to two decimal points.
            change_percent (float, optional): change_dollars in percent format.  Rounded to two decimal points.
            days_in_first (Optional[int], optional): Days ended ranked #1 after NYSE close.
            affiliation (Optional[str], optional): Recurring hedge-fund team key.
            clear_affiliation (bool, optional): Set affiliation to NULL (Independent).
        """
        affiliation_value = 'NULL' if clear_affiliation else affiliation
        self._update_single(
            table='game_participants',
            id_column='participation_id',
            item_id=participant_id,
            status = status,
            current_value = current_value,
            change_dollars = round(change_dollars, 2) if change_dollars is not None else None,
            change_percent = round(change_percent, 2) if change_percent is not None else None,
            days_in_first = days_in_first,
            affiliation = affiliation_value,
            last_updated = _iso8601()
            )
        
    def set_participant_affiliation(
        self,
        user_id: int,
        game_id: int | str,
        affiliation: str | None,
    ) -> dtv.GameParticipant:
        """Assign a hedge-fund affiliation for a recurring-game participant."""
        from helpers.affiliations import is_affiliations_enabled, normalize_affiliation

        game = self.get_game(game_id)
        if game.template_id is None:
            raise ValueError('Affiliations are only available in recurring games.')
        if not is_affiliations_enabled(self, game):
            raise ValueError('Affiliations are not enabled for this game.')
        canonical = normalize_affiliation(affiliation)
        try:
            participant = self.get_many_participants(game_id=game_id, user_id=user_id)[0]
        except LookupError as exc:
            raise LookupError('Player not in game.') from exc
        if participant.status != 'active':
            raise ValueError('Only active players can choose an affiliation.')
        existing = getattr(participant, 'affiliation', None)
        if existing is not None and existing != canonical:
            raise ValueError('Affiliation cannot be changed once chosen.')
        if existing == canonical:
            return participant
        if canonical is None:
            self.update_participant(participant.id, clear_affiliation=True)
        else:
            self.update_participant(participant.id, affiliation=canonical)
        return self.get_participant(participant.id)
        
    def remove_participant(self, participant_id:int):
        """Remove a game participant

        Args:
            participant_id (int): Participant ID.
        """
        
        # is this participant_id or participation_id?
        self._delete_single(table='game_participants', id_column='participation_id', item_id=participant_id)

    # # GAME INVITES # #
    def upsert_game_invite(
        self,
        game_id: str,
        user_id: int,
        inviter_id: int,
        *,
        dm_channel_id: int | None = None,
        dm_message_id: int | None = None,
    ) -> tuple[dtv.GameInvite, dtv.GameInvite | None]:
        """Create or refresh a pending invite. Returns ``(invite, previous_if_any)``."""
        previous: dtv.GameInvite | None = None
        try:
            previous = self.get_game_invite(game_id=game_id, user_id=user_id)
        except LookupError:
            pass

        now = _iso8601()
        items = {
            'inviter_id': inviter_id,
            'dm_channel_id': dm_channel_id,
            'dm_message_id': dm_message_id,
            'status': 'pending',
            'datetime_updated': now,
        }
        if previous:
            resp = self.sql.update(
                table='game_invites',
                filters={'game_id': game_id, 'user_id': user_id},
                items=items,
            )
            if resp.status != 'success':
                raise Exception('Failed to refresh game invite.', resp)
        else:
            resp = self.sql.insert(
                table='game_invites',
                items={
                    'game_id': game_id,
                    'user_id': user_id,
                    **items,
                    'datetime_created': now,
                },
            )
            if resp.status != 'success':
                raise Exception('Failed to create game invite.', resp)

        return self.get_game_invite(game_id=game_id, user_id=user_id), previous

    def get_game_invite(self, game_id: str, user_id: int) -> dtv.GameInvite:
        resp = self.sql.get(
            table='game_invites',
            filters={'game_id': game_id, 'user_id': user_id},
        )
        return self._single_get(model=dtv.GameInvite, resp=resp)

    def get_pending_invites_for_user(self, user_id: int) -> tuple[dtv.GameInvite, ...]:
        resp = self.sql.get(
            table='game_invites',
            filters={'user_id': user_id, 'status': 'pending'},
        )
        return self._many_get(typeadapter=dtv.GameInvites, resp=resp)

    def set_game_invite_status(self, game_id: str, user_id: int, status: str) -> None:
        resp = self.sql.update(
            table='game_invites',
            filters={'game_id': game_id, 'user_id': user_id},
            items={'status': status, 'datetime_updated': _iso8601()},
        )
        if resp.status != 'success':
            raise LookupError('Game invite not found.')

    # # TEMPLATE ROLE HOLDERS (recurring auto top-3) # #
    def get_template_role_holders(self, template_id: int) -> tuple[dtv.TemplateRoleHolder, ...]:
        resp = self.sql.get(
            table='template_role_holders',
            filters={'template_id': int(template_id)},
            order={'rank': 'ASC'},
        )
        try:
            return self._many_get(typeadapter=dtv.TemplateRoleHolders, resp=resp)
        except LookupError:
            return ()

    def clear_template_role_holders(self, template_id: int) -> None:
        resp = self.sql.delete(
            table='template_role_holders',
            filters={'template_id': int(template_id)},
            force=True,
        )
        if resp.status != 'success' and resp.reason not in ('NO ROWS RETURNED', 'NO ROWS EFFECTED'):
            raise Exception(f'Failed to clear role holders for template {template_id}.', resp)

    def replace_template_role_holders(
        self,
        template_id: int,
        *,
        game_id: str,
        ranked_user_ids: list[int],
    ) -> None:
        """Replace all holder rows for a template with up to three ranked users."""
        self.clear_template_role_holders(template_id)
        now = _iso8601()
        for rank, user_id in enumerate(ranked_user_ids[:3], start=1):
            resp = self.sql.insert(
                table='template_role_holders',
                items={
                    'template_id': int(template_id),
                    'rank': rank,
                    'user_id': int(user_id),
                    'game_id': str(game_id),
                    'datetime_awarded': now,
                },
            )
            if resp.status != 'success':
                raise Exception(
                    f'Failed to store role holder template={template_id} rank={rank}.',
                    resp,
                )

    def get_games_pending_top_roles(self) -> tuple[dtv.Game, ...]:
        """Ended recurring games not yet processed for auto top roles."""
        try:
            games = self.get_many_games(
                include_public=True,
                include_private=True,
                include_open=False,
                include_active=False,
                include_ended=True,
            )
        except LookupError:
            return ()
        return tuple(
            game
            for game in games
            if game.template_id is not None and not game.top_roles_applied
        )

    def get_games_pending_final_push(self) -> tuple[dtv.Game, ...]:
        """Ended recurring games not yet sent a final standings push."""
        try:
            games = self.get_many_games(
                include_public=True,
                include_private=True,
                include_open=False,
                include_active=False,
                include_ended=True,
            )
        except LookupError:
            return ()
        return tuple(
            game
            for game in games
            if game.template_id is not None and not game.leaderboard_final_pushed
        )


        
  
class GameLogic: # Might move some of the control/running actions here
    def __init__(self, db_name:str, market_open_est:str='09:30', market_close_est:str='16:00'):
        """GameLogic class
        
        Handles game logic like updating stock prices, etc.
        
        Args:
            db_name (str): Database name.
        """

        create_db(db_name) # Try to create DB
        self.logger = logging.getLogger('StockGameLogic')
        self.be = Backend(db_name)
        self.market_open_est = datetime.strptime(market_open_est,"%H:%M")
        self.market_close_est = datetime.strptime(market_close_est,"%H:%M")
        self.est_offset = self._market_time_offset()
        self.alpaca = AlpacaMarketData()
        # When set (e.g. Discord bot user id), spawned recurring games use this
        # owner so `/game-list owner:@Bot` can filter to recurring series.
        # Defaults to each template's owner_id when unset (tests / non-Discord).
        self.recurring_game_owner_id: Optional[int] = None
        self._latest_prices: dict[str, float] = {}
        if not self.alpaca.configured:
            self.logger.warning(
                'Alpaca credentials missing; stock price updates will fail until '
                'ALPACA_API_KEY and ALPACA_SECRET_KEY are set in .env'
            )
    
    def _is_market_hours(self): # Only considers hours
        """Check whether the US equity market is open.

        Prefers Alpaca's clock; falls back to EST open/close window.

        Returns:
            bool: True when within market hours.
        """
        alpaca_open = self.alpaca.is_market_open()
        if alpaca_open is not None:
            return alpaca_open

        # Fallback if Alpaca clock is unavailable
        the_time = datetime.strftime(datetime.now() + timedelta(hours=self.est_offset), "%H:%M")
        if datetime.strptime(the_time,"%H:%M") > self.market_open_est and self.market_close_est > datetime.strptime(the_time,"%H:%M"):
            return True
        else:
            return False

    def _market_time_offset(self): # If your timezone is EST then none of this is needed and I'll feel real dumb #TODO this is so awful oh my god
        """Get the market offset hours from current timezone.  Add or subtract this from times in DB

        Returns:
            float: Offset in hours.
        """
        local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
        market_offset = datetime.now(pytz.utc).astimezone(
            pytz.timezone('America/New_York')
        ).utcoffset() or timedelta(0)
        return (market_offset - local_offset).total_seconds() / 3600
    
    def _today_et(self) -> date:
        """Calendar date in America/New_York (US market calendar day)."""
        return datetime.now(pytz.timezone('America/New_York')).date()

    def _next_recurring_start(
        self,
        anchor: date,
        recurring_period: int,
        after: Optional[date] = None,
    ) -> date:
        """Next start date on a schedule anchored to ``anchor``.

        The first occurrence is ``anchor``. Later ones are
        ``anchor + n * recurring_period`` months. Adding months from the
        original anchor (not the previous occurrence) keeps the intended
        day-of-month when possible - e.g. the 30th each month, clamped to
        Feb 28/29, then back to the 30th in March.
        """
        if after is None:
            return anchor
        months = 0
        next_start = anchor
        while next_start <= after:
            months += recurring_period
            next_start = anchor + relativedelta(months=months)
        return next_start

    def _unique_recurring_game_name(self, base_name: str) -> str:
        """Return ``base_name``, or ``base_name #n`` if that name is taken."""
        candidate = base_name[:35]
        attempt = 1
        while attempt <= 100:
            try:
                existing = self.be.sql.get(table='games', filters={'name': candidate})
                if existing.status != 'success':
                    return candidate
            except Exception:
                return candidate
            attempt += 1
            suffix = f' #{attempt}'
            candidate = f'{base_name[: max(1, 35 - len(suffix))]}{suffix}'
        raise bexc.AlreadyExistsError(
            table='games',
            duplicate=base_name,
            message=f'Could not find a unique game name for {base_name!r}',
        )

    def recurring_games(self):
        """Create each enabled template's due games (catch up all overdue ones).

        ``template.start_date`` is the first game's start. Later games follow
        every ``recurring_period`` months from that anchor date.
        Disabled templates are skipped (stop = no new games; existing continue).
        """
        today = self._today_et()
        try:
            templates = self.be.get_many_game_templates(status='enabled')
        except LookupError:
            return

        for template in templates:
            try:
                while True:
                    response = self.be.sql.get(
                        table='games',
                        columns=['start_date'],
                        filters={'template_id': template.id},
                        order={'start_date': 'DESC'},
                    )
                    latest_start: Optional[date] = None
                    if response.status == 'success':
                        assert isinstance(response.result, tuple)
                        latest_start = datetime.strptime(
                            response.result[0]['start_date'], '%Y-%m-%d'
                        ).date()
                    next_start_date = self._next_recurring_start(
                        anchor=template.start_date,
                        recurring_period=template.recurring_period,
                        after=latest_start,
                    )

                    due_date = next_start_date - timedelta(days=template.create_days_in_advance)
                    if due_date > today:
                        break

                    game_name = self._unique_recurring_game_name(
                        f"{template.name} {next_start_date.strftime('%b %Y')}"
                    )
                    pick_date = None
                    if template.pick_date is not None:
                        pick_date = next_start_date - timedelta(days=template.pick_date)
                    end_date = None
                    if template.game_length:
                        end_date = next_start_date + relativedelta(
                            months=template.game_length, days=-1
                        )

                    owner_id = (
                        self.recurring_game_owner_id
                        if self.recurring_game_owner_id is not None
                        else template.owner_id
                    )
                    self.be.add_game(
                        user_id=owner_id,
                        name=game_name,
                        start_date=next_start_date,
                        end_date=end_date,
                        starting_money=template.start_money,
                        pick_date=pick_date,
                        private_game=template.private_game,
                        total_picks=template.pick_count,
                        exclusive_picks=template.draft_mode,
                        sell_during_game=template.allow_selling,
                        update_frequency=template.update_frequency,
                        template_id=template.id,
                    )
                    self.logger.info(
                        'Created recurring game %r for template %s starting %s',
                        game_name,
                        template.id,
                        next_start_date,
                    )
            except Exception:
                self.logger.exception(
                    'Failed while creating recurring games for template %s (%s)',
                    template.id,
                    template.name,
                )
                continue
    
    def update_game_statuses(self, game_id:Optional[int | str]=None):
        """Update game statuses
        
        Sets games that have started to 'active' and games that have ended to 'ended'

        Args:
            game_id (Optional[int], optional): Game ID.  If blank, all games will be checked.
        """
        today = self._today_et()
        
        try:
            if game_id:
                games = [self.be.get_game(game_id=game_id)]
            else:
                games = self.be.get_many_games(include_private=True) # Get all games
        except LookupError:
            return # No games

        for game in games: #TODO add log here
            
            # Start and end games
            if game.status == 'open' and game.start_date <= today: # Set games to active
                self.be.update_game(game_id=game.id, status='active')
            if game.status == 'active' and game.end_date and game.end_date < today: #Game has ended
                self.be.update_game(game_id=game.id, status='ended')

    def _is_weekday_et(self) -> bool:
        from helpers.market_schedule import is_weekday_et
        return is_weekday_et()

    def _should_poll_prices(self, force: bool, kind: Optional[str] = None) -> bool:
        """Whether scheduled run should call Alpaca for prices."""
        if force:
            return True
        if not self._is_weekday_et():
            return False
        if kind == 'pre_open':
            return False
        if kind == 'post_close':
            return True
        if kind == 'market' or kind is None:
            return self._is_market_hours()
        return False

    def update_stock_prices(
        self,
        game_id: Optional[int | str] = None,
        force: bool = False,
        *,
        kind: Optional[str] = None,
        split_tickers: Optional[set[str]] = None,
    ):
        """Fetch and store latest prices for every equity ticker in the database.

        Uses Alpaca IEX snapshots in rate-limited batches. Crypto is not included.
        `game_id` is accepted for API compatibility with `update_all` but prices
        are refreshed for all stocks in the DB.

        Args:
            game_id (Optional[int], optional): Unused; all DB tickers are updated.
            force (bool, optional): Reserved for callers; prices always refresh when invoked.
        """
        #TODO Skip holidays
        #TODO allow after hours data to be added here as long as its tagged?
        _ = game_id

        if not self._should_poll_prices(force, kind):
            self.logger.info(
                'Skipping Alpaca price poll (force=%s kind=%s)',
                force,
                kind,
            )
            return

        try:
            stocks = self.be.get_many_stocks()
        except LookupError:
            return  # No stocks

        tickers = [
            stock.ticker
            for stock in stocks
            if stock.ticker and getattr(stock, 'trade_status', 'active') == 'active'
        ]
        if not tickers:
            return

        trade_date = self._today_et()
        split_set = {t.upper() for t in (split_tickers or set())}

        try:
            prices = self.alpaca.get_latest_prices(
                tickers,
                split_tickers=split_set,
                trade_date=trade_date if split_set else None,
            )
        except Exception as e:
            self.logger.exception('Alpaca price fetch failed', exc_info=e)
            return

        self._latest_prices = prices

        requested = {t.upper() for t in tickers}
        received = {t.upper() for t in prices}
        missing_tickers = sorted(requested - received)
        if missing_tickers:
            self.logger.error(
                'Price update dropped %s/%s tickers after Alpaca retries: %s',
                len(missing_tickers),
                len(requested),
                ', '.join(missing_tickers[:50]) + ('...' if len(missing_tickers) > 50 else ''),
            )

        # Floor to the minute so repeated polls in the same minute don't collide
        # on UNIQUE(stock_id, datetime). 15-minute schedule still fits this.
        price_dt = datetime.now().strftime("%Y-%m-%d %H:%M:00")
        updated = 0
        write_failures: list[str] = []
        for ticker, price in prices.items():
            try:
                self.be.add_stock_price(ticker_or_id=ticker, price=price, datetime=price_dt)
                updated += 1
            except Exception as e:
                reason = ''
                if len(e.args) > 1 and hasattr(e.args[1], 'reason'):
                    reason = str(e.args[1].reason)
                blob = f'{e} {reason}'.upper()
                if 'UNIQUE' in blob or 'CONSTRAINT' in blob:
                    self.logger.debug('Price already stored for %s at %s', ticker, price_dt)
                    updated += 1  # already present this minute - not a drop
                else:
                    write_failures.append(ticker)
                    self.logger.exception('Failed to update price for %s', ticker, exc_info=e)

        if write_failures:
            self.logger.error(
                'Failed to persist prices for %s ticker(s): %s',
                len(write_failures),
                ', '.join(write_failures[:50]),
            )

        self.logger.info(
            'Alpaca price update: %s/%s tickers priced (%s missing from feed, %s write failures) at %s',
            updated,
            len(tickers),
            len(missing_tickers),
            len(write_failures),
            price_dt,
        )
    
    def update_stock_picks(self, game_id:Optional[int | str]=None, force:bool=False) -> None:
        """Update all owned and pending stock picks with current prices
        
        - Validates game type of daily, but nothing else for now
        - Adds pending_buy stock picks for users (depending on time)
        - Update owned stock pick values

        Args:
            game_id (Optional[int], optional): Game ID.  If blank, all games will be checked/run
            force (bool, optional): Skip market-hours and 8-hour throttle checks.
        """
        
        try:        
            if game_id:
                self.logger.debug(f'Updating stock picks for single game: {game_id}')
                games = [self.be.get_game(game_id=game_id)] # TODO flag that the checked game specifically did not update
            else:
                self.logger.debug(f'Updating stock picks for games')
                games = self.be.get_many_games(include_open=False, include_active=True, include_private=True) # Only active games
            
        except LookupError as e:
            self.logger.exception(f'Failed to update stock picks', exc_info=e)
            return # No games
        
        for game in games:
            if not force and game.update_frequency == 'daily' and self._is_market_hours():
                self.logger.info(f'Not updating stock picks for game: {game.id} because update_frequency is daily and market is still open')
                continue # daily game, currently in market hours, don't run
            self.logger.debug(f'Updating stock picks for game: {game.id}')
            pending_and_owned_query = """
            WHERE status IN ("pending_buy", "owned", "pending_sell")
            AND participation_id IN (SELECT participation_id
                FROM game_participants
                WHERE status = "active"
                AND game_id = ?
                )
            """ #TODO instead of setting games to active, just use start and end date?
            try:
                resp = self.be.sql.get(table='stock_picks', filters=(pending_and_owned_query, [game.id]))
                picks:tuple[dtv.StockPick, ...] = self.be._many_get(typeadapter=dtv.StockPicks, resp=resp)
                pass
            except LookupError:
                self.logger.debug(f'No stock picks to update for game: {game.id}')
                continue # No picks
            
            for pick in picks:
                assert isinstance(pick.id, int)
                assert isinstance(pick.stock_id, int)
                try:
                    stock = self.be.get_stock(pick.stock_id)
                except LookupError:
                    continue
                trade_status = getattr(stock, 'trade_status', 'active')
                if trade_status in ('delisted', 'merged') and pick.status == 'owned':
                    continue

                if pick.status == 'pending_buy' and not force and not self._is_market_hours():
                    continue

                if not force and game.update_frequency == 'daily' and pick.status == 'owned' and pick.last_updated and datetime.strptime(str(pick.last_updated), "%Y-%m-%d %H:%M:%S") + timedelta(hours=8) > datetime.now():
                    self.logger.debug(f'Skipping stock pick: {pick.id} in game: {game_id} because update_frequency is daily, and it was last updated less than 8 hours ago')
                    continue # Skip picks with daily update frequency that have been updated in the last 12 hours

                price_value: Optional[float] = self._latest_prices.get(stock.ticker.upper())
                if price_value is None:
                    try:
                        price_row = self.be.get_many_stock_prices(stock_id=int(pick.stock_id), datetime=_iso8601('date'))[0]
                        price_value = float(price_row.price)
                    except LookupError as e:
                        self.logger.debug('No price for pick %s (%s): %s', pick.id, stock.ticker, e)
                        continue

                class _PriceRow:
                    def __init__(self, p: float):
                        self.price = p
                price = _PriceRow(price_value)
                
                #TODO check datetime here and decide if price should be used
                buying_power = None
                shares = None
                start_value = None
                status = None
                
                if pick.status == 'pending_buy':
                    buying_power = float(game.start_money / game.pick_count) # Amount available to buy this stock (starting money divided by picks)
                    if buying_power <= 0:
                        self.logger.warning(
                            'Skipping pending buy pick %s in game %s: non-positive per-pick allocation',
                            pick.id,
                            game.id,
                        )
                        continue
                    shares = buying_power / price.price# Total shares owned
                    start_value = current_value = round(float(buying_power), 2)
                    if start_value <= 0:
                        start_value = current_value = round(float(buying_power), 4)
                    if start_value <= 0:
                        self.logger.warning(
                            'Skipping pending buy pick %s in game %s: per-pick allocation %s rounds to zero',
                            pick.id,
                            game.id,
                            buying_power,
                        )
                        continue
                    dollar_change = 0
                    percent_change = 0
                    status = 'owned'
                
                else: # Stock is owned or awaiting sale
                    assert isinstance(pick.shares, float) # Owned stocks would have to have this
                    assert isinstance(pick.start_value, float) # Owned stocks would have to have this
                    current_value = float(pick.shares * price.price)
                    dollar_change = current_value - pick.start_value
                    if pick.start_value:
                        percent_change = (dollar_change / pick.start_value) * 100
                    else:
                        self.logger.warning(
                            'Pick %s in game %s has zero start_value; reporting 0%% change',
                            pick.id,
                            game.id,
                        )
                        percent_change = 0.0
                    if pick.status == 'pending_sell':
                        status = 'sold'
                self.be.update_stock_pick(pick_id=pick.id,shares=shares, start_value=start_value, current_value=current_value, status=status, change_dollars=dollar_change, change_percent=percent_change) # Update

    def update_participants_and_games(self, game_id:Optional[int | str]=None):
        """Update game participant and game information
        
        - Participant portfolio value
        - Game Aggregate value

        Args:
            game_id (Optional[int], optional): Game ID.  If blank, all active games will be updated.
        """
        try:        
            if game_id:
                games = [self.be.get_game(game_id=game_id)] # TODO flag that the checked game specifically did not update
            else:
                games = self.be.get_many_games(include_open=False, include_active=True) # Only active games
                
        except LookupError:
            return # No games
        for game in games:
            aggr_val = 0
            if game.status != 'active':
                continue
            try:
                players = self.be.get_many_participants(game_id=game.id, status='active')
            except LookupError:
                continue # No players
                
            for player in players:
                portfolio_value = 0.0
                try:
                    picks = self.be.get_many_stock_picks(participant_id=player.id, status=['owned', 'pending_buy', 'pending_sell', 'sold'])
                except LookupError:
                    picks = []  # No picks, all cash uninvested

                allocation = game.start_money / game.pick_count
                active_picks = [pick for pick in picks if pick.status != 'sold']
                for pick in active_picks:
                    if pick.status == 'pending_buy':
                        portfolio_value += allocation
                    elif pick.current_value is not None:
                        portfolio_value += pick.current_value
                portfolio_value += sum(pick.change_dollars or 0 for pick in picks if pick.status == 'sold')

                active_count = len(active_picks)
                invested_cash = (game.start_money / game.pick_count) * active_count
                uninvested_cash = game.start_money - invested_cash
                portfolio_value += uninvested_cash

                dollar_change = portfolio_value - game.start_money
                percent_change = (dollar_change / game.start_money) * 100
                self.be.update_participant(participant_id=player.id, current_value=portfolio_value, change_dollars=dollar_change, change_percent=percent_change)
                aggr_val += portfolio_value
            
            game_dollar_change = aggr_val - (game.start_money * len(players))
            game_percent_change =  (game_dollar_change / (game.start_money * len(players))) * 100 
            self.be.update_game(game_id=game.id, aggregate_value=aggr_val, change_dollars=game_dollar_change, change_percent=game_percent_change)

    def record_days_in_first(self, game_id: Optional[int | str] = None) -> None:
        """Award +1 ``days_in_first`` to each active game's #1 after NYSE close (idempotent per trade date)."""
        if self._is_market_hours():
            return
        trade_date = self._today_et()
        # Skip weekend calendar days (no regular session to close).
        if trade_date.weekday() >= 5:
            return
        trade_date_str = trade_date.isoformat()
        try:
            if game_id:
                games = [self.be.get_game(game_id=game_id)]
            else:
                games = list(self.be.get_many_games(include_open=False, include_active=True))
        except LookupError:
            return

        for game in games:
            if game.status != 'active':
                continue
            existing = self.be.sql.get(
                table='leaderboard_day_snapshots',
                filters={'game_id': str(game.id), 'trade_date': trade_date_str},
            )
            if existing.status == 'success':
                continue
            try:
                players = self.be.get_many_participants(game_id=game.id, status='active', sort_by_value=True)
            except LookupError:
                continue
            if not players:
                continue
            leader = players[0]
            new_days = int(getattr(leader, 'days_in_first', 0) or 0) + 1
            self.be.update_participant(participant_id=leader.id, days_in_first=new_days)
            snap = self.be.sql.insert(
                table='leaderboard_day_snapshots',
                items={
                    'game_id': str(game.id),
                    'trade_date': trade_date_str,
                    'first_user_id': int(leader.user_id),
                    'datetime_created': _iso8601(),
                },
            )
            if snap.status != 'success':
                # Race / duplicate: leave days_in_first as-is if insert lost the race.
                self.logger.debug(
                    'days_in_first snapshot insert skipped for game %s date %s: %s',
                    game.id,
                    trade_date_str,
                    snap.reason,
                )

    def update_all(
        self,
        game_id: Optional[int | str] = None,
        force: bool = False,
        *,
        kind: Optional[str] = None,
        apply_corporate_actions: bool = False,
    ):
        """Run all update commands/logic for games

        Args:
            game_id: Game ID. If blank, all active games will be updated.
            force: Force update games that may not be updated due to frequency.
            kind: Scheduled window — ``pre_open``, ``market``, ``post_close``.
            apply_corporate_actions: Apply staged CA at market open.
        """
        import time as _time
        from helpers import corporate_actions as ca

        start = _time.perf_counter()
        trade_date = self._today_et()
        split_tickers: set[str] = set()

        maybe_daily_backup(self.be.sql.db)
        maybe_hourly_backup(self.be.sql.db)

        if kind == 'pre_open' and self._is_weekday_et():
            ca.stage_corporate_actions(
                self.be, self.alpaca, trade_date, force_if_empty=True
            )

        if game_id is None:
            self.recurring_games()
        self.update_game_statuses(game_id=game_id)

        prices: dict[str, float] = {}
        if apply_corporate_actions and self._is_weekday_et():
            pre = ca.apply_staged_corporate_actions(
                self.be, self, trade_date, prices, phase='pre_price'
            )
            split_tickers = pre.split_tickers

        self.update_stock_prices(
            game_id=game_id,
            force=force,
            kind=kind,
            split_tickers=split_tickers,
        )
        prices = dict(self._latest_prices)

        if apply_corporate_actions and self._is_weekday_et():
            ca.apply_staged_corporate_actions(
                self.be, self, trade_date, prices, phase='post_price'
            )

        self.update_stock_picks(game_id=game_id, force=force)
        self.update_participants_and_games(game_id=game_id)
        self.record_days_in_first(game_id=game_id)

        elapsed = _time.perf_counter() - start
        self.logger.info(
            'update_all completed in %.2fs (game_id=%s force=%s kind=%s apply_ca=%s)',
            elapsed,
            game_id,
            force,
            kind,
            apply_corporate_actions,
        )
            
    def find_stock(self, ticker:str) -> str: 
        """Find and add a US equity to the database via Alpaca market data.

        Checks the local ``stocks`` table first, then a short-lived
        ``invalid_stocks`` cache, and only then calls Alpaca. Unknown symbols
        are cached as invalid for seven days; transient API failures are not.

        Args:
            ticker (str): Stock ticker.  Eg: 'MSFT' or 'BRK.B'.

        Returns:
            str: Canonical ticker as stored in the database (e.g. ``BRK-B``).
            
        Raises:
            ValueError: Unable to find stock
            RuntimeError: Stock lookup temporarily unavailable
        """        
        db_ticker = to_db_ticker(ticker)
        alpaca_ticker = to_alpaca_symbol(ticker)

        for candidate in dict.fromkeys([db_ticker, alpaca_ticker]):
            try:
                existing = self.be.get_stock(ticker_or_id=candidate)
                self._ensure_company_name(existing)
                return existing.ticker
            except LookupError:
                pass

        if self.be.is_ticker_invalid(db_ticker):
            raise ValueError("Unable to find stock")

        price, status = self.alpaca.lookup_equity_price(db_ticker)
        if status == "unavailable":
            raise RuntimeError("Stock lookup temporarily unavailable")
        if status == "not_found" or price is None:
            self.be.record_invalid_ticker(db_ticker)
            raise ValueError("Unable to find stock")

        self.be.clear_invalid_ticker(db_ticker)
        buyable = self.alpaca.verify_equity_buyable(db_ticker)
        if buyable is False:
            self.be.record_invalid_ticker(db_ticker)
            raise ValueError("Stock is not tradeable")
        if buyable is None and self.alpaca.is_stale_iex_trade(db_ticker):
            self.be.record_invalid_ticker(db_ticker)
            raise ValueError("Stock is not tradeable")

        asset, api_ok = self.alpaca.fetch_asset_raw(db_ticker)
        if buyable is True and asset is None:
            self.be.record_invalid_ticker(db_ticker)
            raise ValueError("Unable to find stock")

        company_name = db_ticker
        exchange = "UNKNOWN"
        if isinstance(asset, dict):
            exchange = str(asset.get("exchange") or "UNKNOWN")
        try:
            from helpers.equity_meta import lookup_company_name

            resolved_name = lookup_company_name(db_ticker, alpaca=self.alpaca)
            if resolved_name:
                company_name = resolved_name
            elif isinstance(asset, dict) and asset.get("name"):
                from helpers.equity_meta import autocomplete_label

                raw_name = str(asset["name"]).strip()
                if autocomplete_label(db_ticker, raw_name) != db_ticker:
                    company_name = raw_name
        except Exception:
            self.logger.debug('Company name lookup failed for %s', db_ticker, exc_info=True)

        self.be.add_stock(
            ticker=db_ticker,
            exchange=exchange,
            company_name=company_name,
        )
        try:
            self.be.add_stock_price(
                ticker_or_id=db_ticker,
                price=price,
                datetime=datetime.now().strftime("%Y-%m-%d %H:%M:00"),
            )
        except Exception:
            # Price row is nice-to-have; the stock itself is what buy_stock needs.
            self.logger.debug('Could not store initial price for %s', db_ticker, exc_info=True)
        return db_ticker

    def _ensure_company_name(self, stock: dtv.Stock) -> None:
        """Backfill company_name when it was stored as a ticker placeholder."""
        from helpers.equity_meta import lookup_company_name, autocomplete_label

        if autocomplete_label(str(stock.ticker), stock.company) != str(stock.ticker):
            return
        try:
            resolved = lookup_company_name(str(stock.ticker), alpaca=self.alpaca)
        except Exception:
            self.logger.debug('Company name backfill failed for %s', stock.ticker, exc_info=True)
            return
        if not resolved:
            return
        try:
            self.be.update_stock(stock.id, company_name=resolved)
            self.logger.info('Backfilled company name for %s: %s', stock.ticker, resolved)
        except Exception:
            self.logger.debug('Failed to persist company name for %s', stock.ticker, exc_info=True)
# # FRONTEND INTERACTIONS. # #
# This is where things like preventing users from joining a game too late, etc. will take place.
class Frontend: # This will be where a bot (like discord) interacts
    def __init__(self, database_name:str, owner_user_id:int, default_permissions:int=210, source:Optional[str]=None):
        """For use with a discord bot or other frontend
        
        Provides  basic error handling, data validation, more user friendly commands, and more.

        Args:
            database_name (str): Name of database.
            owner_user_id (int): User ID of the owner.  This user will be able to control everything.
            source (str, optional): Source.  EG: Discord. Used when creating users.
            default_permissions (int, optional): Default permissions for new users. Defaults to 210. (Users can view and join games, but not create their own). - UNUSED
        """
        self.logger = logging.getLogger('StockGameLogic')
        self.source = source if source else 'Frontend'
        self.be = Backend(database_name)
        self.gl = GameLogic(database_name) # Handle game logic
        self.default_perms = default_permissions
        self.register(user_id=owner_user_id, source=self.source) # Try to register user
        self.be.update_user(user_id=owner_user_id, permissions=288)
        self.owner_id = int(owner_user_id)
    
    def get_user(self, user_id: int):
        """Get a single user

        Args:
            user_id (int): User ID.

        Returns:
            dict: User information.
        """
        user = self.be.get_user(user_id=user_id)
        total_change = 0.0
        total_starting_value = 0.0
        wins = 0
        try:
            participations = self.be.get_many_participants(user_id=user_id)
        except LookupError:
            participations = ()
        for participation in participations:
            if participation.status == 'pending':
                continue
            game = self.be.get_game(participation.game_id)
            if game.status != 'ended':
                continue
            total_change += participation.change_dollars or 0
            total_starting_value += game.start_money
            try:
                players = self.be.get_many_participants(game_id=game.id, status='active', sort_by_value=True)
            except LookupError:
                continue
            if players and players[0].user_id == user_id:
                wins += 1
        user.overall_wins = wins
        user.change_dollars = round(total_change, 2)
        user.change_percent = round((total_change / total_starting_value) * 100, 2) if total_starting_value else 0
        return user

    def get_user_stats(self, user_id: int) -> dtv.UserStatsDetail:
        """Global stats including recurring podiums and best/worst picks."""
        user = self.get_user(user_id)
        recurring_first = recurring_second = recurring_third = 0
        best_recurring_rank: int | None = None
        best_recurring_game_name: str | None = None
        best_pick: tuple[str, float] | None = None
        worst_pick: tuple[str, float] | None = None

        try:
            participations = self.be.get_many_participants(user_id=user_id)
        except LookupError:
            participations = ()

        for participation in participations:
            if participation.status == "pending":
                continue
            game = self.be.get_game(participation.game_id)
            if game.status != "ended":
                continue

            if game.template_id is not None:
                try:
                    players = self.be.get_many_participants(
                        game_id=game.id, status="active", sort_by_value=True,
                    )
                except LookupError:
                    players = ()
                for rank_idx, player in enumerate(players, start=1):
                    if player.user_id != user_id:
                        continue
                    if rank_idx == 1:
                        recurring_first += 1
                    elif rank_idx == 2:
                        recurring_second += 1
                    elif rank_idx == 3:
                        recurring_third += 1
                    if best_recurring_rank is None or rank_idx < best_recurring_rank:
                        best_recurring_rank = rank_idx
                        best_recurring_game_name = game.name
                    break

            try:
                picks = self.be.get_many_stock_picks(
                    participant_id=participation.id,
                    status=["owned", "sold"],
                    include_tickers=True,
                )
            except LookupError:
                picks = ()
            for pick in picks:
                pct = pick.change_percent
                if pct is None:
                    continue
                ticker = pick.stock_ticker or f"ID({pick.stock_id})"
                if best_pick is None or pct > best_pick[1]:
                    best_pick = (ticker, float(pct))
                if worst_pick is None or pct < worst_pick[1]:
                    worst_pick = (ticker, float(pct))

        return dtv.UserStatsDetail(
            user=user,
            recurring_first=recurring_first,
            recurring_second=recurring_second,
            recurring_third=recurring_third,
            best_stock_ticker=best_pick[0] if best_pick else None,
            best_stock_percent=round(best_pick[1], 2) if best_pick else None,
            worst_stock_ticker=worst_pick[0] if worst_pick else None,
            worst_stock_percent=round(worst_pick[1], 2) if worst_pick else None,
            best_recurring_rank=best_recurring_rank,
            best_recurring_game_name=best_recurring_game_name,
        )

    def _user_owns_game(self, user_id:int, game_id:int | str): # Check if a user owns a specific game
        """Check whether a user owns a specific game

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.

        Returns:
            bool: True if owned, False if not.
        """
        self.logger.debug(f'Checking if user: {user_id} owns game: {game_id}')
        try:
            game = self.be.get_game(game_id=game_id)
        except LookupError as e: # TODO log this in the main bit
            self.logger.error(f'Game: {game_id} does not exist')
            raise LookupError(e)
        
        if game.owner_id != user_id:
            self.logger.debug(f'User: {user_id} does not own game: {game_id}')
            return False
        else:
            self.logger.debug(f'User: {user_id} owns game: {game_id}')
            return True
        
    def _participant_id(self, user_id:int, game_id:int | str)-> int:
        """Get a game participant ID

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.

        Returns:
           int: Participant ID
        """
        
        self.register(user_id) # Must try to register user
        players = self.be.get_many_participants(user_id=user_id, game_id=game_id)
        if len(players) == 1:
            return players[0].id
        else:
            raise ValueError(f'Expected one participant ID, but got {len(players)}.')
    
    def _get_game_name(self, game_id:int | str): # Get a game name from ID
        """Get game name from ID

        Args:
            game_id (int): Game ID.

        Raises:
            LookupError: Game not found if ID is invalid

        Returns:
            str: Game name
        """
        
        self.logger.debug(f'Getting game name for game: {game_id}')
        game_info = self.be.get_game(game_id)
        return str(game_info.name)
    
    def clean_text(self, text:str) -> str:
        """
        Helper function to clean text input, to prevent users from injecting formatting that breaks embeds. 
        Only removes formatting that causes line breaks or links.
        """
        text = re.sub(r'[\(\)\[\]/`\\/{}]', '', text) # Remove stupid characters
        return text

    # # GAME RELATED # #
    def new_game(self, user_id:int, name:str, start_date:str, end_date:Optional[str]=None, starting_money:float=10000.00, pick_date:Optional[str]=None, private_game:bool=False, total_picks:int=10, exclusive_picks:bool=False, sell_during_game:bool=False, update_frequency:dtv.UpdateFrequency='alpaca') -> str:
        """Create a new stock game!
        
        owner will be automatically added

        Args:
            user_id (int): Game creators user ID.
            name (str): Name for this game.
            start_date (str): Start date.  Format: `YYYY-MM-DD`.
            end_date (str, optional): End date.  Format: `YYYY-MM-DD`.  Leave blank for infinite game.
            starting_money (float, optional): Starting money. Defaults to $10000.00.
            pick_date (str, optional): Deadline to buy/pick stocks (`YYYY-MM-DD`). If omitted, players can buy anytime.
            private_game(bool, optional): Whether the game is private (True).  Defaults to public (False).
            total_picks (int, optional): Amount of stocks each user picks. Defaults to 10.
            exclusive_picks (bool, optional): Whether multiple users can pick the same stock. If enabled, pick date must be on or before start date.
            sell_during_game (bool, optional): Whether users can sell during the game. Defaults to False.
            update_frequency (str, optional): Price-update tag. Defaults to 'alpaca'.

        Returns:
            str: Identifier of the newly created game.
        """
        self.register(user_id=user_id)
        if not all(c.isalnum() or c.isspace() for c in name):
            raise ValueError("Name must be alphanumeric (spaces allowed)!")
        game_id = self.be.add_game(
            user_id=user_id,
            name=self.clean_text(name),
            start_date=start_date,
            end_date=end_date,
            starting_money=starting_money,
            pick_date=pick_date,
            total_picks=total_picks,
            private_game=private_game,
            exclusive_picks=exclusive_picks,
            sell_during_game=sell_during_game,
            update_frequency=update_frequency,
        )
        
        try:
            self.be.add_participant(user_id=user_id, game_id=game_id)
        except LookupError: # Game wasn't found for some reason
            self.logger.warning('Game was created but owner could not be added.')
        except ValueError as exc:
            self.logger.warning(f'Game was created but owner could not be added.  Reason: {exc}')
        return game_id
       
    
    def list_games(self, include_public:bool=True, include_private:bool=False, include_open:bool=True, include_active:bool=True, include_ended:bool=False, owner_id:Optional[int]=None): 
        """List games
        
        Args:
            include_public (bool, optional): Include public games in results. Defaults to True.
            include_private (bool, optional): Include private games in results. Defaults to False.
            include_open (bool, optional): Include open games in results. Defaults to True.
            include_active (bool, optional): Include active games in results. Defaults to True.
            include_ended (bool, optional): Include ended games in results. Defaults to False.
            owner_id (int, optional): Only games owned by this user ID.
        Returns:
            list: List of games
        """
        games = self.be.get_many_games(
            include_private=include_private,
            include_public=include_public,
            include_active=include_active,
            include_open=include_open,
            include_ended=include_ended,
            owner_id=owner_id,
        )
        return games

    @staticmethod
    def game_is_time_prominent(game: dtv.Game, today: date) -> bool:
        """Whether a game is 'nearby' for /game-list ranking.

        Start date within ±1 month of today, and pick deadline is either unset
        (buy anytime) or still upcoming within the next month (not past).
        """
        month_ago = today - relativedelta(months=1)
        month_ahead = today + relativedelta(months=1)
        if not (month_ago <= game.start_date <= month_ahead):
            return False
        if game.pick_date is None:
            return True
        return today <= game.pick_date <= month_ahead

    @staticmethod
    def game_status_emoji(game: dtv.Game, today: date) -> str:
        """Emoji for Discord game lists: pickable / locked / ended."""
        if game.status == 'ended' or (game.end_date is not None and game.end_date < today):
            return '🛑'
        if game.pick_date is None or game.pick_date >= today:
            return '💸'
        return '🏃🏻‍➡️'

    def _rank_scored_games(
        self,
        scored: list[tuple[dtv.Game, int]],
        today: date,
    ) -> list[tuple[dtv.Game, int]]:
        def bucket_key(item: tuple[dtv.Game, int]) -> tuple[int, int]:
            game, player_count = item
            prominent = 0 if self.game_is_time_prominent(game, today) else 1
            return (prominent, -player_count)

        recurring = [item for item in scored if item[0].template_id is not None]
        others = [item for item in scored if item[0].template_id is None]
        recurring.sort(key=bucket_key)
        others.sort(key=bucket_key)
        return recurring + others

    def list_games_ranked(
        self,
        include_public: bool = True,
        include_private: bool = False,
        include_open: bool = True,
        include_active: bool = True,
        include_ended: bool = False,
        owner_id: Optional[int] = None,
        viewer_user_id: Optional[int] = None,
        today: Optional[date] = None,
    ) -> list[tuple[dtv.Game, int]]:
        """List games ordered for Discord ``/game-list``.

        Order:
        1. Recurring games (``template_id`` set), then non-recurring
        2. Within each group: time-prominent games first, then the rest
        3. Within each prominence bucket: higher player count first

        When ``viewer_user_id`` is set, private games that user owns or plays
        are merged in (other users' private games stay hidden).

        Returns:
            list of ``(game, player_count)`` where player_count is active+pending.
        """
        games = self.list_games(
            include_public=include_public,
            include_private=include_private,
            include_open=include_open,
            include_active=include_active,
            include_ended=include_ended,
            owner_id=owner_id,
        )
        if today is None:
            today = self.gl._today_et()

        scored: list[tuple[dtv.Game, int]] = []
        for game in games:
            try:
                participants = self.be.get_many_participants(game_id=game.id)
                player_count = sum(
                    1 for p in participants if p.status in ('active', 'pending')
                )
            except LookupError:
                player_count = 0
            scored.append((game, player_count))

        if viewer_user_id is not None and owner_id is None:
            existing_ids = {str(game.id) for game, _count in scored}
            try:
                mine = self.list_my_games_ranked(
                    viewer_user_id,
                    include_ended=False,
                    today=today,
                )
            except LookupError:
                mine = []
            for game, player_count in mine:
                if not game.private_game or str(game.id) in existing_ids:
                    continue
                scored.append((game, player_count))
                existing_ids.add(str(game.id))

        return self._rank_scored_games(scored, today)

    def list_my_games_ranked(
        self,
        user_id: int,
        *,
        include_ended: bool = True,
        today: Optional[date] = None,
    ) -> list[tuple[dtv.Game, int]]:
        """Games the user participates in, ranked like ``/game-list``.

        Includes private games the user is in. Ended games are included by
        default so Discord can show the ended status emoji.
        """
        self.register(user_id)
        try:
            players = self.be.get_many_participants(user_id=user_id)
        except LookupError as exc:
            raise LookupError('Player is not in any games.') from exc

        if today is None:
            today = self.gl._today_et()

        scored: list[tuple[dtv.Game, int]] = []
        seen: set[str] = set()
        for player in players:
            if player.status not in ('active', 'pending'):
                continue
            game = self.be.get_game(player.game_id)
            if game.status == 'ended' and not include_ended:
                continue
            game_key = str(game.id)
            if game_key in seen:
                continue
            seen.add(game_key)
            try:
                participants = self.be.get_many_participants(game_id=game.id)
                player_count = sum(
                    1 for p in participants if p.status in ('active', 'pending')
                )
            except LookupError:
                player_count = 0
            scored.append((game, player_count))

        if not scored:
            raise LookupError('Player is not in any games.')

        return self._rank_scored_games(scored, today)

    GameResolvePurpose = Literal["portfolio", "buy", "leave", "info"]

    _NO_MATCHING_GAME_MSG = (
        "No matching game found. Join a game with `/join-game` or check `/my-stocks`."
    )

    def _participant_row(self, user_id: int, game_id: str | int):
        try:
            rows = self.be.get_many_participants(user_id=user_id, game_id=game_id)
        except LookupError:
            return None
        return rows[0] if rows else None

    def _game_eligible_for_purpose(
        self,
        user_id: int,
        game: dtv.Game,
        purpose: GameResolvePurpose,
        *,
        subject_user_id: int | None = None,
    ) -> bool:
        if game.status == "ended":
            return False
        participant = self._participant_row(user_id, game.id)
        subject = (
            self._participant_row(subject_user_id, game.id)
            if subject_user_id is not None
            else participant
        )
        if purpose == "info":
            if game.owner_id == user_id:
                return True
            if participant is None or participant.status not in ("active", "pending"):
                return False
            return True
        if purpose == "portfolio" and subject_user_id is not None and subject_user_id != user_id:
            if game.private_game:
                return False
            if subject is None or subject.status not in ("active", "pending"):
                return False
            return True
        if participant is None or participant.status not in ("active", "pending"):
            return False
        if purpose == "leave":
            return True
        if purpose == "portfolio":
            return True
        if purpose == "buy":
            if participant.status != "active":
                return False
            if game.pick_date and self.gl._today_et() > game.pick_date:
                return False
            remaining, _total = self.pick_capacity(user_id, game.id)
            return remaining > 0
        return False

    def resolve_game_id(
        self,
        user_id: int,
        game_id: str | None,
        *,
        purpose: GameResolvePurpose,
        subject_user_id: int | None = None,
    ) -> str:
        """Resolve ``game_id`` when omitted, or validate an explicit id for ``purpose``."""
        self.register(user_id)
        if game_id is not None:
            game_id = str(game_id).strip()
            try:
                game = self.be.get_game(game_id)
            except LookupError as exc:
                raise LookupError(f"No game with ID {game_id}.") from exc
            if not self._game_eligible_for_purpose(
                user_id, game, purpose, subject_user_id=subject_user_id,
            ):
                raise LookupError(
                    f"Game **{game_id}** is not eligible for this command."
                )
            return str(game.id)

        if purpose == "portfolio" and subject_user_id is not None and subject_user_id != user_id:
            try:
                players = self.be.get_many_participants(user_id=subject_user_id)
            except LookupError as exc:
                raise LookupError(self._NO_MATCHING_GAME_MSG) from exc
            matches: list[dtv.Game] = []
            seen: set[str] = set()
            for row in players:
                if row.status not in ("active", "pending"):
                    continue
                game = self.be.get_game(row.game_id)
                key = str(game.id)
                if key in seen:
                    continue
                seen.add(key)
                if self._game_eligible_for_purpose(
                    user_id, game, purpose, subject_user_id=subject_user_id,
                ):
                    matches.append(game)
        else:
            try:
                ranked = self.list_my_games_ranked(user_id, include_ended=False)
            except LookupError as exc:
                raise LookupError(self._NO_MATCHING_GAME_MSG) from exc
            matches = [
                game
                for game, _count in ranked
                if self._game_eligible_for_purpose(
                    user_id, game, purpose, subject_user_id=subject_user_id,
                )
            ]
        if not matches:
            raise LookupError(self._NO_MATCHING_GAME_MSG)
        if len(matches) == 1:
            return str(matches[0].id)
        lines = "\n".join(f"- **{g.id}** — {g.name}" for g in matches)
        raise LookupError(
            "You're in multiple games — specify `game_id`. Use `/my-games` to see your list.\n"
            + lines
        )
    
    def game_info(self, game_id:int | str, show_leaderboard:bool=True) -> dtv.GameInfo: 
        """Get information and leaderboard for a game.

        Args:
            game_id (int): Game ID
            show_leaderboard (bool, optional): Whether to include the leaderboard in the response

        Returns:
            dict: Game information
        """

        # Return Tuples
        game = self.be.get_game(game_id) # Will raise an error for invalid games
        
        game.current_value = round(game.current_value, 2) if game.current_value else 0# Round to two decimal places
        info = {
            'game': game,
        }
        if show_leaderboard:
            leaderboard = list()
            try:
                players = self.be.get_many_participants(game_id=game_id, sort_by_value=True)
                for player in players:
                    user = self.be.get_user(player.user_id)
                    leaderboard.append({ 
                        'user_id': int(player.user_id),
                        'current_value': round(player.current_value, 2) if player.current_value else 0, # Round to two decimal places
                        'joined': player.datetime_joined,
                        'change_dollars': round(player.change_dollars, 2) if player.change_dollars else 0, # Round to two decimal places
                        'change_percent': round(player.change_percent, 2) if player.change_percent else 0, # Round to two decimal places
                        'days_in_first': int(getattr(player, 'days_in_first', 0) or 0),
                        'affiliation': getattr(player, 'affiliation', None),
                        'display_name': user.display_name or f'ID({player.user_id})',
                        'last_updated': player.last_updated
                    }) # Should keep order
            except LookupError: # No players in game
                self.logger.info(f'No players are currently in game: {game_id}')
                
            info['leaderboard'] = leaderboard  # type: ignore WAA I DONT FUCKING CARE I KNOW THIS WORKS
        return dtv.GameInfo.model_validate(info)
    
    # # USER RELATED
    def register(self, user_id:int, source:Optional[str]=None, username:Optional[str]=None): #TODO should this be an internal function?
        """Register user to allow gameplay

        Args:
            user_id (int): User ID.
            source (str, optional): Source of user.  EG: Discord.  If blank, will use default source set in frontend.
            username (str, optional): Display name/username.

        Returns:
            str: Status/result
        """
        try:
            self.be.add_user(user_id=user_id, source=source if source else self.source, display_name=username, permissions=self.default_perms)
            return "Registered"
        except bexc.UserExistsError: # user already exists
            return "User already registered"

    def join_game(self, user_id:int, game_id:int | str, force_active: bool = False):
        """Join a game.

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            force_active (bool): Join as active even for private games (owner invite).
                If the user is already pending, upgrades them to active.
        
        Raises:
            add_participant > bexc.DoesntExistError: Attempted to join a game that doesn't exist
        """
        self.register(user_id) # Must try to register user
        try:
            self.be.add_participant(
                user_id=int(user_id),
                game_id=game_id,
                force_active=force_active,
            )
        except ValueError as exc:
            if force_active and 'already in game' in str(exc).lower():
                players = self.be.get_many_participants(user_id=user_id, game_id=game_id)
                if players[0].status == 'pending':
                    self.be.update_participant(players[0].id, status='active')
                    return
            raise
        except LookupError:
            raise LookupError('Game not found.')

    def set_participant_affiliation(
        self,
        user_id: int,
        game_id: int | str,
        affiliation: str | None,
    ) -> dtv.GameParticipant:
        """Assign hedge-fund affiliation for the user in a recurring game."""
        return self.be.set_participant_affiliation(user_id, game_id, affiliation)

    def get_pending_game_invite(self, user_id: int, game_id: str) -> dtv.GameInvite | None:
        try:
            invite = self.be.get_game_invite(game_id=game_id, user_id=user_id)
        except LookupError:
            return None
        return invite if invite.status == 'pending' else None

    def list_pending_game_invites(self, user_id: int) -> tuple[dtv.GameInvite, ...]:
        try:
            return self.be.get_pending_invites_for_user(user_id)
        except LookupError:
            return ()

    def record_game_invite(
        self,
        game_id: str,
        user_id: int,
        inviter_id: int,
        *,
        dm_channel_id: int | None,
        dm_message_id: int | None,
    ) -> tuple[dtv.GameInvite, dtv.GameInvite | None]:
        return self.be.upsert_game_invite(
            game_id=game_id,
            user_id=user_id,
            inviter_id=inviter_id,
            dm_channel_id=dm_channel_id,
            dm_message_id=dm_message_id,
        )

    def finalize_game_invite(self, game_id: str, user_id: int, status: str) -> None:
        self.be.set_game_invite_status(game_id=game_id, user_id=user_id, status=status)

    def kick_player(
        self,
        user_id: int,
        game_id: int | str,
        target_user_id: int,
        *,
        enforce_permissions: bool = True,
    ):
        """Remove a participant from a private game (owner / bot owner).

        Args:
            user_id: Actor performing the kick.
            game_id: Game ID.
            target_user_id: User to remove.
            enforce_permissions: When True, only the game owner or bot owner may kick.
        """
        self.register(user_id)
        game = self.be.get_game(game_id)
        if not game.private_game:
            raise bexc.NotAllowedError(
                action='kick_player',
                reason='Not private',
                message='Players can only be kicked from private games.',
            )
        if game.status == 'ended':
            raise bexc.NotAllowedError(
                action='kick_player',
                reason='Game ended',
                message='Cannot kick players from a completed game.',
            )
        if target_user_id == game.owner_id:
            raise PermissionError('Cannot kick the game owner.')
        if (
            enforce_permissions
            and user_id != self.owner_id
            and not self._user_owns_game(user_id=user_id, game_id=game_id)
        ):
            raise PermissionError(
                f'User {user_id} is not allowed to kick players from game {game_id}'
            )
        participant_id = self._participant_id(user_id=target_user_id, game_id=game_id)
        self.be.remove_participant(participant_id)

    def user_owns_any_game(self, user_id: int) -> tuple[bool, bool]:
        """Return (owns_any_game, owns_any_private_game) for help section gating."""
        try:
            games = self.be.get_many_games(
                owner_id=user_id,
                include_public=True,
                include_private=True,
                include_open=True,
                include_active=True,
                include_ended=True,
            )
        except LookupError:
            return False, False
        owns_private = any(game.private_game for game in games)
        return True, owns_private

    def my_games(self, user_id:int, include_ended:bool=False)->dtv.MyGames:
        """Get a list of your current games

        Args:
            user_id (int): User ID.
            include_ended (bool, optional): Whether to include past games.  Defaults to False.

        Returns:
            dict: User information along with current games
        """
        #TODO should this alow filtering for inactive games, etc.?
        self.register(user_id) # Must try to register user
        try:
            players = self.be.get_many_participants(user_id=user_id)
        except LookupError:
            raise LookupError('Player is not in any games.')
        games = {
            'user': self.be.get_user(user_id=user_id), # User details
            'games': [] # Game details will be stored here
            }
        for player in players: # Provide additional details
            game = self.be.get_game(player.game_id)
            if game.status != 'ended' or include_ended: # Add games that are active or all games if include ended
                games['games'].append(game)

        return dtv.MyGames.model_validate(games)
    
    def my_stocks(self, user_id:int, game_id:int | str, show_pending:bool=True, show_sold:bool=False):
        """Get your stocks for a specific game
        
        Includes stock tickers!

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            show_pending (bool, optional): Whether to show pending purchases. Defaults to False (no).
            show_sold (bool, optional): Whether to sold stocks. Defaults to False (no).

        Returns:
            list: Stocks both owned and pending

        Raises:
        self.register(user_id) # Must try to register user
            _participant_id > bexc.DoesntExistError: Player not in game
            get_many_stock_picks > LookupError: No items found.  Raised when no stocks are found
            
        """
        
        self.register(user_id) # Must try to register user
        player_id = self._participant_id(user_id=user_id, game_id=game_id)
        statuses = ['owned', 'pending_sell']
        if show_pending:
            statuses.append('pending_buy')
        if show_sold:
            statuses.append('sold')
        picks = self.be.get_many_stock_picks(participant_id=player_id, status=statuses, include_tickers=True)
        return picks

    def pick_capacity(self, user_id:int, game_id:int | str) -> tuple[int, int]:
        """Return the remaining and total number of picks for a participant."""
        self.register(user_id)
        participant_id = self._participant_id(user_id=user_id, game_id=game_id)
        game = self.be.get_game(game_id)
        try:
            picks = self.be.get_many_stock_picks(
                participant_id=participant_id,
                status=['pending_buy', 'owned', 'pending_sell'],
            )
        except LookupError:
            picks = ()
        total = int(game.pick_count)
        return max(total - len(picks), 0), total
    
    # # STOCK RELATED
    def buy_stock(self, user_id:int, game_id:int | str, ticker:str):
        """Pick/buy a stock
        
        Prevents users from picking too many stocks, or picking stocks if the game has already started and the pick date has passed.

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            ticker (str): Ticker.
            
        Raises:
            ValueError: Invalid Ticker, too long!
            _participant_id > bexc.DoesntExistError: Player not in game
            _participant_id > LookupError: Game or player doesn't exist
            find_stock > ValueError: Stock is not tradeable.  Stock existed at some point, but cannot be traded
            find_stock > ValueError: Unable to find stock.  HTTP error when searching for stock, assume it doesn't exist
            find_stock > ValueError: Failed to add stock (usually means the stock doesn't exist)
            add_stock_pick > bexc.NotAllowedError: reason='Not active'.  Player status isn't active, so cannot pick stocks
            add_stock_pick > bexc.NotAllowedError: reason='Past pick_date'.  (only possible if a pick date is set).
            add_stock_pick > bexc.NotAllowedError: reason='Maximum picks reached'.  Player already has the maximum amount of stock picks.
            add_stock_pick > bexc.AlreadyExistsError: Cannot own the same stock twice.
            add_stock_pick > Exception: Some other issues ocurred.
            
        """ #TODO should this return picks remaining? Could also add that as another function
        
        if len(str(ticker)) > 5:
            raise ValueError('Invalid Ticker, too long!')
        
        self.register(user_id) # Must try to register user
        player_id = self._participant_id(user_id=user_id, game_id=game_id) # If user doesn't exist in the game, error will be raised
        resolved_ticker = self.gl.find_stock(ticker=str(ticker))  # Ensures stock exists; returns DB ticker
        stock = self.be.get_stock(ticker_or_id=resolved_ticker)
        if getattr(stock, 'trade_status', 'active') == 'delisted':
            raise ValueError('Stock was delisted and can no longer be purchased.')
        buyable = self.gl.alpaca.verify_equity_buyable(resolved_ticker)
        if buyable is False:
            raise ValueError('Stock is not tradeable')
        if buyable is None and self.gl.alpaca.is_stale_iex_trade(resolved_ticker):
            raise ValueError('Stock is not tradeable')

        # Draft mode: prevent duplicate tickers across players
        game = self.be.get_game(game_id=game_id)
        if game.draft_mode:
            try:
                participants = self.be.get_many_participants(game_id=game_id)
            except LookupError:
                participants = []
            for p in participants:
                if p.id != player_id:
                    try:
                        existing = self.be.get_many_stock_picks(participant_id=p.id, stock_id=stock.id, status=['pending_buy', 'owned', 'pending_sell'])
                        if len(existing) > 0:
                            raise bexc.AlreadyExistsError(table='stock_picks', duplicate={'ticker': ticker}, message='Draft mode: ticker already picked by another participant')
                    except LookupError:
                        pass

        self.be.add_stock_pick(participant_id=player_id, stock_id=stock.id) # Add the pick

    def sell_stock(self, user_id:int, game_id:int | str, ticker:str) -> str:
        """Sell/cancel a stock pick.

        - Removes pending_buy picks entirely (cancels the order).
        - Rejects owned picks if allow_selling is not enabled on the game.
        - Marks owned picks as pending_sell when selling is allowed.

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            ticker (str): Stock ticker to sell.

        Raises:
            LookupError: No matching pick found.
            bexc.NotAllowedError(reason='Selling disabled'): Game does not allow selling.

        Returns:
            ``cancelled`` for a pending buy, ``sell_requested`` for an owned
            pick, or ``already_pending`` when a sale was already requested.
        """
        self.register(user_id) # Must try to register user
        player_id = self._participant_id(user_id=user_id, game_id=game_id)
        stock = self.be.get_stock(ticker_or_id=ticker)
        try:
            picks = self.be.get_many_stock_picks(participant_id=player_id, stock_id=stock.id, status=['pending_buy', 'owned', 'pending_sell'])
        except LookupError:
            raise LookupError('No matching pick found for this ticker')

        if len(picks) == 0:
            raise LookupError('No matching pick found for this ticker')

        pick = picks[0]  # Should be exactly one due to UNIQUE constraint

        if pick.status == 'pending_buy':
            # Cancel the pending order
            self.be.remove_stock_pick(pick_id=pick.id)
            return 'cancelled'
        elif pick.status == 'owned':
            game = self.be.get_game(game_id=game_id)
            if not game.allow_selling:
                raise bexc.NotAllowedError(action='sell_stock', reason='Selling disabled', message='Cannot sell owned stocks in this game')
            # Mark for sale - price accounting handled by update_stock_picks
            self.be.update_stock_pick(pick_id=pick.id, status='pending_sell')
            return 'sell_requested'
        else:  # pending_sell - already requested, nothing to do
            return 'already_pending'
    
    def remove_pick(self, user_id:int, game_id:int | str, ticker:str): # Remove a stock pick
        """Remove a stock pick. Status must be pending, cannot remove already owned stocks.

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            ticker (str): Ticker.

        Returns:
            dict: Status/result
        """
        self.register(user_id) # Must try to register user
        player_id = self._participant_id(user_id=user_id, game_id=game_id) #TODO check for errors
        stock = self.be.get_stock(ticker_or_id=ticker)
        try:
            picks = self.be.get_many_stock_picks(participant_id=player_id, stock_id=stock.id)
        except LookupError:
            raise LookupError('No picks found')
        if len(picks) > 1: # IDK how you'd even get this to happen.
            raise ValueError(f'Found {len(picks)} matching picks. Cannot remove more than 1 pick at a time.')
        
        if picks[0].status in ['pending_buy']:
            return self.be.remove_stock_pick(pick_id=picks[0].id)
        else:
            raise ValueError(f'Pick status is `{picks[0].status}`.  Only `pending_buy` picks can be removed.')
    
    # # OTHER # #
    def force_update(self, user_id:int, game_id:Optional[int | str]=None, enforce_permissions:bool=True):
        """Force update game(s)

        Args:
            user_id (int): User ID.
            game_id (Optional[int], optional): Game ID. If blank, all games will be updated.
            enforce_permissions (bool): Disable to bypass permission checking.
        """
        self.register(user_id) # Must try to register user
        if (user_id != self.owner_id) and enforce_permissions:
            raise PermissionError(f'User <@{user_id}> is not allowed to manage game {game_id}')

        
        self.gl.update_all(game_id=game_id, force=True) # 
        
    def manage_game(self, user_id:int, game_id:int | str, owner:Optional[int]=None, name:Optional[str]=None, start_date:Optional[str]=None, end_date:Optional[str]=None, status:Optional[str]=None, starting_money:Optional[float]=None, pick_date:Optional[str]=None, private_game:Optional[bool]=None, total_picks:Optional[int]=None, exclusive_picks:Optional[bool]=None, sell_during_game:Optional[bool]=None, update_frequency:Optional[dtv.UpdateFrequency]=None, enforce_permissions:bool=True, clear_end_date:bool=False, clear_pick_date:bool=False):
        """Update/Manage an existing game.
        
        start_date, starting_money, pick_date, total_picks, exclusive_picks, sell_during_game cannot be changed once a game has started

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            owner (int): Game owner user ID (allows changing).
            name (str): Name for this game. 
            start_date (str): Start date in ISO8601 (YYYY-MM-DD). Cannot be changed once game has started.
            end_date (str, optional): End date ISO8601 (YYYY-MM-DD). 
            status (str, optional): Game Status. 
            starting_money (float, optional): Starting money. Cannot be changed once game has started.
            pick_date (str, optional): Date stocks must be picked by in ISO8601 (YYYY-MM-DD). Cannot be changed once game has started.
            private_game(bool, optional): Whether the game is private or not. 
            total_picks (int, optional): Amount of stocks each user picks. Cannot be changed once game has started.
            exclusive_picks (bool, optional): Whether multiple users can pick the same stock. Pick date must be on or before start date. Cannot be changed once game has started.
            sell_during_game (bool, optional): Whether users can sell stocks during the game. Defaults to False. Cannot be changed once game has started.
            update_frequency (str, optional): How often prices should update ('daily', 'hourly', 'minute', 'realtime').
            enforce_permissions (bool): Disable to bypass permission checking.
            clear_end_date (bool): Remove an existing optional end date.
            clear_pick_date (bool): Remove an existing optional pick deadline.

        Raises:
            dict: Status/result
        """
        self.register(user_id) # Must try to register user
        self.logger.debug(f'User: {user_id} is updating game: {game_id}.  Settings[Owner: {owner}, name: {name}, tbd]')
        if (self._user_owns_game(user_id=user_id, game_id=game_id) == False and user_id != self.owner_id) and enforce_permissions:
            self.logger.error(f'User {user_id} is not allowed to make changes to game {game_id}')
            raise PermissionError(f'User {user_id} is not allowed to make changes to game {game_id}')
        
        self.be.update_game(game_id=game_id, owner=owner, name=name, start_date=start_date, end_date=end_date, status=status, starting_money=starting_money, pick_date=pick_date, private_game=private_game, total_picks=total_picks, exclusive_picks=exclusive_picks, sell_during_game=sell_during_game, update_frequency=update_frequency, clear_end_date=clear_end_date, clear_pick_date=clear_pick_date)

    def leave_game(self, user_id:int, game_id:int | str):
        """Remove a participant and their picks from a game.

        Game owners must transfer ownership or delete the game instead.  Stock
        picks are removed by the schema's participant foreign-key cascade.
        """
        self.register(user_id)
        game = self.be.get_game(game_id)
        if game.owner_id == user_id:
            raise PermissionError('Game owners must transfer ownership or delete the game instead.')
        participant_id = self._participant_id(user_id=user_id, game_id=game_id)
        self.be.remove_participant(participant_id)

    def remove_game(self, user_id:int, game_id:int | str, enforce_permissions:bool=True):
        """Remove a game
        
        Only the games creator or the bots owner can remove a game!

        Args:
            user_id (int): User ID. (Must be the game owner OR the bot owner)
            game_id (int): Game ID.
            enforce_permissions (bool): Disable to bypass permission checking.

        Raises:
            PermissionError: Raised if someone who isn't allowed to remove the game tries
        """
        
        self.register(user_id) # Must try to register user
        if user_id != self.owner_id and not self._user_owns_game(user_id=user_id, game_id=game_id) and enforce_permissions:
            raise PermissionError(f'User {user_id} is not allowed to make changes to game {game_id}')
        
        self.be.remove_game(game_id)
    
    def pending_game_users(self, user_id:int, game_id:int | str, enforce_permissions:bool=True):
        """Get a list of pending users for private games

        Args:
            user_id (int): User ID.
            game_id (int): Game ID.
            enforce_permissions (bool): Disable to bypass permission checking.

        Returns:
            list: Pending users (including participant ID)
        """
        self.register(user_id) # Must try to register user
        if user_id != self.owner_id and not self._user_owns_game(user_id=user_id, game_id=game_id) and enforce_permissions:
            raise PermissionError(f'User {user_id} is not allowed to manage players for game {game_id}')
        try:
            return self.be.get_many_participants(game_id=game_id, status='pending')
        except LookupError: # no pending users, return empty list
            return ()

    def count_pending_participants(self, game_id: int | str) -> int:
        """Number of users awaiting approval on a private game (0 if none)."""
        try:
            pending = self.be.get_many_participants(game_id=game_id, status='pending')
        except LookupError:
            return 0
        return len(pending)
        
    def approve_game_users(self, user_id:int, game_id:int | str, approved_user_id:int, enforce_permissions:bool=True):
        """Approve/add a user to private game
        
        Only the bot owner or game owner can approve users for a game by default

        Args:
            user_id (int): User ID (command runner).
            game_id (int): Game ID.
            approved_user_id (int): User ID to approve.
            enforce_permissions (bool): Disable to bypass permission checking.

        Returns:
            dict: status
        """
        
        self.register(user_id) # Must try to register user
        if user_id != self.owner_id and not self._user_owns_game(user_id=user_id, game_id=game_id) and enforce_permissions:
            raise PermissionError(f'User {user_id} is not allowed to approve players for game {game_id}')

        player_id = self._participant_id(user_id=approved_user_id, game_id=game_id) #TODO check for errors
        self.be.update_participant(participant_id=player_id, status='active')

    def get_all_participants(self, game_id: int | str):
        return self.be.get_many_participants(game_id=game_id, sort_by_value=True)
    
# TESTING
if __name__ == "__main__":
    
    DB_NAME = str(os.getenv('DB_NAME')) # Only added so itll shut the fuck up about types
    OWNER = int(os.getenv("OWNER")) # type: ignore # Set owner ID from env 
    test_users = [111, 222, 333, 444, 555, 666]
    test_stocks = ['MSFT', 'SNAP', 'GME', 'COST', 'NVDA', 'MSTR', 'CSCO', 'IBM', 'GE', 'BKNG']
    test_stocks2 = ['MSFT', 'SNAP', 'UBER', 'COST', 'AMD', 'ADBE', 'CSCO', 'IBM', 'GE', 'PEP']
    
    
    game = Frontend(database_name=DB_NAME, owner_user_id=OWNER) # Create frontend 
    game.gl.find_stock(ticker='COST')
    game.gl.update_all()
    game.be.add_stock(ticker='YM=F',exchange='fake', company_name='fake')
    game.be.update_game(1, update_frequency='hourly')
