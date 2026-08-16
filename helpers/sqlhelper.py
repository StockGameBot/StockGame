# # DO NOT MAKE ANY CHANGES TO THIS VERSION PLEASE.  IT IS GOING TO BE MOVED INTO ITS OWN MODULE # #

# BUILT-IN
from datetime import datetime
import functools
import logging
import re
import sqlite3
import threading
from typing import Optional, Literal

# EXTERNAL
from pydantic import BaseModel, ConfigDict


# Pydantic stuff
MainStatus = Literal['success', 'error']

class Status(BaseModel): # Status item
    model_config = ConfigDict(arbitrary_types_allowed=True)
    status: str
    reason: str
    result: Optional[str | int | dict | tuple | Exception] = None
    more_info: Optional[str | int | dict | tuple | Exception] = None

def _unix_timestamp(): # Get a unix timestamp
    """Creates a unix timestamp from the current time

    Returns:
        int: Unix timestamp for NOW
    """
    return int(datetime.now().timestamp())

def _iso8601(date_type:str='datetime'): # 
    """Get an ISO formatted date or datetime

    Args:
        date_type (str, optional): Toggle between 'date' or 'datetime'. Defaults to 'datetime'.

    Raises:
        ValueError: _description_

    Returns:
        str: Date/datetime
    """
    now = datetime.now()
    date_type = date_type.lower() # Easier to work with
    if date_type == 'datetime':
        now = now.strftime("%Y-%m-%d %H:%M:%S")
        
    elif date_type == 'date':
        now = now.strftime("%Y-%m-%d")
        
    else:
        raise ValueError(f"Date type must be 'datetime' or 'date', not {date_type}!")
    
    return now

# One lock per database file — Frontend and GameLogic each hold a SqlHelper on the
# same path; asyncio.to_thread also runs DB work on different pool threads.
_db_locks: dict[str, threading.Lock] = {}
_db_locks_guard = threading.Lock()

def _lock_for_db(db_name: str) -> threading.Lock:
    with _db_locks_guard:
        lock = _db_locks.get(db_name)
        if lock is None:
            lock = threading.Lock()
            _db_locks[db_name] = lock
        return lock

def open_and_close(func): #TODO MAKE THIS NOT AI
    """
    Decorator to open and close an SQLite connection around a method call.
    Assumes the class instance (self) has _open_connection and _close_connection methods.
    """
    @functools.wraps(func)  # Preserves the name, docstring, etc., of the decorated function
    def wrapper(self, *args, **kwargs):
        """
        Wrapper function that manages the connection.
        'self' is the instance of the class where the decorated method is defined.
        """
        db_lock = _lock_for_db(self.db)
        db_lock.acquire()
        self._open_connection()  # Call the instance's open connection method
        try:
            # Call the original method (e.g., _sql_items)
            result = func(self, *args, **kwargs)
            return result
        except sqlite3.Error as e:
            # Handle potential SQLite errors during the execution of 'func'
            print(f"SQLite error during {func.__name__}: {e}")
            # Depending on the desired behavior, you might want to re-raise the exception
            # or return a specific value indicating failure.
            raise # Re-raise the exception after logging
        finally:
            # This block will always execute, ensuring the connection is closed
            try:
                self._close_connection() # Call the instance's close connection method
            finally:
                db_lock.release()
    return wrapper

class SqlHelper: # Simple helper for SQL
    def __init__(self, db_name:str, create_backup:bool=False, backup_directory:str='backups/automatic'):
        """SQLite helper tool
        
        Tool to make interacting with an SQLite database easier!  Includes optional backup 

        Args:
            db_name (str): Database name
            create_backup (bool, optional): If True, a full backup of the current database will be created upon first run. The backup directory/folder can be set with `backup_directory`.  Defaults to False
            backup_directory (str, optional):  Set the backup directory.  Only relevant if `create_backup` is True.  Defaults to `backups/automatic`.
        """
        #TODO add backup
        self.logger = logging.getLogger('SqlHelper')
        self.logger.info('Logging for SqlHelper started')
        self.db = db_name
        self.conn: sqlite3.Connection | None = None
        self.cur: sqlite3.Cursor | None = None
        self._open_connection()
        self._close_connection()

    @staticmethod
    def _identifier(value:str, allow_wildcard:bool=False) -> str:
        """Validate an SQL identifier before interpolating it into a query."""
        if allow_wildcard and value == '*':
            return value
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?', value):
            raise ValueError(f'Invalid SQL identifier: {value!r}')
        return value
    
    def _open_connection(self): # Start/open connection:
            self.conn = sqlite3.connect(
                self.db,
                timeout=30.0,
                check_same_thread=False,
            )
            self.cur = self.conn.cursor()
            self.cur.execute("PRAGMA foreign_keys = ON;")
            self.cur.execute("PRAGMA journal_mode = WAL;")
            self.cur.execute("PRAGMA busy_timeout = 30000;")
            
    def _close_connection(self): # Stop/close connection
            conn = self.conn
            if conn is not None:
                conn.close()
                self.conn = None
                self.cur = None
    
    def _simple_status(self, status:MainStatus='success', reason:str='NA', result: str | int | dict | tuple | Exception | None=None, more_info:str | int | dict | tuple | Exception | None='NA')-> Status:
        """Simple status and results object

        Args:
            status (str, optional): Status. Defaults to 'success'.
            reason (str, optional): Reason. Defaults to 'NA'.
            result (str | int | dict | list | Exception | None, optional): Result item (if any).  
            more_info (str | int | dict | list | Exception | None, optional): Extra info. Defaults to 'NA'.

        Returns:
            Status: Status/result
        """
        return Status(status=status, reason=reason, result=result, more_info=more_info)
        
    def _run_query(self, query:str, values:Optional[list]=None, mode: str ='get')-> Status:
        if mode not in ['insert', 'insert_multi', 'update', 'delete', 'get', 'raw-get', 'ddl']:
            raise ValueError(f'Invalid mode {mode}.')
        cur = self.cur
        conn = self.conn
        if cur is None or conn is None:
            raise RuntimeError('Database connection is not open.')
        status = 'error' # Assume the request was no good to start
        reason = 'UNKNOWN ERROR'
        more_info = None
        result = None
        try:
            if mode == 'insert_multi': 
                if not values:
                    raise ValueError('values required for multiple insert') # TODO maybe make this a status instead of a true error idk
                resp = cur.executemany(query, values)
            
            elif values:
                resp = cur.execute(query, values)
            else: # Run without values, prevents error
                resp = cur.execute(query)
            conn.commit() # Commit changes, should only run if something happened
            reason = 'VALID QUERY' # Assume query is valid (I love assuming)
            
            if mode == 'ddl':
                result = None
            elif mode in ['insert', 'update', 'delete']: # Modify/change modes
                if cur.rowcount > 0:
                    result = cur.lastrowid # Get the last updated row ID
                    more_info = f'{cur.rowcount} row effected' # Shows how many rows were effected by the last command
                else:
                    reason = 'NO ROWS EFFECTED'
                    more_info = 'Query valid, but no rows were returned'
            elif mode in ['get', 'raw-get']: # Get modes
                resp = cur.fetchall()
                if len(resp) > 0:
                    result = self._format(resp, cur.description) if mode == 'get' else (resp, cur.description)
                    more_info = f'{len(resp)} rows found'
                else: # The SQL query was valid, but no rows were returned
                    reason = 'NO ROWS RETURNED'
                    more_info = 'Query valid, but no rows were returned'
                    
            status = 'success' if reason == 'VALID QUERY' else 'error'
            
        except sqlite3.IntegrityError as e:   
            if e.sqlite_errorcode in [2067, 1555]: # (Unique, primary key) constraint failed # type: ignore is custom exception
                reason = str(e.sqlite_errorname)  # type: ignore is custom exception
                result = e.args[0].split(':')[1].strip() 
            
            elif e.sqlite_errorcode == 787: # Foreign Key Constraint Failed # type: ignore is custom exception
                reason = 'SQLITE_CONSTRAINT_FOREIGNKEY'
                result = e.args[0]
                
            else:
                reason = str(e.sqlite_errorname)  # type: ignore is custom exception
                result = e
        
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e.args[0]):
                reason = 'DUPLICATE COLUMN NAME'
                result = e.args[0]
                
            else:
                reason = str(e.sqlite_errorname)  # type: ignore is custom exception
                result = e
            
        except Exception as e:
            reason = 'OTHER ERROR'
            result = e
            
        return self._simple_status(
            status=status,
            reason=reason,
            result=result,
            more_info=more_info,
        )

    def _format(self, items:list | tuple, keys:list | tuple)-> tuple[dict]:
        item_keys = [key[0] for key in keys] # Extract keys
        formatted_items = []
        
        for item in items: # Extract the individual values
            formatted_item = {}
            for count, value in enumerate(item):
                if item_keys[count] in formatted_item: # Prevent Key overwriting
                    formatted_item[str(f'{count}-{item_keys[count]}')] = value
                else:
                    formatted_item[item_keys[count]] = value
                
            formatted_items.append(formatted_item)
            
        return tuple(formatted_items)
    
    def _sql_filters(self, filters:dict | str | tuple[str, list])-> tuple[str, list[str | int | float | bool]| None]:
        """Handle different filtering formats and items for other internal methods

        Args:
            filters (dict | str): String (not injection safe) or dict (hopefully injection safe)

        Returns:
            tuple[str, list[str | int | float | bool]| None]:
        """
        
        if isinstance(filters, str):
            filter_str = filters
            filter_items = None
        elif isinstance(filters, tuple):
            filter_str = filters[0] 
            filter_items = filters[1]
        elif not isinstance(filters, dict): # something unexpected provided in filters field
            raise TypeError(f'`filters` must be str or dict, not{type(filters)}.') 
        else:
            filter_str = "" # Will contain filter string (if any)
            filter_vars = list()
            filter_items = list()
            if filters: # Create filter string (if exists)
                for var, item in filters.items():
                    if item != None: # Skip blank items
                        if isinstance(var, tuple): # Support LIKE and NOT by sending a line like this var = ('LIKE', '<query>')
                            operator = var[0].upper()
                            if operator not in {'LIKE', 'IN', 'NOT LIKE'}:
                                raise ValueError(f'Invalid SQL filter operator: {operator}')
                            column = self._identifier(var[1])
                            filter_vars.append(f'{column} {operator} ' + str(f'({item})' if operator == 'IN' else '?'))
                            if not var[0].lower() == 'in':
                                filter_items.append(item)
                        else:
                            filter_vars.append(self._identifier(var) + " = ?")
                            filter_items.append(item)
        
                if len(filter_vars) > 0: # Sometimes filters are sent but all the items are none I guess
                    filter_str = "WHERE " + " AND ".join(filter_vars)
            
        return filter_str, filter_items
    
    def _sql_items(self, items:dict, mode:str='insert'):
        keys = list()
        values = list()
        questionmarks = list()
        for key, val in items.items():
            key = self._identifier(key)
            if val == None:  # Skip blank items
                continue
            elif val == 'NULL': # Allows a field be set back to none/null
                values.append(None)
            else:
                values.append(val)
                
            if mode == 'insert':
                keys.append(key)
            elif mode == 'set':
                keys.append(key +'=?')

            questionmarks.append("?")
        
        return keys, values, questionmarks
    
    @open_and_close
    def insert(self, table:str, items:dict): # Insert into table
        table = self._identifier(table)
        sql_query = "INSERT INTO {table} ({keys}) VALUES({keyvars})"
        keys, values, questionmarks = self._sql_items(items)
        
        sql_query = sql_query.format(table=table, keys=",".join(keys), keyvars=",".join(questionmarks))
        
        return self._run_query(sql_query, values, mode='insert')
    
    @open_and_close
    def _insert_many(self, table:str, columns:list[str], rows:list[dict]): # Insert multiple rows
        """Insert multiple rows at once using executemany.

        Args:
            table: Table name (NOT injection safe — do not expose to users).
            columns: Ordered list of column names (NOT injection safe).
            rows: List of dicts; each dict must have the keys listed in *columns*.
        """
        if not rows:
            return self._simple_status(status='error', reason='NO ROWS',
                                       more_info='rows list is empty')
        table = self._identifier(table)
        columns = [self._identifier(column) for column in columns]
        placeholders = ','.join(['?'] * len(columns))
        sql_query = f"INSERT INTO {table} ({','.join(columns)}) VALUES({placeholders})"
        values = [tuple(row[col] for col in columns) for row in rows]
        return self._run_query(sql_query, values=values, mode='insert_multi')
        
        
        
    @open_and_close    
    def get(self, table:str, columns:list=["*"], filters:dict | str | tuple={}, left_join:Optional[str]=None, order:Optional[dict[str,str]]=None) -> Status: 
        """Run SQL get queries
        
        THE COLUMNS ARE NOT INJECTION SAFE! DO NOT LET USERS SEND ANYTHING HERE, AND NEVER SEND UNTRUSTED INPUT TO table OR columns

        Args:
            table (str): Table name
            columns (list, optional): List of columns to be returned, Defaults to ['*'] (all columns)
            filters (dict | str, optional): Run simple filters by sending them as a dict {'column': 'val'}.  These will be added as `WHERE column = `val` using injection safe input.  Alternatively, a str can be used to send pre-formatted filters, eg: `WHERE column IS NOT 1`.  These AREN'T currently injection safe!
            left_join (str, optional): Include a LEFT JOIN SQL query in your request
            order (dict): Key should be the column name to order by, values should be ASC or DESC
            
        Returns:
            tuple of items and their keys
        """
        table = self._identifier(table)
        if len(columns) == 0:
            columns = ['*']        
        columns = [self._identifier(column, allow_wildcard=True) for column in columns]
        sql_query = """SELECT {columns} FROM {table} {left_join} {filters} {order}"""

        filter_str, filter_items = self._sql_filters(filters)
        
        order_str = "" # Will contain order string (if any)
        order_items = list()
        if order:
            for var, direction in order.items():
                if direction.lower() not in ['asc', 'desc']: # Skip invalid order/sort
                    return self._simple_status( # Return the result
                        status='error', 
                        reason='INVALID ORDER DIRECTION',
                        more_info=f'Order direction must be ASC or DESC, not \'{direction}\'.'
                        )
                
                order_items.append(f"{self._identifier(var)} {direction.upper()}")
            
            order_str = "ORDER BY " + ", ".join(order_items)
            
        sql_query = sql_query.format(columns=",".join(columns), table=table, left_join=left_join or '', filters=filter_str, order=order_str)
        return self._run_query(sql_query, values=filter_items, mode='get')  # type: ignore its a list or status, idk why it has a hard time understanding that but im sick of trying to fix it
    
    @open_and_close
    def update(self, table:str, items:dict, filters:dict | str | tuple={}, force:bool=False):
        table = self._identifier(table)
        sql_query = """UPDATE {table} SET {keys} {filters}"""
        
        filter_str, filter_items = self._sql_filters(filters)

        if not filter_str and not force:
            return self._simple_status(
                status='error',
                reason='FORCE REQUIRED',
                more_info='Empty filters would update all rows; set force=True to proceed',
            )

        keys, value_items, questionmarks = self._sql_items(items, mode='set')
        if len(value_items) == 0:
            return self._simple_status( # Return the result
                status='error', 
                reason='NO COLUMNS CHANGED',
                more_info='Atleast one column must be changed'
                )
        all_items = value_items + (filter_items if isinstance(filter_items, list) else [])
            
        sql_query = sql_query.format(table=table, keys=",".join(keys), filters=filter_str)
        return self._run_query(sql_query, all_items, mode='update')
    
    @open_and_close
    def delete(self, table:str, filters:dict | str | tuple={}, force:bool=False):
        """Delete rows matching *filters*.

        Args:
            table: Table name (NOT injection safe).
            filters: SQL filter(s).  An empty dict is treated as "no filter"
                     and requires *force=True* to guard against truncation.
            force: Must be True when *filters* is empty (or all-dict items are
                   None).  Prevents accidental full-table deletion.
        """
        table = self._identifier(table)
        filter_str, filter_items = self._sql_filters(filters)
        # Guard: refuse to delete every row unless explicitly forced
        if not filter_str and not force:
            return self._simple_status(status='error', reason='FORCE REQUIRED',
                                       more_info='Empty filters would delete all rows; set force=True to proceed')

        sql_query = """DELETE FROM {table} {filters}"""
        sql_query = sql_query.format(table=table, filters=filter_str)
        return self._run_query(sql_query, filter_items, mode='delete')
    
    @open_and_close
    def send_query(self, query, values: Optional[list]=None , mode:str='get'): # Send an SQL query directly
        return self._run_query(query=query, values=values, mode=mode)
    
    @open_and_close
    def delete_table(self, table:str, force:bool=False): # Drop a table
        """Drop a table from the database.

        Args:
            table: Table name (NOT injection safe — do not expose to users).
            force: Must be True to proceed.  Protects against accidental drops.
        """
        if not force:
            return self._simple_status(status='error', reason='FORCE REQUIRED',
                                       more_info='Set force=True to drop the table')
        # Table names cannot be parameterized; use a simple allow-list guard
        _allowed_tables = {
            'database_info', 'users', 'game_templates', 'games', 'stocks',
            'stock_prices', 'game_participants', 'stock_picks',
        }
        if table.lower() not in _allowed_tables:
            return self._simple_status(status='error', reason='TABLE NOT ALLOWED',
                                       more_info=f'Table {table} is not in the allow-list')
        query = f"DROP TABLE IF EXISTS {table}"
        return self._run_query(query=query, mode='ddl')
    
    @open_and_close
    def alter_table(self, table:str, data:str, mode:str):
        query = """ALTER TABLE {table}
        """
        if mode.lower() == 'add':
            query += 'ADD {data}'
        
        elif mode.lower() == 'rename':
            query += 'RENAME {data}'
            
        else:
            raise ValueError(f'Invalid mode: {mode}')
        
        return self._run_query(query=query.format(table=table, data=data), mode='ddl')

    def create_backup(self, dest_db:str, display_progress:bool=False):
        """Manually create a backup of the current database

        Args:
            dest_db (str): Destination/name for the backup.  Accepts path.
            display_progress (bool, optional): Whether to display(print) the progress of the backup.  Defaults to False.
        """
        # Backup main DB
        
        
        def info(status:int, todo:int, total:int):
            print(f'Status: {status} | Copied {total - todo} of {total}')
            
        # Connect/create DBs
        src = sqlite3.connect(self.db)
        dest = sqlite3.connect(dest_db)
        
        # Display progress conditionally (idk if this will work, in my head it does)
        src.backup(dest, progress=info if display_progress else None) 
        
        # Close
        src.close()
        dest.close()
