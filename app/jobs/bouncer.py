"""The ``bouncer`` queue: check new posts against Bouncer's rules for their community."""

from __future__ import annotations

from datetime import UTC, datetime

import dramatiq

from app.ids import Fullname, Kind
from app.infra.cache import Cache
from app.jobs.context import redis, transaction
from app.jobs.inbox import deliver_comment
from app.jobs.scores import recount_comment
from app.jobs.search import index_link
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.communities import PgCommunityRepo
from app.repos.pg.links import PgLinkRepo, PgVoteRepo
from app.repos.pg.messages import PgMessageRepo
from app.repos.pg.moderation import PgModerationRepo
from app.services import bouncer
from app.services.listings import community_scope
from app.views.links import permalink


async def check(thing_fullname: str) -> None:
    thing = Fullname.parse(thing_fullname)
    async with transaction() as session:
        accounts, communities = PgAccountRepo(session), PgCommunityRepo(session)
        links, comments = PgLinkRepo(session), PgCommentRepo(session)
        comment = await comments.by_id(thing.id) if thing.kind is Kind.COMMENT else None
        link = await links.by_id(comment.link_id if comment else thing.id)
        community = await communities.by_id(link.community_id) if link else None
        if link is None or community is None or not community.bouncer_rules.strip():
            return
        try:
            rules = bouncer.parse(community.bouncer_rules)
        except bouncer.RuleError:
            # Saving checks the rules, so these were valid under an older parser; skip them.
            return
        author = await accounts.by_id(comment.author_id if comment else link.author_id)
        if author is None:
            return
        mod, _, _ = await communities.standing(community.id, author.id)
        link_url = permalink(link)
        item = bouncer.Item(
            link=link,
            comment=comment,
            author=author,
            author_is_moderator=mod is not None,
            permalink=f'{link_url}{comment.fullname.id36}/' if comment else link_url,
        )
        repos = bouncer.Repos(
            accounts=accounts,
            communities=communities,
            moderation=PgModerationRepo(session),
            comments=comments,
            votes=PgVoteRepo(session),
            messages=PgMessageRepo(session),
        )
        outcome = await bouncer.moderate(repos, rules, item, community, now=datetime.now(UTC))
    # After the commit, as the web handlers do.
    if outcome.changed:
        cache = Cache(redis())
        await cache.forget_things(sorted({str(link.fullname), thing_fullname}))
        await cache.bump(community_scope(community.id))
        if comment is None:
            index_link.send(thing_fullname)
    for reply in outcome.comments:
        recount_comment.send(str(reply.fullname))
        deliver_comment.send(str(reply.fullname))


check_new = dramatiq.actor(actor_name='bouncer_check', queue_name='bouncer')(check)
