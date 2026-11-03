"""The ``inbox`` queue: record mentions and keep unread counts for whoever a new thing reaches."""

from __future__ import annotations

import dramatiq

from app.ids import Fullname
from app.jobs.context import transaction
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.links import PgLinkRepo
from app.repos.pg.messages import PgMessageRepo
from app.services import messaging


async def comment_delivery(comment_fullname: str) -> None:
    async with transaction() as session:
        comments, links = PgCommentRepo(session), PgLinkRepo(session)
        messages, accounts = PgMessageRepo(session), PgAccountRepo(session)
        communities = PgCommunityRepo(session)
        comment = await comments.by_id(Fullname.parse(comment_fullname).id)
        link = await links.by_id(comment.link_id) if comment else None
        community = await communities.by_id(link.community_id) if link else None
        if comment is None or link is None or community is None:
            return
        parent = await comments.by_id(comment.parent_id) if comment.parent_id else None
        reached = await messaging.deliver_comment(
            messages, accounts, communities, comment, parent, link, community
        )
        for account_id in reached:
            await messaging.recount(messages, accounts, account_id)


async def message_delivery(message_fullname: str) -> None:
    async with transaction() as session:
        messages = PgMessageRepo(session)
        message = await messages.by_id(Fullname.parse(message_fullname).id)
        if message is not None and message.to_id is not None:
            await messaging.recount(messages, PgAccountRepo(session), message.to_id)


deliver_comment = dramatiq.actor(actor_name='deliver_comment', queue_name='inbox')(comment_delivery)
deliver_message = dramatiq.actor(actor_name='deliver_message', queue_name='inbox')(message_delivery)
