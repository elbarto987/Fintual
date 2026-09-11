from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone


class User(models.Model):
    username = models.CharField(max_length=64, unique=True)
    email = models.CharField(max_length=255, db_index=True)
    display_name = models.CharField(max_length=128)
    bio = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.username


class Tag(models.Model):
    name = models.CharField(max_length=64, unique=True)
    slug = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.name


class Post(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posts")
    title = models.CharField(max_length=255)
    body = models.TextField()
    is_published = models.BooleanField(default=True)
    view_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    tags = models.ManyToManyField(Tag, related_name="posts", blank=True)

    # Maintained by a Postgres trigger (see migration 0002), NOT by Django.
    # Kept null=True/editable=False because the app should never write to
    # this column directly — the DB owns it and keeps it consistent even
    # for bulk_create/bulk_update/raw SQL, which bypass Django signals.
    search_vector = SearchVectorField(null=True, editable=False)

    class Meta:
        indexes = [
            GinIndex(fields=["search_vector"], name="post_search_vector_gin"),
            models.Index(
                fields=["is_published", "-created_at", "-id"],
                name="post_pub_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="comments")
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
