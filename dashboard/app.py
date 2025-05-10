import sys # Add this
import traceback # Add this

try:
    from flask import Flask, redirect, url_for, session, request, render_template, flash, jsonify
    from functools import wraps
    import requests
    from requests_oauthlib import OAuth2Session
    import os
    from werkzeug.middleware.proxy_fix import ProxyFix

    from config import Config
    import internal_api_client # Import the new API client
    from api_clients import tmdb_client # Import the TMDB client
except Exception as e:
    print(f"CRITICAL ERROR DURING INITIAL IMPORTS: {e}", file=sys.stderr)
    print(traceback.format_exc(), file=sys.stderr)
    sys.exit(1) # Exit immediately if imports fail

app = Flask(__name__)
app.config.from_object(Config)

# Apply ProxyFix to handle X-Forwarded-Proto and other headers correctly
# This is important for OAuth2 callbacks when running behind a reverse proxy (like on Render)
# It ensures that url_for generates HTTPS URLs when the proxy terminates SSL.
# x_for=1 means trust X-Forwarded-For (client IP)
# x_proto=1 means trust X-Forwarded-Proto (http/https)
# x_host=1 means trust X-Forwarded-Host
# x_prefix=1 means trust X-Forwarded-Prefix (if app is under a subpath)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Ensure the templates and static folders exist, Flask might not create them.
# However, write_to_file will create parent directories if they don't exist.
# For templates, we'll create them explicitly later.
# For static, it's good practice to have it.
static_dir = os.path.join(os.path.dirname(__file__), 'static')
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
if not os.path.exists(templates_dir):
    os.makedirs(templates_dir)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'discord_user' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    if 'discord_user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login')
def login():
    if not Config.DISCORD_CLIENT_ID or not Config.DISCORD_REDIRECT_URI:
        return "OAuth2 Client ID or Redirect URI is not configured. Please check your .env file.", 500
    
    discord_oauth = OAuth2Session(
        Config.DISCORD_CLIENT_ID,
        redirect_uri=Config.DISCORD_REDIRECT_URI,
        scope=Config.DISCORD_SCOPES
    )
    authorization_url, state = discord_oauth.authorization_url(Config.DISCORD_AUTHORIZATION_URL)
    session['oauth2_state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    if request.values.get('error'):
        return request.values['error']

    if not Config.DISCORD_CLIENT_ID or not Config.DISCORD_CLIENT_SECRET or not Config.DISCORD_REDIRECT_URI:
        return "OAuth2 Client ID, Client Secret, or Redirect URI is not configured.", 500

    discord_oauth = OAuth2Session(
        Config.DISCORD_CLIENT_ID,
        state=session.get('oauth2_state'),
        redirect_uri=Config.DISCORD_REDIRECT_URI
    )
    
    try:
        token = discord_oauth.fetch_token(
            Config.DISCORD_TOKEN_URL,
            client_secret=Config.DISCORD_CLIENT_SECRET,
            authorization_response=request.url
        )
    except Exception as e:
        return f"Error fetching token: {e}", 500

    session['oauth2_token'] = token

    # Fetch user info
    user_info_response = discord_oauth.get(Config.DISCORD_USER_INFO_URL)
    if user_info_response.status_code == 200:
        user_data = user_info_response.json()
        session['discord_user'] = {
            'id': user_data.get('id'),
            'username': user_data.get('username'),
            'discriminator': user_data.get('discriminator'),
            'avatar': user_data.get('avatar'),
            'email': user_data.get('email')
        }
        # Construct avatar URL
        if user_data.get('avatar'):
            session['discord_user']['avatar_url'] = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png"
        else:
            # Default avatar if user has none (based on discriminator)
            session['discord_user']['avatar_url'] = f"https://cdn.discordapp.com/embed/avatars/{int(user_data.get('discriminator', '0')) % 5}.png"
        
        next_url = session.pop('next_url', url_for('dashboard'))
        return redirect(next_url)
    else:
        return "Failed to fetch user information from Discord.", 500

@app.route('/dashboard')
@login_required
def dashboard():
    user = session.get('discord_user')
    if not user or 'id' not in user:
        flash("User session not found or invalid. Please log in again.", "error")
        return redirect(url_for('login'))

    user_id = user['id']
    data = {}
    errors = {}

    # Fetch TV Subscriptions
    tv_subs, tv_error = internal_api_client.get_tv_subscriptions(user_id)
    if tv_error:
        errors['tv_shows'] = tv_error
        flash(f"Error fetching TV shows: {tv_error}", "error")
    data['tv_shows'] = tv_subs if tv_subs else []
    print(f"Fetched TV Shows for dashboard: {tv_subs}")

    # Fetch Movie Subscriptions
    movie_subs, movie_error = internal_api_client.get_movie_subscriptions(user_id)
    if movie_error:
        errors['movies'] = movie_error
        flash(f"Error fetching movies: {movie_error}", "error")
    data['movies'] = movie_subs if movie_subs else []
    print(f"Fetched Movie Subscriptions for dashboard: {movie_subs}")

    # Fetch Tracked Stocks
    tracked_stocks, stocks_error = internal_api_client.get_tracked_stocks(user_id)
    if stocks_error:
        errors['stocks'] = stocks_error
        flash(f"Error fetching stocks: {stocks_error}", "error")
    data['stocks'] = tracked_stocks if tracked_stocks else []
    print(f"Fetched Tracked Stocks for dashboard: {tracked_stocks}")

    # Fetch Stock Alerts
    stock_alerts, alerts_error = internal_api_client.get_stock_alerts(user_id)
    if alerts_error:
        errors['stock_alerts'] = alerts_error
        flash(f"Error fetching stock alerts: {alerts_error}", "error")
    data['stock_alerts'] = stock_alerts if stock_alerts else []
    print(f"Fetched Stock Alerts for dashboard: {stock_alerts}")
    
    # Fetch User Settings
    user_settings, settings_error = internal_api_client.get_user_settings(user_id)
    if settings_error:
        errors['settings'] = settings_error
        flash(f"Error fetching settings: {settings_error}", "error")
    data['settings'] = user_settings if user_settings else {}
    print(f"Fetched User Settings for dashboard: {user_settings}")


    return render_template('dashboard.html', user=user, data=data, errors=errors, config=app.config)

# The /tv_shows route is removed as data is now fetched and displayed on the main /dashboard.
# If specific pages are needed later, they can be added.

@app.route('/dashboard/add_tv_show', methods=['POST'])
@login_required
def add_tv_show():
    user = session.get('discord_user')
    if not user or 'id' not in user:
        flash("User session not found or invalid. Please log in again.", "error")
        return redirect(url_for('login'))

    user_id = user['id']
    tmdb_id_str = request.form.get('tmdb_id')
    title = request.form.get('title')
    poster_path = request.form.get('poster_path')

    if not tmdb_id_str or not title: # poster_path can be optional (empty string)
        flash("Missing tmdb_id or title for the TV show.", "error")
        return redirect(url_for('dashboard'))

    try:
        tmdb_id = int(tmdb_id_str)
    except ValueError:
        flash("Invalid TMDB ID format.", "error")
        return redirect(url_for('dashboard'))

    response_json, error_message = internal_api_client.add_tv_show_subscription(
        user_id=user_id,
        tmdb_id=tmdb_id,
        title=title,
        poster_path=poster_path if poster_path else "" # Ensure poster_path is a string
    )

    if error_message:
        flash(f"Error adding TV show: {error_message}", "error")
    else:
        # Assuming the API returns a success message or the created object
        flash(f"TV show '{title}' added successfully!", "success")
        
    return redirect(url_for('dashboard'))

@app.route('/dashboard/add_movie', methods=['POST'])
@login_required
def add_movie():
    user = session.get('discord_user')
    if not user or 'id' not in user:
        flash("User session not found or invalid. Please log in again.", "error")
        return redirect(url_for('login'))

    user_id = user['id']
    tmdb_id_str = request.form.get('tmdb_id')
    title = request.form.get('title')
    poster_path = request.form.get('poster_path')

    if not tmdb_id_str or not title: # poster_path can be optional
        flash("Missing tmdb_id or title for the movie.", "error")
        return redirect(url_for('dashboard'))

    try:
        tmdb_id = int(tmdb_id_str)
    except ValueError:
        flash("Invalid TMDB ID format.", "error")
        return redirect(url_for('dashboard'))

    response_json, error_message = internal_api_client.add_movie_subscription(
        user_id=user_id,
        tmdb_id=tmdb_id,
        title=title,
        poster_path=poster_path if poster_path else ""
    )

    if error_message:
        flash(f"Error adding movie: {error_message}", "error")
    else:
        flash(f"Movie '{title}' added successfully!", "success")
        
    return redirect(url_for('dashboard'))
@app.route('/dashboard/remove_tv_show/<int:tmdb_id>', methods=['POST'])
@login_required
def remove_tv_show(tmdb_id: int):
    user = session.get('discord_user')
    if not user or 'id' not in user:
        flash("User session not found or invalid. Please log in again.", "error")
        return redirect(url_for('login'))

    user_id = user['id']

    app.logger.info(f"Attempting to remove TV show subscription for user_id: {user_id}, tmdb_id: {tmdb_id}")

    response_json, error_message = internal_api_client.remove_tv_show_subscription(
        user_id=user_id,
        tmdb_id=tmdb_id
    )

    if error_message:
        flash(f"Error removing TV show (ID: {tmdb_id}): {error_message}", "error")
        app.logger.error(f"Error removing TV show for user {user_id}, tmdb_id {tmdb_id}: {error_message}")
    else:
        # response_json might be None or {} for a successful 204, so we don't typically use its content here
        flash(f"TV show (ID: {tmdb_id}) subscription removed successfully!", "success")
        app.logger.info(f"Successfully removed TV show subscription for user_id: {user_id}, tmdb_id: {tmdb_id}")
        
    return redirect(url_for('dashboard'))
@app.route('/dashboard/search_tv_shows', methods=['GET'])
@login_required
def search_tv_shows_route():
    user = session.get('discord_user')
    if not user or 'id' not in user:
        return jsonify({"error": "User session not found or invalid. Please log in again."}), 401

    query = request.args.get('query')
    if not query:
        return jsonify({"error": "Missing search query parameter."}), 400

    results = tmdb_client.search_tv_shows(query)

    if results is None: # Should be an empty list on error from tmdb_client
        return jsonify({"error": "An error occurred while searching for TV shows."}), 500
    
    # The tmdb_client.search_tv_shows is expected to return a list of dicts
    # or an empty list if no results or an error occurred.
    # So, we can directly return it.
    return jsonify(results)
@app.route('/logout')
def logout():
    session.pop('discord_user', None)
    session.pop('oauth2_state', None)
    session.pop('oauth2_token', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    # Check for essential configurations
    if not Config.SECRET_KEY or Config.SECRET_KEY == 'your_default_secret_key' or Config.SECRET_KEY == 'REPLACE_WITH_A_VERY_STRONG_RANDOM_SECRET_KEY_FOR_FLASK':
        print("CRITICAL ERROR: DASHBOARD_SECRET_KEY is not set or is using a placeholder value.")
        print("Flask sessions will not work securely. Please set a strong, unique key in your .env file.")
        print("Exiting to prevent insecure operation.")
        exit(1) # Exit if secret key is not secure
        
    if not Config.DISCORD_CLIENT_ID or not Config.DISCORD_CLIENT_SECRET or not Config.DISCORD_REDIRECT_URI:
        print("WARNING: Discord OAuth2 credentials (DASHBOARD_CLIENT_ID, DASHBOARD_CLIENT_SECRET, DASHBOARD_REDIRECT_URI)")
        print("are not fully configured in your .env file. Discord login will likely fail.")

    # Ensure templates directory exists (though it should have been created by now)
    if not os.path.exists(templates_dir):
        os.makedirs(templates_dir)
        print(f"Created missing templates directory: {templates_dir}. You should have login.html and dashboard.html here.")

    # Determine port and debug mode from environment variables for deployment flexibility
    port = int(os.environ.get('PORT', 5001))
    # FLASK_DEBUG for Render, or general DEBUG. Default to False if not set or not 'true'.
    debug_mode_str = os.environ.get('FLASK_DEBUG', os.environ.get('DEBUG', 'False'))
    debug_mode = debug_mode_str.lower() in ['true', '1', 't']

    print(f"Starting Flask app on host 0.0.0.0, port {port}, debug mode: {debug_mode}")
    app.run(host='0.0.0.0', port=port, debug=debug_mode)