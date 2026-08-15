# -*- coding: utf-8 -*-
"""社区帖子模块：发布 / 列表 / 详情 / 点赞 / 评论 / 删除
接口与原 Node 版兼容：/api/posts
"""
from flask import Blueprint, g, jsonify, request

from . import db
from .auth import auth_required, _try_auth

router = Blueprint('posts', __name__, url_prefix='/api/posts')


def public_user(u):
    return {'id': u['id'], 'username': u['username'] or None,
            'nickname': u['nickname'], 'avatar': u['avatar']}


def public_post(row, viewer_id=None):
    author = db.query_one('SELECT * FROM users WHERE id = ?', (row['user_id'],))
    like_count = db.query_one('SELECT COUNT(*) AS n FROM post_likes WHERE post_id = ?', (row['id'],))['n']
    comment_count = db.query_one('SELECT COUNT(*) AS n FROM post_comments WHERE post_id = ?', (row['id'],))['n']
    liked = False
    if viewer_id:
        liked = db.query_one('SELECT 1 AS x FROM post_likes WHERE post_id = ? AND user_id = ?',
                             (row['id'], viewer_id)) is not None
    images = row['images'].split(',') if row['images'] else []
    return {
        'id': row['id'],
        'author': public_user(author) if author else {'id': row['user_id'], 'username': None, 'nickname': '已注销', 'avatar': ''},
        'title': row['title'],
        'content': row['content'],
        'images': images,
        'likeCount': like_count,
        'commentCount': comment_count,
        'liked': liked,
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def public_comment(row):
    author = db.query_one('SELECT * FROM users WHERE id = ?', (row['user_id'],))
    return {
        'id': row['id'],
        'postId': row['post_id'],
        'author': public_user(author) if author else {'id': row['user_id'], 'username': None, 'nickname': '已注销', 'avatar': ''},
        'content': row['content'],
        'createdAt': row['created_at'],
    }


# ============ 发帖 ============
@router.post('')
@auth_required
def create_post():
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    images = data.get('images') or []
    if not title or not content:
        return jsonify({'error': '标题和内容不能为空'}), 400
    if len(title) > 100 or len(content) > 5000:
        return jsonify({'error': '标题最长 100 字，内容最长 5000 字'}), 400
    images_str = ','.join(str(i) for i in images[:9])
    last_id, _ = db.execute(
        'INSERT INTO posts (user_id, title, content, images) VALUES (?, ?, ?, ?)',
        (g.user['id'], title, content, images_str))
    row = db.query_one('SELECT * FROM posts WHERE id = ?', (last_id,))
    return jsonify({'post': public_post(row, g.user['id'])}), 201


# ============ 帖子列表 ============
@router.get('')
def list_posts():
    viewer = _try_auth()
    viewer_id = viewer['id'] if viewer else None
    page = max(1, int(request.args.get('page', '1') or 1))
    page_size = min(50, max(1, int(request.args.get('pageSize', '10') or 10)))
    rows = db.query('SELECT * FROM posts WHERE deleted = 0 ORDER BY id DESC LIMIT ? OFFSET ?',
                    (page_size, (page - 1) * page_size))
    total = db.query_one('SELECT COUNT(*) AS n FROM posts WHERE deleted = 0')['n']
    return jsonify({'posts': [public_post(r, viewer_id) for r in rows],
                    'total': total, 'page': page, 'pageSize': page_size})


# ============ 帖子详情 ============
@router.get('/<int:pid>')
def post_detail(pid):
    viewer = _try_auth()
    viewer_id = viewer['id'] if viewer else None
    row = db.query_one('SELECT * FROM posts WHERE id = ? AND deleted = 0', (pid,))
    if not row:
        return jsonify({'error': '帖子不存在'}), 404
    comments = db.query('SELECT * FROM post_comments WHERE post_id = ? ORDER BY id ASC', (pid,))
    return jsonify({'post': public_post(row, viewer_id),
                    'comments': [public_comment(c) for c in comments]})


# ============ 点赞 / 取消点赞 ============
@router.post('/<int:pid>/like')
@auth_required
def like_post(pid):
    row = db.query_one('SELECT * FROM posts WHERE id = ? AND deleted = 0', (pid,))
    if not row:
        return jsonify({'error': '帖子不存在'}), 404
    exists = db.query_one('SELECT 1 AS x FROM post_likes WHERE post_id = ? AND user_id = ?',
                          (pid, g.user['id']))
    if exists:
        db.execute('DELETE FROM post_likes WHERE post_id = ? AND user_id = ?', (pid, g.user['id']))
        db.execute('UPDATE posts SET likes = GREATEST(likes - 1, 0) WHERE id = ?', (pid,))
        liked = False
    else:
        db.execute('INSERT INTO post_likes (post_id, user_id) VALUES (?, ?)', (pid, g.user['id']))
        db.execute('UPDATE posts SET likes = likes + 1 WHERE id = ?', (pid,))
        liked = True
    count = db.query_one('SELECT COUNT(*) AS n FROM post_likes WHERE post_id = ?', (pid,))['n']
    return jsonify({'liked': liked, 'likeCount': count})


# ============ 评论 ============
@router.post('/<int:pid>/comments')
@auth_required
def add_comment(pid):
    row = db.query_one('SELECT * FROM posts WHERE id = ? AND deleted = 0', (pid,))
    if not row:
        return jsonify({'error': '帖子不存在'}), 404
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': '评论内容不能为空'}), 400
    if len(content) > 1000:
        return jsonify({'error': '评论最长 1000 字'}), 400
    last_id, _ = db.execute('INSERT INTO post_comments (post_id, user_id, content) VALUES (?, ?, ?)',
                            (pid, g.user['id'], content))
    c = db.query_one('SELECT * FROM post_comments WHERE id = ?', (last_id,))
    return jsonify({'comment': public_comment(c)}), 201


# ============ 我的帖子 ============
@router.get('/mine/list')
@auth_required
def my_posts():
    rows = db.query('SELECT * FROM posts WHERE user_id = ? AND deleted = 0 ORDER BY id DESC', (g.user['id'],))
    return jsonify({'posts': [public_post(r, g.user['id']) for r in rows]})


# ============ 删除帖子（作者或管理员） ============
@router.delete('/<int:pid>')
@auth_required
def delete_post(pid):
    row = db.query_one('SELECT * FROM posts WHERE id = ? AND deleted = 0', (pid,))
    if not row:
        return jsonify({'error': '帖子不存在'}), 404
    if row['user_id'] != g.user['id'] and (g.user['role'] or 'user') != 'admin':
        return jsonify({'error': '只能删除自己的帖子'}), 403
    db.execute('UPDATE posts SET deleted = 1 WHERE id = ?', (pid,))
    return jsonify({'ok': True})
