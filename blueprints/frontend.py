# -*- coding: utf-8 -*-

import asyncio
import re
import time
import bcrypt
import hashlib
import markdown2
from geoip import geolite2
from quart import Blueprint, render_template, redirect, request, session
from cmyui import log, Ansi

from objects import glob
from objects.privileges import Privileges
from objects.utils import flash, get_safe_name

__all__ = ()

frontend = Blueprint('frontend', __name__)

""" valid modes, mods, sorts """
valid_modes = frozenset({'std', 'taiko', 'catch', 'mania'})
valid_mods = frozenset({'vn', 'rx', 'ap'})
valid_sorts = frozenset({'tscore', 'rscore', 'pp', 'plays',
                        'playtime', 'acc', 'maxcombo'})

""" home """
@frontend.route('/home') # GET
@frontend.route('/')
async def home():
    return await render_template('home.html')

""" settings """
@frontend.route('/settings') # GET
async def settings():
    # if not authenticated; render login
    if not 'authenticated' in session:
        return await flash('error', 'You must be logged in to access user settings!', 'login')

    # TODO: user settings page
    NotImplemented

    return await render_template('settings.html')

@frontend.route('/u/<user>') # GET
async def profile(user):
    mode = request.args.get('mode', type=str)
    mods = request.args.get('mods', type=str)
    
    if mods:
        if mods not in valid_mods:
            return b'invalid mods! (vn, rx, ap)'
    else:
        mods = 'vn'
    if mode:
        if mode not in valid_modes:
            return b'invalid mode! (std, taiko, catch, mania)'
    else:
        mode = 'std'

    userdata = await glob.db.fetch(f'SELECT name, id, priv, country FROM users WHERE id = {user}')

    # don't display profile if user is banned
    if not userdata or \
    userdata and userdata['priv'] < 3 and not 'authenticated' in session or \
    userdata and userdata['priv'] < 3 and 'authenticated' in session and not session['user_data']['priv'] & Privileges.Staff:  
        return await render_template('404.html')

    return await render_template('profile.html', user=userdata, mode=mode, mods=mods)

""" leaderboard """
@frontend.route('/leaderboard') # GET
async def leaderboard_nodata():
    return await render_template('leaderboard.html', mode='std', sort='pp', mods='vn')
@frontend.route('/leaderboard/<mode>/<sort>/<mods>') # GET
async def leaderboard(mode, sort, mods):
    return await render_template('leaderboard.html', mode=mode, sort=sort, mods=mods)

""" login """
@frontend.route('/login') # GET
async def login():
    # if authenticated; render home
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already logged in {session["user_data"]["name"]}!', 'home')

    return await render_template('login.html')
@frontend.route('/login', methods=['POST']) # POST
async def login_post():
    # if authenticated; deny post; return
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already logged in {session["user_data"]["name"]}!', 'home')

    login_time = time.time_ns() if glob.config.debug else 0

    form = await request.form
    username = form.get('username')

    # check if account exists
    user_info = await glob.db.fetch(
        'SELECT id, name, priv, pw_bcrypt, silence_end '
        'FROM users WHERE safe_name = %s',
        [get_safe_name(username)]
    )

    # the second part of this if statement exists because if we try to login with Aika
    # and compare our password input against the database it will fail because the
    # hash saved in the database is invalid.
    if not user_info or user_info['id'] == 1:
        if glob.config.debug:
            log(f'{username}\'s login failed - account doesn\'t exist.', Ansi.LYELLOW)
        return await flash('error', 'Account does not exist.', 'login')

    bcrypt_cache = glob.cache['bcrypt']

    pw_bcrypt = user_info['pw_bcrypt'].encode()
    pw_md5 = hashlib.md5(form.get('password').encode()).hexdigest().encode()

    # check credentials (password) against db
    # intentionally slow, will cache to speed up
    if pw_bcrypt in bcrypt_cache:
        if pw_md5 != bcrypt_cache[pw_bcrypt]: # ~0.1ms
            if glob.config.debug:
                log(f'{username}\'s login failed - pw incorrect.', Ansi.LYELLOW)
            return await flash('error', 'Password is incorrect.', 'login')
    else: # ~200ms
        if not bcrypt.checkpw(pw_md5, pw_bcrypt):
            if glob.config.debug:
                log(f'{username}\'s login failed - pw incorrect.', Ansi.LYELLOW)
            return await flash('error', 'Password is incorrect.', 'login')

        # login successful; cache password for next login
        bcrypt_cache[pw_bcrypt] = pw_md5

    # user not verified render verify page
    if not user_info['priv'] & Privileges.Verified:
        if glob.config.debug:
            log(f'{username}\'s login failed - not verified.', Ansi.LYELLOW)
        return await render_template('verify.html')

    # user banned
    if not user_info['priv'] & Privileges.Normal:
        if glob.config.debug:
            log(f'{username}\'s login failed - banned.', Ansi.RED)
        return await flash('error', 'You are banned!', 'login')

    # login successful; store session data
    if glob.config.debug:
        log(f'{username}\'s login succeeded.', Ansi.LGREEN)

    session['authenticated'] = True
    session['user_data'] = {
        'id': user_info['id'],
        'name': user_info['name'],
        'priv': user_info['priv'],
        'silence_end': user_info['silence_end'],
        'is_staff': user_info['priv'] & Privileges.Staff
    }

    if glob.config.debug:
        login_time = (time.time_ns() - login_time) / 1e6
        log(f'Login took {login_time:.2f}ms!', Ansi.LYELLOW)

    # authentication successful; redirect home
    return await flash('success', f'Hey! Welcome back {username}!', 'home')

""" registration """
_username_rgx = re.compile(r'^[\w \[\]-]{2,15}$')
_email_rgx = re.compile(r'^[^@\s]{1,200}@[^@\s\.]{1,30}\.[^@\.\s]{1,24}$')
@frontend.route('/register') # GET
async def register():
    # if authenticated; redirect home
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already registered and logged in {session["user_data"]["name"]}!', 'home')

    # if registration is disabled; redirect home
    if not glob.config.registration:
        return await flash('error', 'Hey! You can\'t register at this time! Sorry for the inconvenience!', 'home')
    
    return await render_template('register.html')
@frontend.route('/register', methods=['POST']) # POST
async def register_post():
    # if authenticated; deny post; return
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already registered and logged in {session["user_data"]["name"]}!', 'home')

    # if registration is disabled; deny post; return
    if not glob.config.registration:
        return await flash('error', 'Hey! You can\'t register at this time! Sorry for the inconvenience!', 'home')

    # get form data (username, email, password)
    form = await request.form
    username = form.get('username')
    email = form.get('email')
    pw_txt = form.get('password')

    # Usernames must:
    # - be within 2-15 characters in length
    # - not contain both ' ' and '_', one is fine
    # - not be in the config's `disallowed_names` list
    # - not already be taken by another player
    # check if username exists
    if not _username_rgx.match(username):
        return await flash('error', 'Invalid username syntax.', 'register')

    if '_' in username and ' ' in username:
        return await flash('error', 'Username may contain "_" or " ", but not both.', 'register')

    if username in glob.config.disallowed_names:
        return await flash('error', 'Disallowed username; pick another.', 'register')

    if await glob.db.fetch('SELECT 1 FROM users WHERE name = %s', username):
        return await flash('error', 'Username already taken by another user.', 'register')

    # Emails must:
    # - match the regex `^[^@\s]{1,200}@[^@\s\.]{1,30}\.[^@\.\s]{1,24}$`
    # - not already be taken by another player
    if not _email_rgx.match(email):
        return await flash('error', 'Invalid email syntax.', 'register')

    if await glob.db.fetch('SELECT 1 FROM users WHERE email = %s', email):
        return await flash('error', 'Email already taken by another user.', 'register')

    # Passwords must:
    # - be within 8-32 characters in length
    # - have more than 3 unique characters
    # - not be in the config's `disallowed_passwords` list
    if not 8 < len(pw_txt) <= 32:
        return await flash('error', 'Password must be 8-32 characters in length', 'register')

    if len(set(pw_txt)) <= 3:
        return await flash('error', 'Password must have more than 3 unique characters.', 'register')

    if pw_text.lower() in glob.config.disallowed_passwords:
        return await flash('error', 'That password was deemed too simple.', 'register')

    async with asyncio.Lock():
        pw_md5 = hashlib.md5(pw_txt.encode()).hexdigest().encode()
        pw_bcrypt = bcrypt.hashpw(pw_md5, bcrypt.gensalt())
        glob.cache['bcrypt'][pw_bcrypt] = pw_md5 # cache result for login

        safe_name = get_safe_name(username)

        country = 'xx'
        if request.remote_addr == '127.0.0.1':
            country = 'xx'
        else:
            match = geolite2.lookup(request.remote_addr)
            if match:
                country = match.country.lower()

        # add to `users` table.
        user_id = await glob.db.execute(
            'INSERT INTO users '
            '(name, safe_name, email, pw_bcrypt, country, creation_time, latest_activity) '
            'VALUES (%s, %s, %s, %s, %s, UNIX_TIMESTAMP(), UNIX_TIMESTAMP())',
            [username, safe_name, email, pw_bcrypt, country]
        )

        # add to `stats` table.
        await glob.db.execute(
            'INSERT INTO stats '
            '(id) VALUES (%s)',
            [user_id]
        )

    if glob.config.debug:
        log(f'{username} has registered - awaiting verification.', Ansi.LGREEN)

    # user has successfully registered
    return await render_template('verify.html')

""" logout """
@frontend.route('/logout') # GET
async def logout():
    if not 'authenticated' in session:
        return await flash('error', 'You can\'t logout if you aren\'t logged in!', 'login')

    if glob.config.debug:
        log(f'{session["user_data"]["name"]} logged out.', Ansi.LGREEN)

    # clear session data
    session.pop('authenticated', None)
    session.pop('user_data', None)

    # render login
    return await flash('success', 'Successfully logged out!', 'login')

""" docs """
@frontend.route('/docs') # GET
async def docs_nodata():
    return await render_template('docs.html')
@frontend.route('/doc/<doc>') # GET
async def docs(doc):
    async with asyncio.Lock():
        markdown = markdown2.markdown_path(f'docs/{doc.lower()}.md')
    return await render_template('doc.html', doc=markdown, doc_title=doc.lower().capitalize())

""" discord redirect """
@frontend.route('/discord') # GET
async def discord():
    return redirect(glob.config.discord_server)