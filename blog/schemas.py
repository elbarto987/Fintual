from datetime import datetime

from ninja import Schema


class AuthorOut(Schema):
    id: int
    username: str
    display_name: str


class TagOut(Schema):
    id: int
    name: str
    slug: str


class PostListOut(Schema):
    id: int
    title: str
    author: AuthorOut
    tags: list[TagOut]
    view_count: int
    created_at: datetime


class CursorPaginatedPostListOut(Schema):
    page_size: int
    results: list[PostListOut]
    next_cursor: str | None
    has_next: bool


class CommentOut(Schema):
    id: int
    author: AuthorOut
    body: str
    created_at: datetime


class PostDetailOut(Schema):
    id: int
    title: str
    body: str
    author: AuthorOut
    tags: list[TagOut]
    comments: list[CommentOut]
    view_count: int
    created_at: datetime
    updated_at: datetime


class UserDetailOut(Schema):
    id: int
    username: str
    display_name: str
    email: str
    bio: str
    post_count: int
    comment_count: int


class PostCreateIn(Schema):
    author_id: int
    title: str
    body: str
    tag_slugs: list[str] = []


class PostCreateOut(Schema):
    id: int
    title: str


class CommentCreateIn(Schema):
    author_id: int
    body: str


class CommentCreateOut(Schema):
    id: int
