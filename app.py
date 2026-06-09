from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import uuid
from PIL import Image, ImageDraw
import io

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key_here_change_in_production')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

# PostgreSQL connection
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

# Создаем папки для загрузок
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'videos'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'images'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'community_avatars'), exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi', 'mkv', 'webm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    print("Инициализация базы данных PostgreSQL...")
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Пользователи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT,
            bio TEXT,
            avatar TEXT,
            background_image TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Посты
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            media_type TEXT,
            media_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Друзья
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS friends (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            friend_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, friend_id)
        )
    ''')
    
    # Лайки
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, post_id)
        )
    ''')
    
    # Комментарии
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Сообщества
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS communities (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            avatar TEXT,
            background_image TEXT,
            creator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            is_channel BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Участники сообществ
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS community_members (
            id SERIAL PRIMARY KEY,
            community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(community_id, user_id)
        )
    ''')
    
    # Посты в сообществах
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS community_posts (
            id SERIAL PRIMARY KEY,
            community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            media_type TEXT,
            media_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✓ База данных PostgreSQL инициализирована")

def resize_image(image_path, max_size=(800, 600)):
    try:
        with Image.open(image_path) as img:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(image_path, optimize=True, quality=85)
    except Exception as e:
        print(f"Ошибка при изменении размера изображения: {e}")

def create_favicon():
    favicon_path = 'static/favicon.ico'
    if not os.path.exists(favicon_path):
        os.makedirs('static', exist_ok=True)
        img = Image.new('RGBA', (32, 32), color=(0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for i in range(16):
            color = (81, 129, 184, 255 - i * 8)
            d.ellipse([i, i, 32-i, 32-i], outline=color)
        d.ellipse([8, 8, 24, 24], fill=(255, 255, 255, 255))
        img.save(favicon_path)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем посты пользователя и его друзей
    cursor.execute('''
        SELECT p.*, u.username, u.name, u.avatar,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
               (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count,
               EXISTS(SELECT 1 FROM likes WHERE post_id = p.id AND user_id = %s) as liked
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.user_id = %s OR p.user_id IN (
            SELECT friend_id FROM friends WHERE user_id = %s AND status = 'accepted'
        )
        ORDER BY p.created_at DESC
    ''', (session['user_id'], session['user_id'], session['user_id']))
    
    posts = cursor.fetchall()
    
    # Информация о текущем пользователе
    cursor.execute('SELECT * FROM users WHERE id = %s', (session['user_id'],))
    user = cursor.fetchone()
    
    # Друзья
    cursor.execute('''
        SELECT u.id, u.username, u.name, u.avatar 
        FROM friends f
        JOIN users u ON f.friend_id = u.id
        WHERE f.user_id = %s AND f.status = 'accepted'
        LIMIT 10
    ''', (session['user_id'],))
    friends = cursor.fetchall()
    
    # Заявки в друзья
    cursor.execute('''
        SELECT u.id, u.username, u.name, u.avatar 
        FROM friends f
        JOIN users u ON f.user_id = u.id
        WHERE f.friend_id = %s AND f.status = 'pending'
    ''', (session['user_id'],))
    friend_requests = cursor.fetchall()
    
    # Сообщества пользователя
    cursor.execute('''
        SELECT c.*, cm.role 
        FROM communities c
        JOIN community_members cm ON c.id = cm.community_id
        WHERE cm.user_id = %s
        ORDER BY c.created_at DESC
        LIMIT 10
    ''', (session['user_id'],))
    user_communities = cursor.fetchall()
    
    # Популярные сообщества
    cursor.execute('''
        SELECT c.*, COUNT(cm.id) as members_count
        FROM communities c
        LEFT JOIN community_members cm ON c.id = cm.community_id
        GROUP BY c.id
        ORDER BY members_count DESC
        LIMIT 5
    ''')
    popular_communities = cursor.fetchall()
    
    conn.close()
    
    return render_template('home.html', 
                         posts=posts, 
                         user=user, 
                         friends=friends, 
                         friend_requests=friend_requests,
                         user_communities=user_communities,
                         popular_communities=popular_communities)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        name = request.form.get('name', '')
        
        if len(password) < 6:
            flash('Пароль должен содержать не менее 6 символов', 'error')
            return render_template('register.html')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            hashed_password = generate_password_hash(password)
            cursor.execute('INSERT INTO users (username, password, name) VALUES (%s, %s, %s)', 
                          (username, hashed_password, name))
            conn.commit()
            flash('Регистрация прошла успешно! Теперь вы можете войти.', 'success')
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            conn.rollback()
            flash('Это имя пользователя уже занято.', 'error')
        finally:
            conn.close()
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT id, password FROM users WHERE username = %s', (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(url_for('home'))
        else:
            flash('Неверное имя пользователя или пароль.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/post', methods=['POST'])
def create_post():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    content = request.form['content']
    media_file = request.files.get('media')
    media_type = None
    media_url = None
    
    if media_file and allowed_file(media_file.filename):
        file_ext = media_file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4()}.{file_ext}"
        
        if file_ext in ['mp4', 'mov', 'avi', 'mkv', 'webm']:
            media_type = 'video'
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'videos', filename)
        else:
            media_type = 'image'
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'images', filename)
        
        media_file.save(filepath)
        media_url = filename
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO posts (user_id, content, media_type, media_url) VALUES (%s, %s, %s, %s)', 
                  (session['user_id'], content, media_type, media_url))
    conn.commit()
    conn.close()
    
    flash('Пост опубликован!', 'success')
    return redirect(url_for('home'))

@app.route('/like/<int:post_id>', methods=['POST'])
def like_post(post_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT id FROM likes WHERE user_id = %s AND post_id = %s', 
                  (session['user_id'], post_id))
    existing_like = cursor.fetchone()
    
    if existing_like:
        cursor.execute('DELETE FROM likes WHERE id = %s', (existing_like['id'],))
        liked = False
    else:
        cursor.execute('INSERT INTO likes (user_id, post_id) VALUES (%s, %s)', 
                      (session['user_id'], post_id))
        liked = True
    
    cursor.execute('SELECT COUNT(*) as count FROM likes WHERE post_id = %s', (post_id,))
    likes_count = cursor.fetchone()['count']
    
    conn.commit()
    conn.close()
    
    return jsonify({'liked': liked, 'likes_count': likes_count})

@app.route('/comment/<int:post_id>', methods=['POST'])
def add_comment(post_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    content = request.form['content']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO comments (user_id, post_id, content) VALUES (%s, %s, %s)', 
                  (session['user_id'], post_id, content))
    conn.commit()
    conn.close()
    
    flash('Комментарий добавлен!', 'success')
    return redirect(request.referrer or url_for('home'))

@app.route('/get_comments/<int:post_id>')
def get_comments(post_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.*, u.username, u.name, u.avatar
        FROM comments c
        JOIN users u ON c.user_id = u.id
        WHERE c.post_id = %s
        ORDER BY c.created_at DESC
    ''', (post_id,))
    comments = cursor.fetchall()
    conn.close()
    return jsonify([dict(comment) for comment in comments])

@app.route('/add_friend', methods=['POST'])
def add_friend():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    friend_username = request.form['friend_username']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT id FROM users WHERE username = %s', (friend_username,))
    friend = cursor.fetchone()
    
    if not friend:
        flash('Пользователь не найден.', 'error')
    elif friend['id'] == session['user_id']:
        flash('Нельзя добавить самого себя в друзья.', 'error')
    else:
        cursor.execute('''
            SELECT id, status FROM friends 
            WHERE (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)
        ''', (session['user_id'], friend['id'], friend['id'], session['user_id']))
        
        existing = cursor.fetchone()
        
        if existing:
            if existing['status'] == 'pending':
                flash('Заявка уже отправлена или ожидает подтверждения.', 'info')
            else:
                flash('Этот пользователь уже у вас в друзьях.', 'info')
        else:
            cursor.execute('INSERT INTO friends (user_id, friend_id) VALUES (%s, %s)', 
                          (session['user_id'], friend['id']))
            flash('Заявка в друзья отправлена.', 'success')
    
    conn.commit()
    conn.close()
    return redirect(url_for('home'))

@app.route('/accept_friend/<int:friend_id>')
def accept_friend(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE friends SET status = %s WHERE user_id = %s AND friend_id = %s', 
                  ('accepted', friend_id, session['user_id']))
    
    cursor.execute('''
        INSERT INTO friends (user_id, friend_id, status) 
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, friend_id) DO NOTHING
    ''', (session['user_id'], friend_id, 'accepted'))
    
    conn.commit()
    conn.close()
    
    flash('Заявка в друзья принята.', 'success')
    return redirect(url_for('home'))

@app.route('/reject_friend/<int:friend_id>')
def reject_friend(friend_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM friends WHERE user_id = %s AND friend_id = %s', 
                  (friend_id, session['user_id']))
    
    conn.commit()
    conn.close()
    
    flash('Заявка отклонена.', 'success')
    return redirect(url_for('home'))

@app.route('/profile/<username>')
def profile(username):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
    user = cursor.fetchone()
    
    if not user:
        flash('Пользователь не найден.', 'error')
        return redirect(url_for('home'))
    
    cursor.execute('''
        SELECT p.*, u.username, u.name, u.avatar,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as likes_count,
               (SELECT COUNT(*) FROM comments WHERE post_id = p.id) as comments_count,
               EXISTS(SELECT 1 FROM likes WHERE post_id = p.id AND user_id = %s) as liked
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.user_id = %s
        ORDER BY p.created_at DESC
    ''', (session['user_id'], user['id']))
    posts = cursor.fetchall()
    
    is_friend = False
    friend_request_sent = False
    if user['id'] != session['user_id']:
        cursor.execute('''
            SELECT status FROM friends 
            WHERE (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)
        ''', (session['user_id'], user['id'], user['id'], session['user_id']))
        
        friend_status = cursor.fetchone()
        if friend_status:
            is_friend = friend_status['status'] == 'accepted'
            friend_request_sent = friend_status['status'] == 'pending'
    
    conn.close()
    
    return render_template('profile.html', user=user, posts=posts, is_friend=is_friend, friend_request_sent=friend_request_sent)

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    name = request.form.get('name', '')
    bio = request.form.get('bio', '')
    avatar = request.files.get('avatar')
    background = request.files.get('background')
    
    avatar_url = None
    background_url = None
    
    if avatar and allowed_file(avatar.filename):
        filename = f"avatar_{uuid.uuid4()}.{avatar.filename.rsplit('.', 1)[1].lower()}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'images', filename)
        avatar.save(filepath)
        resize_image(filepath, (200, 200))
        avatar_url = filename
    
    if background and allowed_file(background.filename):
        filename = f"bg_{uuid.uuid4()}.{background.filename.rsplit('.', 1)[1].lower()}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds', filename)
        background.save(filepath)
        resize_image(filepath, (1200, 400))
        background_url = filename
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if avatar_url and background_url:
        cursor.execute('UPDATE users SET name = %s, bio = %s, avatar = %s, background_image = %s WHERE id = %s', 
                      (name, bio, avatar_url, background_url, session['user_id']))
    elif avatar_url:
        cursor.execute('UPDATE users SET name = %s, bio = %s, avatar = %s WHERE id = %s', 
                      (name, bio, avatar_url, session['user_id']))
    elif background_url:
        cursor.execute('UPDATE users SET name = %s, bio = %s, background_image = %s WHERE id = %s', 
                      (name, bio, background_url, session['user_id']))
    else:
        cursor.execute('UPDATE users SET name = %s, bio = %s WHERE id = %s', 
                      (name, bio, session['user_id']))
    
    conn.commit()
    conn.close()
    
    flash('Профиль обновлен.', 'success')
    return redirect(url_for('home'))

@app.route('/communities')
def communities():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT c.*, u.username as creator_name, 
               COUNT(cm.id) as members_count,
               EXISTS(SELECT 1 FROM community_members WHERE community_id = c.id AND user_id = %s) as is_member
        FROM communities c
        JOIN users u ON c.creator_id = u.id
        LEFT JOIN community_members cm ON c.id = cm.community_id
        GROUP BY c.id, u.username
        ORDER BY members_count DESC
    ''', (session['user_id'],))
    
    communities_list = cursor.fetchall()
    conn.close()
    
    return render_template('communities.html', communities=communities_list)

@app.route('/create_community', methods=['GET', 'POST'])
def create_community():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form['name']
        description = request.form.get('description', '')
        is_channel = 'is_channel' in request.form
        avatar = request.files.get('avatar')
        background = request.files.get('background')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        avatar_url = None
        background_url = None
        
        if avatar and allowed_file(avatar.filename):
            filename = f"community_avatar_{uuid.uuid4()}.{avatar.filename.rsplit('.', 1)[1].lower()}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'community_avatars', filename)
            avatar.save(filepath)
            resize_image(filepath)
            avatar_url = filename
        
        if background and allowed_file(background.filename):
            filename = f"community_bg_{uuid.uuid4()}.{background.filename.rsplit('.', 1)[1].lower()}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'backgrounds', filename)
            background.save(filepath)
            resize_image(filepath, (1200, 400))
            background_url = filename
        
        cursor.execute('''
            INSERT INTO communities (name, description, avatar, background_image, creator_id, is_channel)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (name, description, avatar_url, background_url, session['user_id'], is_channel))
        
        community_id = cursor.fetchone()['id']
        
        cursor.execute('''
            INSERT INTO community_members (community_id, user_id, role)
            VALUES (%s, %s, 'admin')
        ''', (community_id, session['user_id']))
        
        conn.commit()
        conn.close()
        
        flash('Сообщество успешно создано!', 'success')
        return redirect(url_for('community', community_id=community_id))
    
    return render_template('create_community.html')

@app.route('/community/<int:community_id>')
def community(community_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT c.*, u.username as creator_name,
               COUNT(cm.id) as members_count,
               EXISTS(SELECT 1 FROM community_members WHERE community_id = c.id AND user_id = %s) as is_member,
               (SELECT role FROM community_members WHERE community_id = c.id AND user_id = %s) as user_role
        FROM communities c
        JOIN users u ON c.creator_id = u.id
        LEFT JOIN community_members cm ON c.id = cm.community_id
        WHERE c.id = %s
        GROUP BY c.id, u.username
    ''', (session['user_id'], session['user_id'], community_id))
    
    community = cursor.fetchone()
    
    if not community:
        flash('Сообщество не найдено', 'error')
        return redirect(url_for('communities'))
    
    cursor.execute('''
        SELECT cp.*, u.username, u.name, u.avatar,
               (SELECT COUNT(*) FROM likes WHERE post_id = cp.id) as likes_count,
               (SELECT COUNT(*) FROM comments WHERE post_id = cp.id) as comments_count,
               EXISTS(SELECT 1 FROM likes WHERE post_id = cp.id AND user_id = %s) as liked
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        WHERE cp.community_id = %s
        ORDER BY cp.created_at DESC
    ''', (session['user_id'], community_id))
    
    posts = cursor.fetchall()
    
    cursor.execute('''
        SELECT u.id, u.username, u.name, u.avatar, cm.role, cm.joined_at
        FROM community_members cm
        JOIN users u ON cm.user_id = u.id
        WHERE cm.community_id = %s
        ORDER BY 
            CASE cm.role 
                WHEN 'admin' THEN 1 
                ELSE 2 
            END,
            cm.joined_at
    ''', (community_id,))
    
    members = cursor.fetchall()
    conn.close()
    
    return render_template('community.html', 
                         community=community, 
                         posts=posts, 
                         members=members)

@app.route('/join_community/<int:community_id>')
def join_community(community_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT id FROM community_members WHERE community_id = %s AND user_id = %s', 
                  (community_id, session['user_id']))
    
    if cursor.fetchone():
        flash('Вы уже являетесь участником этого сообщества', 'info')
    else:
        cursor.execute('INSERT INTO community_members (community_id, user_id) VALUES (%s, %s)', 
                      (community_id, session['user_id']))
        conn.commit()
        flash('Вы успешно присоединились к сообществу!', 'success')
    
    conn.close()
    return redirect(url_for('community', community_id=community_id))

@app.route('/leave_community/<int:community_id>')
def leave_community(community_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем, является ли пользователь создателем
    cursor.execute('SELECT creator_id FROM communities WHERE id = %s', (community_id,))
    community = cursor.fetchone()
    
    if community and community['creator_id'] == session['user_id']:
        flash('Вы не можете покинуть сообщество, так как являетесь его создателем', 'error')
    else:
        cursor.execute('DELETE FROM community_members WHERE community_id = %s AND user_id = %s', 
                      (community_id, session['user_id']))
        conn.commit()
        flash('Вы вышли из сообщества', 'success')
    
    conn.close()
    return redirect(url_for('communities'))

@app.route('/create_community_post/<int:community_id>', methods=['POST'])
def create_community_post(community_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    content = request.form['content']
    media_file = request.files.get('media')
    media_type = None
    media_url = None
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем членство и права
    cursor.execute('''
        SELECT cm.role, c.is_channel 
        FROM community_members cm 
        JOIN communities c ON cm.community_id = c.id 
        WHERE cm.community_id = %s AND cm.user_id = %s
    ''', (community_id, session['user_id']))
    member = cursor.fetchone()
    
    if not member:
        flash('Вы не являетесь участником этого сообщества', 'error')
        return redirect(url_for('community', community_id=community_id))
    
    if member['is_channel'] and member['role'] != 'admin':
        flash('Только администраторы могут публиковать в этом канале', 'error')
        return redirect(url_for('community', community_id=community_id))
    
    if media_file and allowed_file(media_file.filename):
        file_ext = media_file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4()}.{file_ext}"
        
        if file_ext in ['mp4', 'mov', 'avi', 'mkv', 'webm']:
            media_type = 'video'
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'videos', filename)
        else:
            media_type = 'image'
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'images', filename)
        
        media_file.save(filepath)
        media_url = filename
    
    cursor.execute('''
        INSERT INTO community_posts (community_id, user_id, content, media_type, media_url)
        VALUES (%s, %s, %s, %s, %s)
    ''', (community_id, session['user_id'], content, media_type, media_url))
    
    conn.commit()
    conn.close()
    
    flash('Пост успешно опубликован в сообществе!', 'success')
    return redirect(url_for('community', community_id=community_id))

def create_templates():
    templates_dir = 'templates'
    os.makedirs(templates_dir, exist_ok=True)
    
    # Base template with improved design
    with open(f'{templates_dir}/base.html', 'w', encoding='utf-8') as f:
        f.write('''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CASCADE - Социальная сеть</title>
    <link rel="icon" type="image/x-icon" href="{{ url_for('static', filename='favicon.ico') }}">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        :root {
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --secondary: #10b981;
            --accent: #f59e0b;
            --danger: #ef4444;
            --text: #1f2937;
            --text-light: #6b7280;
            --bg: #f3f4f6;
            --card-bg: #ffffff;
            --border: #e5e7eb;
            --shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06);
            --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif; 
            background: var(--bg); 
            color: var(--text);
            min-height: 100vh;
        }
        
        /* Header */
        .header { 
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            color: white; 
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: var(--shadow-lg);
        }
        
        .container { max-width: 1280px; margin: 0 auto; padding: 0 24px; }
        
        .nav { 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            height: 64px;
        }
        
        .logo { 
            font-size: 28px; 
            font-weight: 800; 
            display: flex; 
            align-items: center; 
            gap: 10px;
            background: linear-gradient(135deg, #fff, #e0e7ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .logo i { 
            background: none;
            -webkit-text-fill-color: white;
            font-size: 32px;
        }
        
        .nav-links { display: flex; gap: 24px; align-items: center; }
        
        .nav-links a { 
            color: white; 
            text-decoration: none; 
            font-weight: 500;
            padding: 8px 12px;
            border-radius: 8px;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .nav-links a:hover { 
            background: rgba(255, 255, 255, 0.1);
            transform: translateY(-1px);
        }
        
        /* Main Layout */
        .main-content { 
            display: grid; 
            grid-template-columns: 280px 1fr 320px; 
            gap: 24px; 
            margin: 24px 0; 
        }
        
        /* Cards */
        .sidebar, .feed, .right-sidebar { 
            background: var(--card-bg); 
            border-radius: 16px; 
            padding: 20px; 
            box-shadow: var(--shadow);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .sidebar:hover, .feed:hover, .right-sidebar:hover {
            box-shadow: var(--shadow-lg);
        }
        
        /* User Info Card */
        .user-info-card {
            text-align: center;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }
        
        .user-avatar-large {
            width: 96px;
            height: 96px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid var(--primary);
            margin-bottom: 12px;
        }
        
        /* Post Card */
        .post { 
            margin-bottom: 24px; 
            padding: 20px; 
            border-radius: 16px;
            background: var(--card-bg);
            box-shadow: var(--shadow);
            transition: all 0.3s ease;
        }
        
        .post:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-lg);
        }
        
        .post-header { 
            display: flex; 
            align-items: center; 
            margin-bottom: 16px; 
        }
        
        .avatar { 
            width: 48px; 
            height: 48px; 
            border-radius: 50%; 
            margin-right: 12px; 
            object-fit: cover;
            border: 2px solid var(--primary);
        }
        
        .post-content { 
            margin: 16px 0; 
            line-height: 1.6;
            font-size: 15px;
        }
        
        .post-media { 
            max-width: 100%; 
            border-radius: 12px; 
            margin: 12px 0;
            cursor: pointer;
            transition: opacity 0.3s;
        }
        
        .post-media:hover {
            opacity: 0.95;
        }
        
        .video-player {
            width: 100%;
            border-radius: 12px;
            background: #000;
        }
        
        /* Post Actions */
        .post-actions { 
            display: flex; 
            gap: 24px; 
            margin-top: 16px;
            padding-top: 12px;
            border-top: 1px solid var(--border);
        }
        
        .action-btn { 
            background: none; 
            border: none; 
            color: var(--text-light); 
            cursor: pointer; 
            display: flex; 
            align-items: center; 
            gap: 6px;
            padding: 8px 12px;
            border-radius: 8px;
            transition: all 0.2s;
            font-size: 14px;
        }
        
        .action-btn:hover { 
            background: var(--bg);
            color: var(--primary); 
        }
        
        .action-btn.liked { 
            color: var(--danger); 
        }
        
        .action-btn.liked:hover {
            background: #fee2e2;
        }
        
        /* Friend Items */
        .friend-item { 
            display: flex; 
            align-items: center; 
            gap: 12px;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
        }
        
        .friend-item:last-child {
            border-bottom: none;
        }
        
        .friend-avatar { 
            width: 40px; 
            height: 40px; 
            border-radius: 50%; 
            object-fit: cover;
        }
        
        /* Forms */
        .form-group { margin-bottom: 16px; }
        
        .form-control { 
            width: 100%; 
            padding: 12px 16px; 
            border: 2px solid var(--border); 
            border-radius: 12px;
            font-size: 14px;
            transition: all 0.2s;
        }
        
        .form-control:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
        }
        
        textarea.form-control {
            resize: vertical;
            font-family: inherit;
        }
        
        /* Buttons */
        .btn { 
            background: var(--primary); 
            color: white; 
            border: none; 
            padding: 10px 20px; 
            border-radius: 10px; 
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-weight: 500;
            transition: all 0.2s;
        }
        
        .btn:hover { 
            background: var(--primary-dark);
            transform: translateY(-1px);
        }
        
        .btn-success { 
            background: var(--secondary); 
        }
        
        .btn-success:hover { 
            background: #059669; 
        }
        
        .btn-danger { 
            background: var(--danger); 
        }
        
        .btn-danger:hover { 
            background: #dc2626; 
        }
        
        .btn-outline {
            background: transparent;
            border: 2px solid var(--primary);
            color: var(--primary);
        }
        
        .btn-outline:hover {
            background: var(--primary);
            color: white;
        }
        
        /* Flash Messages */
        .flash { 
            padding: 14px 20px; 
            margin: 16px 0; 
            border-radius: 12px; 
            font-weight: 500;
            animation: slideDown 0.3s ease;
        }
        
        @keyframes slideDown {
            from {
                opacity: 0;
                transform: translateY(-20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .success { 
            background: #d1fae5; 
            color: #065f46; 
            border-left: 4px solid var(--secondary);
        }
        
        .error { 
            background: #fee2e2; 
            color: #991b1b; 
            border-left: 4px solid var(--danger);
        }
        
        .info { 
            background: #e0e7ff; 
            color: #3730a3; 
            border-left: 4px solid var(--primary);
        }
        
        /* Community Cards */
        .community-card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 16px;
            margin: 12px 0;
            box-shadow: var(--shadow);
            transition: all 0.2s;
            border: 1px solid var(--border);
        }
        
        .community-card:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-lg);
            border-color: var(--primary);
        }
        
        .community-avatar { 
            width: 56px; 
            height: 56px; 
            border-radius: 12px; 
            object-fit: cover;
        }
        
        /* Modal */
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            backdrop-filter: blur(4px);
            animation: fadeIn 0.3s;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        
        .modal-content {
            background: var(--card-bg);
            margin: 10% auto;
            padding: 0;
            width: 90%;
            max-width: 500px;
            border-radius: 20px;
            box-shadow: var(--shadow-lg);
            animation: slideUp 0.3s;
        }
        
        @keyframes slideUp {
            from {
                transform: translateY(50px);
                opacity: 0;
            }
            to {
                transform: translateY(0);
                opacity: 1;
            }
        }
        
        .modal-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .modal-body {
            padding: 20px;
            max-height: 60vh;
            overflow-y: auto;
        }
        
        .close {
            font-size: 28px;
            cursor: pointer;
            transition: color 0.2s;
        }
        
        .close:hover {
            color: var(--danger);
        }
        
        /* Comment styles */
        .comment-item {
            padding: 12px;
            border-bottom: 1px solid var(--border);
            display: flex;
            gap: 12px;
        }
        
        .comment-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            object-fit: cover;
        }
        
        .comment-content {
            flex: 1;
        }
        
        .comment-author {
            font-weight: 600;
            font-size: 13px;
        }
        
        .comment-text {
            font-size: 14px;
            margin-top: 4px;
        }
        
        .comment-time {
            font-size: 11px;
            color: var(--text-light);
            margin-top: 4px;
        }
        
        /* Responsive */
        @media (max-width: 1024px) {
            .main-content { grid-template-columns: 1fr; }
            .sidebar, .right-sidebar { display: none; }
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: var(--bg);
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--primary);
            border-radius: 10px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--primary-dark);
        }
    </style>
</head>
<body>
    <header class="header">
        <div class="container">
            <nav class="nav">
                <div class="logo">
                    <i class="fas fa-water"></i>
                    CASCADE
                </div>
                <div class="nav-links">
                    {% if 'user_id' in session %}
                        <a href="{{ url_for('home') }}"><i class="fas fa-home"></i> Главная</a>
                        <a href="{{ url_for('communities') }}"><i class="fas fa-users"></i> Сообщества</a>
                        <a href="{{ url_for('logout') }}"><i class="fas fa-sign-out-alt"></i> Выйти</a>
                    {% else %}
                        <a href="{{ url_for('login') }}">Войти</a>
                        <a href="{{ url_for('register') }}">Регистрация</a>
                    {% endif %}
                </div>
            </nav>
        </div>
    </header>

    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {% block content %}{% endblock %}
    </div>

    <script>
        // Like functionality
        document.addEventListener('DOMContentLoaded', function() {
            // Like buttons
            document.querySelectorAll('.like-btn').forEach(btn => {
                btn.addEventListener('click', async function() {
                    const postId = this.dataset.postId;
                    const isCommunity = this.dataset.community === 'true';
                    
                    try {
                        const response = await fetch(`/like/${postId}`, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'}
                        });
                        
                        const data = await response.json();
                        const icon = this.querySelector('i');
                        const countSpan = this.parentElement.nextElementSibling;
                        
                        if (data.liked) {
                            this.classList.add('liked');
                            icon.className = 'fas fa-heart';
                        } else {
                            this.classList.remove('liked');
                            icon.className = 'far fa-heart';
                        }
                        
                        if (countSpan && countSpan.tagName === 'SPAN') {
                            countSpan.textContent = data.likes_count;
                        }
                    } catch (error) {
                        console.error('Error:', error);
                    }
                });
            });
            
            // Comment modals
            document.querySelectorAll('.comment-btn').forEach(btn => {
                btn.addEventListener('click', async function() {
                    const postId = this.dataset.postId;
                    const modalId = `comment-modal-${postId}`;
                    let modal = document.getElementById(modalId);
                    
                    if (!modal) {
                        modal = createCommentModal(postId);
                        document.body.appendChild(modal);
                    }
                    
                    // Load comments
                    await loadComments(postId);
                    modal.style.display = 'block';
                });
            });
        });
        
        function createCommentModal(postId) {
            const modal = document.createElement('div');
            modal.id = `comment-modal-${postId}`;
            modal.className = 'modal';
            modal.innerHTML = `
                <div class="modal-content">
                    <div class="modal-header">
                        <h3><i class="fas fa-comments"></i> Комментарии</h3>
                        <span class="close">&times;</span>
                    </div>
                    <div class="modal-body">
                        <div id="comments-list-${postId}" style="max-height: 400px; overflow-y: auto;">
                            <div style="text-align: center; padding: 20px;">
                                <i class="fas fa-spinner fa-spin"></i> Загрузка...
                            </div>
                        </div>
                        <form id="comment-form-${postId}" style="margin-top: 20px;">
                            <textarea class="form-control" placeholder="Написать комментарий..." rows="3" required></textarea>
                            <button type="submit" class="btn" style="margin-top: 10px; width: 100%;">
                                <i class="fas fa-paper-plane"></i> Отправить
                            </button>
                        </form>
                    </div>
                </div>
            `;
            
            modal.querySelector('.close').onclick = () => modal.style.display = 'none';
            window.onclick = (event) => {
                if (event.target === modal) modal.style.display = 'none';
            };
            
            const form = modal.querySelector(`#comment-form-${postId}`);
            form.onsubmit = async (e) => {
                e.preventDefault();
                const content = form.querySelector('textarea').value;
                if (!content.trim()) return;
                
                const response = await fetch(`/comment/${postId}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: `content=${encodeURIComponent(content)}`
                });
                
                if (response.ok) {
                    form.querySelector('textarea').value = '';
                    await loadComments(postId);
                }
            };
            
            return modal;
        }
        
        async function loadComments(postId) {
            const container = document.getElementById(`comments-list-${postId}`);
            if (!container) return;
            
            try {
                const response = await fetch(`/get_comments/${postId}`);
                const comments = await response.json();
                
                if (comments.length === 0) {
                    container.innerHTML = '<div style="text-align: center; padding: 20px; color: #6b7280;">Нет комментариев. Будьте первым!</div>';
                    return;
                }
                
                container.innerHTML = comments.map(comment => `
                    <div class="comment-item">
                        <img src="${comment.avatar ? '/static/uploads/images/' + comment.avatar : 'https://via.placeholder.com/32'}" 
                             alt="Avatar" class="comment-avatar">
                        <div class="comment-content">
                            <div class="comment-author">${comment.name || comment.username}</div>
                            <div class="comment-text">${escapeHtml(comment.content)}</div>
                            <div class="comment-time">${new Date(comment.created_at).toLocaleString()}</div>
                        </div>
                    </div>
                `).join('');
            } catch (error) {
                console.error('Error loading comments:', error);
                container.innerHTML = '<div style="text-align: center; padding: 20px; color: #ef4444;">Ошибка загрузки комментариев</div>';
            }
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    </script>
</body>
</html>''')

    # Home template
    with open(f'{templates_dir}/home.html', 'w', encoding='utf-8') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
<div class="main-content">
    <!-- Левая панель -->
    <div class="sidebar">
        <div class="user-info-card">
            <img src="{% if user and user.avatar %}{{ url_for('static', filename='uploads/images/' + user.avatar) }}{% else %}https://via.placeholder.com/96{% endif %}" 
                 alt="Avatar" class="user-avatar-large">
            <h3>{{ user.name or user.username if user else 'User' }}</h3>
            <p style="color: var(--text-light);">@{{ user.username if user else 'username' }}</p>
            {% if user and user.bio %}
                <p style="margin-top: 8px; font-size: 13px;">{{ user.bio }}</p>
            {% endif %}
        </div>
        
        <nav style="margin-top: 20px;">
            <a href="{{ url_for('home') }}" style="display: block; padding: 10px; color: var(--text); text-decoration: none; border-radius: 8px; margin: 4px 0;">
                <i class="fas fa-home"></i> Главная
            </a>
            <a href="{{ url_for('communities') }}" style="display: block; padding: 10px; color: var(--text); text-decoration: none; border-radius: 8px; margin: 4px 0;">
                <i class="fas fa-users"></i> Сообщества
            </a>
        </nav>

        <h3 style="margin-top: 24px; font-size: 16px;">Мои сообщества</h3>
        {% for community in user_communities %}
        <div class="community-card" style="display: flex; align-items: center; gap: 12px;">
            <img src="{% if community.avatar %}{{ url_for('static', filename='uploads/community_avatars/' + community.avatar) }}{% else %}https://via.placeholder.com/40{% endif %}" 
                 alt="Community" style="width: 40px; height: 40px; border-radius: 8px;">
            <div>
                <strong><a href="{{ url_for('community', community_id=community.id) }}" style="color: var(--text); text-decoration: none;">{{ community.name }}</a></strong>
                <div style="font-size: 11px; color: var(--text-light);">{{ community.role }}</div>
            </div>
        </div>
        {% else %}
        <p style="color: var(--text-light); font-size: 13px;">Вы не состоите в сообществах</p>
        {% endfor %}
    </div>

    <!-- Основная лента -->
    <div class="feed">
        <div style="background: var(--card-bg); border-radius: 16px; padding: 20px; margin-bottom: 24px; box-shadow: var(--shadow);">
            <h3 style="margin-bottom: 16px;"><i class="fas fa-edit"></i> Создать пост</h3>
            <form action="{{ url_for('create_post') }}" method="post" enctype="multipart/form-data">
                <textarea name="content" class="form-control" rows="3" placeholder="Что у вас нового?" required></textarea>
                <div style="margin-top: 12px; display: flex; justify-content: space-between; align-items: center;">
                    <label style="cursor: pointer; background: var(--bg); padding: 8px 16px; border-radius: 8px;">
                        <i class="fas fa-image"></i> Прикрепить файл
                        <input type="file" name="media" accept="image/*,video/*" style="display: none;">
                    </label>
                    <button type="submit" class="btn">Опубликовать <i class="fas fa-paper-plane"></i></button>
                </div>
                <small style="color: var(--text-light);">Можно загрузить фото или видео (макс. 100MB)</small>
            </form>
        </div>

        {% for post in posts %}
        <div class="post">
            <div class="post-header">
                <img src="{% if post.avatar %}{{ url_for('static', filename='uploads/images/' + post.avatar) }}{% else %}https://via.placeholder.com/48{% endif %}" 
                     alt="Avatar" class="avatar">
                <div>
                    <strong><a href="{{ url_for('profile', username=post.username) }}" style="color: var(--text); text-decoration: none;">{{ post.name or post.username }}</a></strong>
                    <div style="color: var(--text-light); font-size: 12px;">
                        {{ post.created_at }}
                    </div>
                </div>
            </div>
            
            <div class="post-content">
                {{ post.content }}
            </div>
            
            {% if post.media_url %}
                {% if post.media_type == 'image' %}
                    <img src="{{ url_for('static', filename='uploads/images/' + post.media_url) }}" 
                         alt="Post media" class="post-media" onclick="window.open(this.src)">
                {% elif post.media_type == 'video' %}
                    <video controls class="post-media video-player">
                        <source src="{{ url_for('static', filename='uploads/videos/' + post.media_url) }}">
                        Ваш браузер не поддерживает видео.
                    </video>
                {% endif %}
            {% endif %}
            
            <div class="post-actions">
                <button class="action-btn like-btn {% if post.liked %}liked{% endif %}" data-post-id="{{ post.id }}">
                    <i class="{% if post.liked %}fas{% else %}far{% endif %} fa-heart"></i> Нравится
                </button>
                <span style="margin-right: 16px;">{{ post.likes_count or 0 }}</span>
                
                <button class="action-btn comment-btn" data-post-id="{{ post.id }}">
                    <i class="far fa-comment"></i> Комментировать
                </button>
                <span>{{ post.comments_count or 0 }}</span>
            </div>
        </div>
        {% else %}
        <div class="post" style="text-align: center;">
            <i class="fas fa-newspaper" style="font-size: 48px; color: var(--text-light); margin-bottom: 16px;"></i>
            <p>Пока нет постов. Будьте первым, кто опубликует что-то!</p>
        </div>
        {% endfor %}
    </div>

    <!-- Правая панель -->
    <div class="right-sidebar">
        <h3 style="margin-bottom: 16px;"><i class="fas fa-user-friends"></i> Друзья</h3>
        {% for friend in friends %}
        <div class="friend-item">
            <img src="{% if friend.avatar %}{{ url_for('static', filename='uploads/images/' + friend.avatar) }}{% else %}https://via.placeholder.com/40{% endif %}" 
                 alt="Avatar" class="friend-avatar">
            <div>
                <a href="{{ url_for('profile', username=friend.username) }}" style="color: var(--text); text-decoration: none; font-weight: 500;">{{ friend.name or friend.username }}</a>
            </div>
        </div>
        {% else %}
        <p style="color: var(--text-light);">У вас пока нет друзей</p>
        {% endfor %}
        
        {% if friend_requests %}
        <h3 style="margin: 20px 0 12px;"><i class="fas fa-user-plus"></i> Заявки в друзья</h3>
        {% for request in friend_requests %}
        <div class="friend-item">
            <img src="{% if request.avatar %}{{ url_for('static', filename='uploads/images/' + request.avatar) }}{% else %}https://via.placeholder.com/40{% endif %}" 
                 alt="Avatar" class="friend-avatar">
            <div style="flex: 1;">
                <div><a href="{{ url_for('profile', username=request.username) }}" style="color: var(--text); text-decoration: none;">{{ request.name or request.username }}</a></div>
                <div style="display: flex; gap: 8px; margin-top: 8px;">
                    <a href="{{ url_for('accept_friend', friend_id=request.id) }}" class="btn btn-success" style="padding: 4px 12px; font-size: 12px;">Принять</a>
                    <a href="{{ url_for('reject_friend', friend_id=request.id) }}" class="btn btn-danger" style="padding: 4px 12px; font-size: 12px;">Отклонить</a>
                </div>
            </div>
        </div>
        {% endfor %}
        {% endif %}
        
        <h3 style="margin: 20px 0 12px;"><i class="fas fa-search"></i> Добавить друга</h3>
        <form action="{{ url_for('add_friend') }}" method="post">
            <div style="display: flex; gap: 8px;">
                <input type="text" name="friend_username" placeholder="Имя пользователя" class="form-control" required>
                <button type="submit" class="btn" style="padding: 10px 16px;">+</button>
            </div>
        </form>
        
        <h3 style="margin: 20px 0 12px;"><i class="fas fa-fire"></i> Популярные сообщества</h3>
        {% for community in popular_communities %}
        <div class="community-card" style="display: flex; align-items: center; gap: 12px;">
            <img src="{% if community.avatar %}{{ url_for('static', filename='uploads/community_avatars/' + community.avatar) }}{% else %}https://via.placeholder.com/40{% endif %}" 
                 alt="Community" style="width: 40px; height: 40px; border-radius: 8px;">
            <div style="flex: 1;">
                <strong><a href="{{ url_for('community', community_id=community.id) }}" style="color: var(--text); text-decoration: none;">{{ community.name }}</a></strong>
                <div style="font-size: 11px; color: var(--text-light);">{{ community.members_count }} участников</div>
            </div>
        </div>
        {% endfor %}

        <div style="margin-top: 20px;">
            <a href="{{ url_for('create_community') }}" class="btn" style="width: 100%; text-align: center;">
                <i class="fas fa-plus"></i> Создать сообщество
            </a>
        </div>
        
        <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--border);">
            <h3 style="margin-bottom: 12px;"><i class="fas fa-user-edit"></i> Редактировать профиль</h3>
            <form action="{{ url_for('update_profile') }}" method="post" enctype="multipart/form-data">
                <input type="text" name="name" placeholder="Ваше имя" value="{{ user.name or '' }}" class="form-control" style="margin-bottom: 8px;">
                <textarea name="bio" placeholder="О себе" class="form-control" style="margin-bottom: 8px;">{{ user.bio or '' }}</textarea>
                <input type="file" name="avatar" accept="image/*" class="form-control" style="margin-bottom: 8px; padding: 8px;">
                <input type="file" name="background" accept="image/*" class="form-control" style="margin-bottom: 8px; padding: 8px;">
                <button type="submit" class="btn" style="width: 100%;">Обновить профиль</button>
            </form>
        </div>
    </div>
</div>
{% endblock %}''')

    # Login template
    with open(f'{templates_dir}/login.html', 'w', encoding='utf-8') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
<div style="max-width: 420px; margin: 80px auto;">
    <div style="background: var(--card-bg); border-radius: 24px; padding: 40px; box-shadow: var(--shadow-lg);">
        <div style="text-align: center; margin-bottom: 32px;">
            <i class="fas fa-water" style="font-size: 64px; color: var(--primary);"></i>
            <h1 style="margin-top: 16px; font-size: 32px;">Добро пожаловать</h1>
            <p style="color: var(--text-light);">Войдите в свой аккаунт</p>
        </div>
        
        <form action="{{ url_for('login') }}" method="post">
            <div class="form-group">
                <input type="text" name="username" class="form-control" placeholder="Имя пользователя" required>
            </div>
            <div class="form-group">
                <input type="password" name="password" class="form-control" placeholder="Пароль" required>
            </div>
            <button type="submit" class="btn" style="width: 100%; padding: 14px;">Войти</button>
        </form>
        
        <p style="margin-top: 24px; text-align: center;">
            Нет аккаунта? <a href="{{ url_for('register') }}" style="color: var(--primary); text-decoration: none;">Зарегистрируйтесь</a>
        </p>
    </div>
</div>
{% endblock %}''')

    # Register template
    with open(f'{templates_dir}/register.html', 'w', encoding='utf-8') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
<div style="max-width: 420px; margin: 60px auto;">
    <div style="background: var(--card-bg); border-radius: 24px; padding: 40px; box-shadow: var(--shadow-lg);">
        <div style="text-align: center; margin-bottom: 32px;">
            <i class="fas fa-users" style="font-size: 64px; color: var(--primary);"></i>
            <h1 style="margin-top: 16px;">Регистрация</h1>
            <p style="color: var(--text-light);">Создайте новый аккаунт</p>
        </div>
        
        <form action="{{ url_for('register') }}" method="post">
            <div class="form-group">
                <input type="text" name="username" class="form-control" placeholder="Имя пользователя" required>
            </div>
            <div class="form-group">
                <input type="password" name="password" class="form-control" placeholder="Пароль (мин. 6 символов)" required minlength="6">
            </div>
            <div class="form-group">
                <input type="text" name="name" class="form-control" placeholder="Ваше имя (необязательно)">
            </div>
            <button type="submit" class="btn" style="width: 100%; padding: 14px; background: var(--secondary);">Зарегистрироваться</button>
        </form>
        
        <p style="margin-top: 24px; text-align: center;">
            Уже есть аккаунт? <a href="{{ url_for('login') }}" style="color: var(--primary); text-decoration: none;">Войдите</a>
        </p>
    </div>
</div>
{% endblock %}''')

    # Profile template
    with open(f'{templates_dir}/profile.html', 'w', encoding='utf-8') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
<div class="main-content">
    <div class="sidebar"></div>
    
    <div class="feed">
        {% if user %}
        <div class="post" style="text-align: center;">
            <img src="{% if user.avatar %}{{ url_for('static', filename='uploads/images/' + user.avatar) }}{% else %}https://via.placeholder.com/120{% endif %}" 
                 alt="Avatar" class="avatar" style="width: 120px; height: 120px;">
            <h1 style="margin-top: 16px;">{{ user.name or user.username }}</h1>
            <p style="color: var(--text-light);">@{{ user.username }}</p>
            {% if user.bio %}
                <p style="margin-top: 12px; max-width: 400px; margin-left: auto; margin-right: auto;">{{ user.bio }}</p>
            {% endif %}
            
            <div style="margin-top: 20px; display: flex; gap: 12px; justify-content: center;">
                {% if user.id == session['user_id'] %}
                    <a href="{{ url_for('home') }}" class="btn btn-outline"><i class="fas fa-edit"></i> Редактировать профиль</a>
                {% elif is_friend %}
                    <span class="btn btn-success"><i class="fas fa-check"></i> У вас в друзьях</span>
                {% elif friend_request_sent %}
                    <span class="btn"><i class="fas fa-clock"></i> Заявка отправлена</span>
                {% else %}
                    <form action="{{ url_for('add_friend') }}" method="post">
                        <input type="hidden" name="friend_username" value="{{ user.username }}">
                        <button type="submit" class="btn"><i class="fas fa-user-plus"></i> Добавить в друзья</button>
                    </form>
                {% endif %}
            </div>
        </div>

        <h2 style="margin: 24px 0 16px;">Посты пользователя</h2>
        {% for post in posts %}
        <div class="post">
            <div class="post-content">
                {{ post.content }}
            </div>
            
            {% if post.media_url %}
                {% if post.media_type == 'image' %}
                    <img src="{{ url_for('static', filename='uploads/images/' + post.media_url) }}" 
                         alt="Post media" class="post-media">
                {% elif post.media_type == 'video' %}
                    <video controls class="post-media" style="max-width: 100%; border-radius: 12px;">
                        <source src="{{ url_for('static', filename='uploads/videos/' + post.media_url) }}">
                    </video>
                {% endif %}
            {% endif %}
            
            <div style="color: var(--text-light); font-size: 12px; margin-top: 12px;">
                {{ post.created_at }}
            </div>
            
            <div class="post-actions">
                <span><i class="far fa-heart"></i> {{ post.likes_count or 0 }}</span>
                <span><i class="far fa-comment"></i> {{ post.comments_count or 0 }}</span>
            </div>
        </div>
        {% else %}
        <div class="post" style="text-align: center;">
            <p>Пользователь еще не опубликовал ни одного поста.</p>
        </div>
        {% endfor %}
        {% else %}
        <div class="post" style="text-align: center;">
            <p>Пользователь не найден.</p>
            <a href="{{ url_for('home') }}" class="btn">Вернуться на главную</a>
        </div>
        {% endif %}
    </div>
    
    <div class="right-sidebar"></div>
</div>
{% endblock %}''')

    # Communities template
    with open(f'{templates_dir}/communities.html', 'w', encoding='utf-8') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
<div class="main-content">
    <div class="sidebar">
        <div class="user-info-card">
            <h3><i class="fas fa-compass"></i> Обзор</h3>
            <p style="font-size: 13px; color: var(--text-light); margin-top: 8px;">Найдите сообщества по интересам</p>
        </div>
        <a href="{{ url_for('create_community') }}" class="btn btn-success" style="width: 100%; margin-top: 16px;">
            <i class="fas fa-plus"></i> Создать сообщество
        </a>
    </div>
    
    <div class="feed">
        <h1 style="margin-bottom: 8px;"><i class="fas fa-users"></i> Сообщества</h1>
        <p style="color: var(--text-light); margin-bottom: 24px;">Присоединяйтесь к сообществам по интересам или создавайте свои!</p>
        
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px;">
            {% for community in communities %}
            <div class="community-card">
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px;">
                    <img src="{% if community.avatar %}{{ url_for('static', filename='uploads/community_avatars/' + community.avatar) }}{% else %}https://via.placeholder.com/60{% endif %}" 
                         alt="Community" class="community-avatar">
                    <div>
                        <h3 style="margin: 0;"><a href="{{ url_for('community', community_id=community.id) }}" style="color: var(--text); text-decoration: none;">{{ community.name }}</a></h3>
                        <p style="margin: 4px 0 0 0; color: var(--text-light); font-size: 12px;">
                            <i class="fas fa-users"></i> {{ community.members_count }} участников
                        </p>
                    </div>
                </div>
                
                {% if community.description %}
                    <p style="margin: 8px 0; font-size: 13px; color: var(--text-light);">{{ community.description[:100] }}{% if community.description|length > 100 %}...{% endif %}</p>
                {% endif %}
                
                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 12px;">
                    <span style="font-size: 11px; color: var(--text-light);">
                        <i class="fas fa-crown"></i> {{ community.creator_name }}
                    </span>
                    {% if community.is_member %}
                        <a href="{{ url_for('community', community_id=community.id) }}" class="btn btn-outline" style="padding: 6px 12px; font-size: 12px;">
                            Открыть
                        </a>
                    {% else %}
                        <a href="{{ url_for('join_community', community_id=community.id) }}" class="btn btn-success" style="padding: 6px 12px; font-size: 12px;">
                            Присоединиться
                        </a>
                    {% endif %}
                </div>
            </div>
            {% else %}
            <div class="post" style="grid-column: 1/-1; text-align: center;">
                <i class="fas fa-users-slash" style="font-size: 48px; color: var(--text-light); margin-bottom: 16px;"></i>
                <p>Пока нет сообществ.</p>
                <a href="{{ url_for('create_community') }}" class="btn" style="margin-top: 16px;">Создать первое сообщество</a>
            </div>
            {% endfor %}
        </div>
    </div>
    
    <div class="right-sidebar">
        <h3><i class="fas fa-lightbulb"></i> Рекомендации</h3>
        <p style="color: var(--text-light); font-size: 13px; margin-top: 8px;">
            Присоединяйтесь к активным сообществам, чтобы находить новых друзей и делиться интересами!
        </p>
        <div style="margin-top: 20px;">
            <i class="fas fa-rocket" style="font-size: 48px; color: var(--primary); opacity: 0.5; display: block; text-align: center;"></i>
        </div>
    </div>
</div>
{% endblock %}''')

    # Create community template
    with open(f'{templates_dir}/create_community.html', 'w', encoding='utf-8') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
<div style="max-width: 600px; margin: 50px auto;">
    <div style="background: var(--card-bg); border-radius: 24px; padding: 32px; box-shadow: var(--shadow-lg);">
        <div style="text-align: center; margin-bottom: 32px;">
            <i class="fas fa-plus-circle" style="font-size: 48px; color: var(--primary);"></i>
            <h1 style="margin-top: 16px;">Создать сообщество</h1>
            <p style="color: var(--text-light);">Объединяйте людей вокруг общих интересов</p>
        </div>
        
        <form action="{{ url_for('create_community') }}" method="post" enctype="multipart/form-data">
            <div class="form-group">
                <label style="font-weight: 500;">Название сообщества <span style="color: var(--danger);">*</span></label>
                <input type="text" name="name" class="form-control" required maxlength="100" placeholder="Например: Любители кофе">
            </div>
            
            <div class="form-group">
                <label style="font-weight: 500;">Описание</label>
                <textarea name="description" class="form-control" rows="4" placeholder="Расскажите о вашем сообществе..."></textarea>
            </div>
            
            <div class="form-group">
                <label style="font-weight: 500;">Аватар сообщества</label>
                <input type="file" name="avatar" accept="image/*" class="form-control">
                <small style="color: var(--text-light);">Рекомендуемый размер: 200x200 пикселей</small>
            </div>
            
            <div class="form-group">
                <label style="font-weight: 500;">Фоновое изображение</label>
                <input type="file" name="background" accept="image/*" class="form-control">
                <small style="color: var(--text-light);">Рекомендуемый размер: 1200x400 пикселей</small>
            </div>
            
            <div class="form-group">
                <label style="display: flex; align-items: center; cursor: pointer;">
                    <input type="checkbox" name="is_channel" style="margin-right: 12px; width: 18px; height: 18px;">
                    <span style="font-weight: 500;">Это канал</span>
                </label>
                <small style="color: var(--text-light);">В канале только администраторы могут публиковать посты</small>
            </div>
            
            <div style="display: flex; gap: 12px; margin-top: 24px;">
                <button type="submit" class="btn" style="flex: 1;"><i class="fas fa-check"></i> Создать сообщество</button>
                <a href="{{ url_for('communities') }}" class="btn btn-outline" style="flex: 1; text-align: center;">Отмена</a>
            </div>
        </form>
    </div>
</div>
{% endblock %}''')

    # Community template
    with open(f'{templates_dir}/community.html', 'w', encoding='utf-8') as f:
        f.write('''{% extends "base.html" %}

{% block content %}
{% if community %}
<div class="community-header" style="background: linear-gradient(135deg, var(--primary), var(--primary-dark)); color: white; padding: 40px 0; border-radius: 20px; margin-bottom: 24px;">
    <div class="container" style="display: flex; align-items: center; gap: 24px;">
        <img src="{% if community.avatar %}{{ url_for('static', filename='uploads/community_avatars/' + community.avatar) }}{% else %}https://via.placeholder.com/100{% endif %}" 
             alt="Community" style="width: 100px; height: 100px; border-radius: 20px; object-fit: cover; border: 3px solid white;">
        <div>
            <h1 style="margin: 0;">{{ community.name }}</h1>
            {% if community.is_channel %}
                <span style="background: rgba(255,255,255,0.2); padding: 4px 12px; border-radius: 20px; font-size: 12px; display: inline-block; margin-top: 8px;">
                    <i class="fas fa-broadcast-tower"></i> Канал
                </span>
            {% endif %}
            <p style="margin: 12px 0 0 0; opacity: 0.9;"><i class="fas fa-users"></i> {{ community.members_count }} участников</p>
        </div>
    </div>
</div>

<div class="main-content">
    <div class="sidebar">
        <h3><i class="fas fa-users"></i> Участники</h3>
        <div style="max-height: 500px; overflow-y: auto;">
            {% for member in members %}
            <div class="friend-item">
                <img src="{% if member.avatar %}{{ url_for('static', filename='uploads/images/' + member.avatar) }}{% else %}https://via.placeholder.com/40{% endif %}" 
                     alt="Avatar" class="friend-avatar">
                <div>
                    <div><a href="{{ url_for('profile', username=member.username) }}" style="color: var(--text); text-decoration: none;">{{ member.name or member.username }}</a></div>
                    <div style="font-size: 11px; color: var(--text-light);">
                        {% if member.role == 'admin' %}
                            <i class="fas fa-crown" style="color: var(--accent);"></i> Администратор
                        {% else %}
                            Участник
                        {% endif %}
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    
    <div class="feed">
        {% if community.description %}
            <div class="post">
                <h3><i class="fas fa-info-circle"></i> О сообществе</h3>
                <p style="margin-top: 12px; line-height: 1.6;">{{ community.description }}</p>
                <div style="display: flex; gap: 12px; margin-top: 20px;">
                    {% if community.is_member %}
                        {% if community.user_role == 'admin' or not community.is_channel %}
                            <button onclick="document.getElementById('post-form').style.display='block'" class="btn">
                                <i class="fas fa-edit"></i> Написать пост
                            </button>
                        {% endif %}
                        {% if community.user_role != 'admin' %}
                            <a href="{{ url_for('leave_community', community_id=community.id) }}" class="btn btn-danger" onclick="return confirm('Вы уверены, что хотите покинуть сообщество?')">
                                <i class="fas fa-sign-out-alt"></i> Покинуть
                            </a>
                        {% endif %}
                    {% else %}
                        <a href="{{ url_for('join_community', community_id=community.id) }}" class="btn">
                            <i class="fas fa-plus"></i> Присоединиться
                        </a>
                    {% endif %}
                </div>
            </div>
        {% endif %}
        
        {% if community.is_member and (community.user_role == 'admin' or not community.is_channel) %}
        <div class="post" id="post-form" style="display: {% if posts %}none{% else %}block{% endif %};">
            <h3><i class="fas fa-pen"></i> Новый пост</h3>
            <form action="{{ url_for('create_community_post', community_id=community.id) }}" method="post" enctype="multipart/form-data">
                <textarea name="content" class="form-control" rows="3" placeholder="Что нового в сообществе?" required></textarea>
                <div style="margin-top: 12px; display: flex; justify-content: space-between; align-items: center;">
                    <label style="cursor: pointer; background: var(--bg); padding: 8px 16px; border-radius: 8px;">
                        <i class="fas fa-image"></i> Прикрепить файл
                        <input type="file" name="media" accept="image/*,video/*" style="display: none;">
                    </label>
                    <button type="submit" class="btn">Опубликовать</button>
                </div>
            </form>
        </div>
        {% endif %}
        
        {% for post in posts %}
        <div class="post">
            <div class="post-header">
                <img src="{% if post.avatar %}{{ url_for('static', filename='uploads/images/' + post.avatar) }}{% else %}https://via.placeholder.com/48{% endif %}" 
                     alt="Avatar" class="avatar">
                <div>
                    <strong><a href="{{ url_for('profile', username=post.username) }}" style="color: var(--text); text-decoration: none;">{{ post.name or post.username }}</a></strong>
                    <div style="color: var(--text-light); font-size: 12px;">
                        {{ post.created_at }}
                    </div>
                </div>
            </div>
            
            <div class="post-content">
                {{ post.content }}
            </div>
            
            {% if post.media_url %}
                {% if post.media_type == 'image' %}
                    <img src="{{ url_for('static', filename='uploads/images/' + post.media_url) }}" 
                         alt="Post media" class="post-media" onclick="window.open(this.src)">
                {% elif post.media_type == 'video' %}
                    <video controls class="post-media video-player">
                        <source src="{{ url_for('static', filename='uploads/videos/' + post.media_url) }}">
                        Ваш браузер не поддерживает видео.
                    </video>
                {% endif %}
            {% endif %}
            
            <div class="post-actions">
                <button class="action-btn like-btn {% if post.liked %}liked{% endif %}" data-post-id="{{ post.id }}" data-community="true">
                    <i class="{% if post.liked %}fas{% else %}far{% endif %} fa-heart"></i> Нравится
                </button>
                <span>{{ post.likes_count or 0 }}</span>
                
                <button class="action-btn comment-btn" data-post-id="{{ post.id }}">
                    <i class="far fa-comment"></i> Комментировать
                </button>
                <span>{{ post.comments_count or 0 }}</span>
            </div>
        </div>
        {% else %}
        <div class="post" style="text-align: center;">
            <i class="fas fa-comments" style="font-size: 48px; color: var(--text-light); margin-bottom: 16px;"></i>
            <p>В этом сообществе пока нет постов.</p>
            {% if community.is_member and (community.user_role == 'admin' or not community.is_channel) %}
                <button onclick="document.getElementById('post-form').style.display='block'" class="btn" style="margin-top: 16px;">
                    Написать первый пост
                </button>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    
    <div class="right-sidebar">
        <h3><i class="fas fa-chart-line"></i> Статистика</h3>
        <div style="background: var(--bg); border-radius: 12px; padding: 16px;">
            <p><strong><i class="fas fa-user"></i> Создатель:</strong> {{ community.creator_name }}</p>
            <p><strong><i class="fas fa-users"></i> Участников:</strong> {{ community.members_count }}</p>
            <p><strong><i class="fas fa-tag"></i> Тип:</strong> {{ 'Канал' if community.is_channel else 'Сообщество' }}</p>
            {% if community.is_member %}
                <p><strong><i class="fas fa-user-tag"></i> Ваша роль:</strong> {{ community.user_role }}</p>
            {% endif %}
        </div>
    </div>
</div>

<script>
    function togglePostForm() {
        const form = document.getElementById('post-form');
        form.style.display = form.style.display === 'none' ? 'block' : 'none';
    }
</script>
{% else %}
<div class="post" style="text-align: center;">
    <i class="fas fa-question-circle" style="font-size: 48px; color: var(--text-light); margin-bottom: 16px;"></i>
    <p>Сообщество не найдено.</p>
    <a href="{{ url_for('communities') }}" class="btn" style="margin-top: 16px;">Вернуться к сообществам</a>
</div>
{% endif %}
{% endblock %}''')

if __name__ == '__main__':
    create_favicon()
    init_db()
    create_templates()
    
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)