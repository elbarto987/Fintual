import base64
import json

from django.contrib.postgres.search import SearchQuery
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError

from blog.models import Comment, Post, Tag, User
from blog.schemas import (
    CommentCreateIn,
    CommentCreateOut,
    PostCreateIn,
    PostCreateOut,
    PostDetailOut,
    CursorPaginatedPostListOut,
    UserDetailOut,
)

router = Router()


def _serialize_author(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
    }


def _serialize_tag(tag: Tag) -> dict:
    return {"id": tag.id, "name": tag.name, "slug": tag.slug}


def _serialize_post_list(post: Post) -> dict:
    return {
        "id": post.id,
        "title": post.title,
        "author": _serialize_author(post.author),
        "tags": [_serialize_tag(t) for t in post.tags.all()],
        "view_count": post.view_count,
        "created_at": post.created_at,
    }


def _decode_cursor(cursor: str | None) -> tuple[str, int] | None:
    if not cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        payload = json.loads(decoded)
        return payload["created_at"], int(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _encode_cursor(post: Post) -> str:
    payload = {"created_at": post.created_at.isoformat(), "id": post.id}
    encoded = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(encoded).decode()


def _paginate_posts(posts: QuerySet, cursor: str | None, page_size: int) -> dict:
    decoded_cursor = _decode_cursor(cursor)
    if decoded_cursor:
        created_at, post_id = decoded_cursor
        posts = posts.filter(
            Q(created_at__lt=created_at)
            | Q(created_at=created_at, id__lt=post_id)
        )

    page_posts = list(posts[: page_size + 1])
    has_next = len(page_posts) > page_size
    page_posts = page_posts[:page_size]
    return {
        "page_size": page_size,
        "results": [_serialize_post_list(post) for post in page_posts],
        "next_cursor": _encode_cursor(page_posts[-1]) if has_next else None,
        "has_next": has_next,
    }


def _reject_offset_pagination(request) -> None:
    if "page" in request.GET:
        raise HttpError(
            400,
            "This endpoint uses cursor pagination. Use the cursor returned in next_cursor.",
        )


@router.get("/posts", response=CursorPaginatedPostListOut)
def list_posts(
    request,
    cursor: str | None = None,
    page_size: int = Query(20, ge=1, le=100),
):
    _reject_offset_pagination(request)
    posts = (
        Post.objects.filter(is_published=True)
        .select_related("author")
        .prefetch_related("tags")
        .order_by("-created_at", "-id")
    )
    return _paginate_posts(posts, cursor, page_size)


@router.get("/posts/search", response=CursorPaginatedPostListOut)
def search_posts(
    request,
    q: str,
    cursor: str | None = None,
    page_size: int = Query(20, ge=1, le=100),
):
    _reject_offset_pagination(request)
    posts = (
        Post.objects.filter(
            search_vector=SearchQuery(q, config="english"),
            is_published=True,
        )
        .select_related("author")
        .prefetch_related("tags")
        .order_by("-created_at", "-id")
    )
    return _paginate_posts(posts, cursor, page_size)


@router.get("/posts/by-tag/{slug}", response=CursorPaginatedPostListOut)
def posts_by_tag(
    request,
    slug: str,
    cursor: str | None = None,
    page_size: int = Query(20, ge=1, le=100),
):
    _reject_offset_pagination(request)
    tag = get_object_or_404(Tag, slug=slug)
    posts = (
        tag.posts.filter(is_published=True)
        .select_related("author")
        .prefetch_related("tags")
        .order_by("-created_at", "-id")
    )
    return _paginate_posts(posts, cursor, page_size)


@router.get("/posts/{post_id}", response=PostDetailOut)
def get_post(request, post_id: int):
    post = get_object_or_404(
        Post.objects.select_related("author").prefetch_related(
            "tags", "comments__author"
        ),
        id=post_id,
    )
    post.view_count += 1
    post.save()

    comments = [
        {
            "id": c.id,
            "author": _serialize_author(c.author),
            "body": c.body,
            "created_at": c.created_at,
        }
        for c in post.comments.order_by("created_at")
    ]
    return {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "author": _serialize_author(post.author),
        "tags": [_serialize_tag(t) for t in post.tags.all()],
        "comments": comments,
        "view_count": post.view_count,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


@router.post("/posts", response=PostCreateOut)
def create_post(request, payload: PostCreateIn):
    author = get_object_or_404(User, id=payload.author_id)
    post = Post.objects.create(
        author=author,
        title=payload.title,
        body=payload.body,
    )
    for slug in payload.tag_slugs:
        tag = Tag.objects.get(slug=slug)
        post.tags.add(tag)
    return {"id": post.id, "title": post.title}


@router.post("/posts/{post_id}/comments", response=CommentCreateOut)
def create_comment(request, post_id: int, payload: CommentCreateIn):
    post = get_object_or_404(Post, id=post_id)
    author = get_object_or_404(User, id=payload.author_id)
    comment = Comment.objects.create(post=post, author=author, body=payload.body)
    return {"id": comment.id}


@router.get("/users/find", response=UserDetailOut)
def find_user_by_email(request, email: str):
    user = get_object_or_404(User, email=email)
    return _user_detail(user)


@router.get("/users/{user_id}", response=UserDetailOut)
def get_user(request, user_id: int):
    user = get_object_or_404(User, id=user_id)
    return _user_detail(user)


def _user_detail(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "bio": user.bio,
        "post_count": user.posts.count(),
        "comment_count": user.comments.count(),
    }
