import pytest
from django.test import Client

from blog.models import Post, Tag, User


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
    )


@pytest.mark.django_db
def test_list_posts_returns_published(client, user):
    tag = Tag.objects.create(name="Python", slug="python")
    post = Post.objects.create(author=user, title="Hello", body="World")
    post.tags.add(tag)
    Post.objects.create(author=user, title="Draft", body="...", is_published=False)

    response = client.get("/api/posts")

    assert response.status_code == 200
    data = response.json()
    titles = [p["title"] for p in data["results"]]
    assert "Hello" in titles
    assert "Draft" not in titles


@pytest.mark.django_db
def test_get_post_returns_detail(client, user):
    post = Post.objects.create(author=user, title="Hello", body="World")

    response = client.get(f"/api/posts/{post.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Hello"
    assert data["author"]["username"] == "alice"
    assert data["comments"] == []