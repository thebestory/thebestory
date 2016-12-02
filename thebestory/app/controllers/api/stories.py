"""
The Bestory Project
"""

import json
from collections import OrderedDict
from datetime import datetime

import pytz
from aiohttp import web
from sqlalchemy.sql import insert, select, update
from sqlalchemy.sql.expression import func

from thebestory.app.lib import identifier, listing
from thebestory.app.lib.api.response import *
from thebestory.app.models import comments, stories, topics, users, story_likes

# User ID
ANONYMOUS_USER_ID = 5

# User ID
THEBESTORY_USER_ID = 2


class StoriesController:
    # 25 stories per page
    listing = listing.Listing(1, 100, 25)

    async def details(self, request):
        """
        Returns the story info.
        """
        try:
            id = identifier.from36(request.match_info["id"])
        except (KeyError, ValueError):
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(2003)))

        async with request.db.acquire() as conn:
            story = await conn.fetchrow(
                select([stories]).where(stories.c.id == id))

            if story is None or story.is_removed:
                return web.Response(
                    status=404,
                    content_type="application/json",
                    text=json.dumps(error(2003)))

            if not story.is_approved:
                return web.Response(
                    status=403,
                    content_type="application/json",
                    text=json.dumps(error(4001)))

            topic = await conn.fetchrow(
                select([topics]).where(topics.c.id == story.topic_id))

        data = {
            "id": identifier.to36(story.id),
            "topic": {"id": story.topic_id} if topic is None else {
                "id": topic.id,
                "slug": topic.slug,
                "title": topic.title,
                "description": topic.description,
                "icon": topic.icon,
                "stories_count": topic.stories_count
            },
            "content": story.content,
            "likes_count": story.likes_count,
            "comments_count": story.comments_count,
            # "edited_date": story.edited_date.isoformat(),
            "published_date": story.published_date.isoformat()
        }

        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(
                ok(data) if topic is not None else warning(2002, data)))

    async def like(self, request):
        """
        Likes the story.
        """
        return await self._like(request, True)

    async def unlike(self, request):
        """
        Unlikes the story.
        """
        return await self._like(request, False)

    async def comments(self, request):
        """
        Returns comments for the story.
        """
        try:
            id = identifier.from36(request.match_info["id"])
        except (KeyError, ValueError):
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(2003)))

        # TODO: WTF is this? Rewrite via a cte query with depth
        async with request.db.acquire() as conn:
            story = await conn.fetchrow(
                select([
                    stories.c.is_approved,
                    stories.c.is_removed
                ]).where(stories.c.id == id))

            # Comments is not available, if story is not exists, removed or
            # not approved yet.

            if story is None or story.is_removed:
                return web.Response(
                    status=404,
                    content_type="application/json",
                    text=json.dumps(error(2003)))

            if not story.is_approved:
                return web.Response(
                    status=403,
                    content_type="application/json",
                    text=json.dumps(error(4001)))

            comments_ = await conn.fetch(
                select([comments, users.c.username.label("author_username")])
                    .where(users.c.id == comments.c.author_id)
                    .where(comments.c.story_id == id)
                    .order_by(comments.c.likes_count.desc()))

        data = OrderedDict(
            [
                (
                    identifier.to36(comment.id),
                    {
                        "id": identifier.to36(comment.id),
                        "parent": None if comment.parent_id is None else {
                            "id": identifier.to36(comment.parent_id),
                        },
                        "author": {
                            "id": comment.author_id,
                            "username": comment.author_username
                        },
                        "content": comment.content,
                        "comments": [],
                        "likes_count": comment.likes_count,
                        "comments_count": comment.comments_count,
                        "submitted_date": comment.submitted_date.isoformat(),
                        "edited_date": comment.edited_date.isoformat() if comment.edited_date else None
                    }
                ) for comment in comments_]
        )

        for comment in data.values():
            if comment["parent"] is not None:
                data[comment["parent"]["id"]]["comments"].append(comment)

        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(
                ok(list(filter(lambda c: c["parent"] is None, data.values())))))

    async def latest(self, request):
        """
        Returns the list of last published stories.
        Listings are supported.
        """
        try:
            pivot, limit, direction = self.listing.validate(
                request.url.query.get("before", None),
                request.url.query.get("after", None),
                request.url.query.get("limit", None))
        except ValueError:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(3001)))

        data = []

        query = select([
            stories.c.id,
            stories.c.topic_id,
            stories.c.content,
            stories.c.likes_count,
            stories.c.comments_count,
            # stories.c.edited_date,
            stories.c.published_date
        ]) \
            .where(stories.c.is_approved == True) \
            .where(stories.c.is_removed == False) \
            .order_by(stories.c.published_date.desc()) \
            .limit(limit)

        # if pivot is none, fetch first page w/o any parameters
        if pivot is not None:
            if direction == listing.Direction.BEFORE:
                query = query.where(stories.c.id > pivot)
            elif direction == listing.Direction.AFTER:
                query = query.where(stories.c.id < pivot)

        async with request.db.acquire() as conn:
            for story in await conn.fetch(query):
                data.append({
                    "id": identifier.to36(story.id),
                    "topic": {
                        "id": story.topic_id
                    },
                    "content": story.content,
                    "likes_count": story.likes_count,
                    "comments_count": story.comments_count,
                    # "edited_date": story.edited_date.isoformat(),
                    "published_date": story.published_date.isoformat()
                })

        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(ok(data)))

    async def hot(self, request):
        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(error(4001)))

    async def top(self, request):
        """
        Returns the list of top stories.
        Listings are supported.
        """
        try:
            pivot, limit, direction = self.listing.validate(
                request.url.query.get("before", None),
                request.url.query.get("after", None),
                request.url.query.get("limit", None))
        except ValueError:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(3001)))

        data = []

        query = select([
            stories.c.id,
            stories.c.topic_id,
            stories.c.content,
            stories.c.likes_count,
            stories.c.comments_count,
            # stories.c.edited_date,
            stories.c.published_date
        ]) \
            .where(stories.c.is_approved == True) \
            .where(stories.c.is_removed == False) \
            .order_by(stories.c.likes_count.desc()) \
            .limit(limit)

        # if pivot is none, fetch first page w/o any parameters
        if pivot is not None:
            if direction == listing.Direction.BEFORE:
                query = query.where(stories.c.id > pivot)
            elif direction == listing.Direction.AFTER:
                query = query.where(stories.c.id < pivot)

        async with request.db.acquire() as conn:
            for story in await conn.fetch(query):
                data.append({
                    "id": identifier.to36(story.id),
                    "topic": {
                        "id": story.topic_id
                    },
                    "content": story.content,
                    "likes_count": story.likes_count,
                    "comments_count": story.comments_count,
                    # "edited_date": story.edited_date.isoformat(),
                    "published_date": story.published_date.isoformat()
                })

        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(ok(data)))

    async def random(self, request):
        """
        Returns the list of random stories.
        """
        try:
            limit = self.listing.validate_limit(
                request.url.query.get("limit", None))
        except ValueError:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(3001)))

        data = []

        query = select([
            stories.c.id,
            stories.c.topic_id,
            stories.c.content,
            stories.c.likes_count,
            stories.c.comments_count,
            # stories.c.edited_date,
            stories.c.published_date
        ]) \
            .where(stories.c.is_approved == True) \
            .where(stories.c.is_removed == False) \
            .order_by(func.random()) \
            .limit(limit)

        async with request.db.acquire() as conn:
            for story in await conn.fetch(query):
                data.append({
                    "id": identifier.to36(story.id),
                    "topic": {
                        "id": story.topic_id
                    },
                    "content": story.content,
                    "likes_count": story.likes_count,
                    "comments_count": story.comments_count,
                    # "edited_date": story.edited_date.isoformat(),
                    "published_date": story.published_date.isoformat()
                })

        return web.Response(
            status=200,
            content_type="application/json",
            text=json.dumps(data))

    # TODO: Auth required
    async def submit(self, request):
        """
        Sumbits a new story.
        """
        # Parses the content and ID of topic
        try:
            body = await request.json()
            topic_id = int(body["topic"])
            content = body["content"]
        except (KeyError, ValueError):
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(2002)))

        # Content checks
        if len(content) <= 0:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(5004)))
        elif len(content) > stories.c.content.type.length:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(5006)))

        # TODO: Block some ascii graphics, and other unwanted symbols...

        # Getting topic, where comment is submitting
        async with request.db.acquire() as conn:
            topic = await conn.fetchrow(
                select([topics]).where(topics.c.id == topic_id))

        # Checks, if topic is present for comment submitting
        if topic is None:
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(2002)))

        async with request.db.transaction() as conn:
            # TODO: Rewrite, when asyncpgsa replaces nulls with default values

            story_id = await conn.fetchval(insert(stories).values(
                author_id=ANONYMOUS_USER_ID,
                topic_id=topic.id,
                content=content,
                likes_count=0,
                comments_count=0,
                is_approved=False,
                is_removed=False,
                submitted_date=datetime.utcnow().replace(tzinfo=pytz.utc)
            ))

            await conn.execute(
                update(topics)
                    .where(topics.c.id == topic.id)
                    .values(stories_count=topics.c.stories_count + 1))

            await conn.execute(
                update(users)
                    .where(users.c.id == ANONYMOUS_USER_ID)
                    .values(stories_count=topics.c.stories_count + 1))

        # Is story committed actually?
        if story_id is not None:
            async with request.db.acquire() as conn:
                story = await conn.fetchrow(
                    select([stories]).where(stories.c.id == story_id))

            if story is None:
                return web.Response(
                    status=500,
                    content_type="application/json",
                    text=json.dumps(error(1004)))

            data = {
                "id": identifier.to36(story.id),
                "topic": None if topic is None else {
                    "id": topic.id,
                    "slug": topic.slug,
                    "title": topic.title,
                    "description": topic.description,
                    "icon": topic.icon,
                    "stories_count": topic.stories_count
                },
                "content": story.content,
                "likes_count": story.likes_count,
                "comments_count": story.comments_count,
                # "edited_date": story.edited_date.isoformat(),
                "submitted_date": story.submitted_date.isoformat(),
                "published_date": None
            }

            return web.Response(
                status=201,
                content_type="application/json",
                text=json.dumps(ok(data)))
        else:
            return web.Response(
                status=500,
                content_type="application/json",
                text=json.dumps(error(1004)))

    @staticmethod
    async def _like(request, state: bool):
        try:
            id = identifier.from36(request.match_info["id"])
        except (KeyError, ValueError):
            return web.Response(
                status=400,
                content_type="application/json",
                text=json.dumps(error(2003)))

        diff = 1 if state is True else -1

        async with request.db.acquire() as conn:
            story = await conn.fetchrow(
                select([stories]).where(stories.c.id == id))

            if story is None or story.is_removed or not story.is_approved:
                return web.Response(
                    status=404,
                    content_type="application/json",
                    text=json.dumps(error(2003)))

            like = await conn.fetchrow(
                select([story_likes])
                    .where(story_likes.c.user_id == ANONYMOUS_USER_ID)
                    .where(story_likes.c.story_id == story.id)
                    .order_by(story_likes.c.timestamp.desc()))

        if like is None or like.state != state:
            async with request.db.transaction() as conn:
                await conn.execute(insert(story_likes).values(
                    user_id=ANONYMOUS_USER_ID,
                    story_id=story.id,
                    state=state,
                    timestamp=datetime.utcnow().replace(tzinfo=pytz.utc)
                ))

                await conn.execute(
                    update(stories)
                        .where(stories.c.id == story.id)
                        .values(likes_count=stories.c.likes_count + diff))

                await conn.execute(
                    update(users)
                        .where(users.c.id == ANONYMOUS_USER_ID)
                        .values(likes_count=users.c.likes_count + diff))

            async with request.db.acquire() as conn:
                like = await conn.fetchrow(
                    select([story_likes])
                        .where(story_likes.c.user_id == ANONYMOUS_USER_ID)
                        .where(story_likes.c.story_id == story.id)
                        .order_by(story_likes.c.timestamp.desc()))

            if like is None:
                return web.Response(
                    status=500,
                    content_type="application/json",
                    text=json.dumps(error(1006)))

        return web.Response(
            status=201,
            content_type="application/json",
            text=json.dumps(ok({
                "user_id": like.user_id,
                "story_id": like.story_id,
                "state": like.state,
                "timestamp": like.timestamp.isoformat()
            })))
