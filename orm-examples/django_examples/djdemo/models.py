"""
Django models for the seeded social schema. managed = False tells Django
these tables already exist, so migrations never create or change them.
In your own app, your models are managed normally.
"""
from django.db import models


class User(models.Model):
    email = models.CharField(max_length=320)

    class Meta:
        managed = False
        db_table = "users"


class Post(models.Model):
    author = models.ForeignKey(User, models.DO_NOTHING, db_column="author_id", related_name="posts")
    body = models.TextField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "posts"


class Friendship(models.Model):
    user = models.ForeignKey(User, models.DO_NOTHING, db_column="user_id", related_name="friendships")
    friend = models.ForeignKey(User, models.DO_NOTHING, db_column="friend_id", related_name="friend_of")

    class Meta:
        managed = False
        db_table = "friendships"


class Like(models.Model):
    user = models.ForeignKey(User, models.DO_NOTHING, db_column="user_id", related_name="likes")
    post = models.ForeignKey(Post, models.DO_NOTHING, db_column="post_id", related_name="likes")
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "likes"


class PostStats(models.Model):
    """A database VIEW (see db_objects.sql), read like any other model."""
    post = models.OneToOneField(Post, models.DO_NOTHING, primary_key=True, db_column="post_id")
    author_id = models.IntegerField()
    created_at = models.DateTimeField()
    like_count = models.IntegerField()
    last_liked_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "post_stats"
