"""Comment threads as templates and r2's JSON draw them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar

from app.ids import Fullname, Kind
from app.services import ranking
from app.services.flair import NO_FLAIR
from app.views.listing import listing_json, prefixed

if TYPE_CHECKING:
    from datetime import datetime

    from app.jsontypes import JSON
    from app.repos.base import Comment, Community, Link
    from app.services.comments import More, Selection, Shown
    from app.services.flair import FlairDisplay
    from app.services.flair import Shown as FlairShown

DELETED = '[deleted]'
REMOVED = '[removed]'


@dataclass(frozen=True, slots=True)
class MoreNode:
    """'load more comments (N replies)', answered by ``/api/morechildren``."""

    parent: str
    depth: int
    ids: tuple[str, ...]
    count: int
    # Where a browser without JS reads them: the parent's own thread.
    permalink: str

    @property
    def name(self) -> str:
        return f't1_{self.ids[0]}'

    def to_json(self) -> dict[str, JSON]:
        return {
            'kind': 'more',
            'data': {
                'count': self.count,
                'name': self.name,
                'id': self.ids[0],
                'parent_id': self.parent,
                'depth': self.depth,
                'children': list(self.ids),
            },
        }


@dataclass(frozen=True, slots=True)
class CommentNode:
    comment: Comment
    depth: int
    likes: bool | None
    children: tuple[CommentNode, ...]
    more: MoreNode | None
    continued: bool
    permalink: str
    is_submitter: bool
    score_hidden: bool
    # The viewer moderates here: removed comments show, with their state and reports.
    moderator_view: bool = False
    # Pinned by a moderator above the other top-level comments.
    stickied: bool = False
    author_flair: FlairShown | None = None
    saved: bool = False
    # The author is the viewer's friend.
    friend: bool = False

    @property
    def vote_class(self) -> str:
        return {True: 'likes', False: 'dislikes', None: 'unvoted'}[self.likes]

    @property
    def parent_permalink(self) -> str | None:
        """The parent comment's permalink, for the 'parent' button."""
        if self.comment.parent_id is None:
            return None
        thread = self.permalink.removesuffix(f'{self.comment.fullname.id36}/')
        return f'{thread}{Fullname(Kind.COMMENT, self.comment.parent_id).id36}/'

    @property
    def author(self) -> str:
        return DELETED if self.comment.deleted else self.comment.author_name

    @property
    def body_html(self) -> str:
        if self.comment.deleted:
            return f'<p>{DELETED}</p>'
        if (self.comment.removed or self.comment.spam) and not self.moderator_view:
            return f'<p>{REMOVED}</p>'
        return self.comment.body_html

    @property
    def controversial(self) -> bool:
        return ranking.is_controversial(self.comment.upvotes, self.comment.downvotes)

    def data(self, replies: JSON) -> dict[str, JSON]:
        """The ``data`` of a ``t1``, with r2's field names."""
        c = self.comment
        created = c.created.timestamp()
        score = 1 if self.score_hidden else c.score
        return {
            'id': c.fullname.id36,
            'name': str(c.fullname),
            'parent_id': str(c.parent_fullname),
            'link_id': str(Fullname(Kind.LINK, c.link_id)),
            'author': self.author,
            'author_fullname': None if c.deleted else str(Fullname(Kind.ACCOUNT, c.author_id)),
            'author_flair_text': self.author_flair.text or None if self.author_flair else None,
            'author_flair_css_class': (
                self.author_flair.css_class or None if self.author_flair else None
            ),
            'body': DELETED if c.deleted else c.body_md,
            'body_html': self.body_html,
            'score': score,
            'ups': score,
            'downs': 0,
            'likes': self.likes,
            'score_hidden': self.score_hidden,
            'controversiality': int(self.controversial),
            'created': created,
            'created_utc': created,
            'edited': c.edited.timestamp() if c.edited else False,
            'community': c.community_name,
            'community_id': str(Fullname(Kind.COMMUNITY, c.community_id)),
            'community_name_prefixed': prefixed(c.community_name),
            'permalink': self.permalink,
            'is_submitter': self.is_submitter,
            'send_replies': c.sendreplies,
            'distinguished': c.distinguished,
            'stickied': self.stickied,
            'archived': False,
            'gilded': 0,
            'saved': self.saved,
            'num_reports': c.reported if self.moderator_view else None,
            'approved': c.approved_by is not None if self.moderator_view else None,
            'removed': c.removed if self.moderator_view else None,
            'spam': c.spam if self.moderator_view else None,
            'depth': self.depth,
            'replies': replies,
        }

    def to_json(self, modhash: str) -> JSON:
        kids: list[JSON] = [child.to_json(modhash) for child in self.children]
        if self.more is not None:
            kids.append(self.more.to_json())
        replies: JSON = listing_json(kids, after=None, before=None, modhash=modhash) if kids else ''
        return {'kind': 't1', 'data': self.data(replies)}


@dataclass(frozen=True, slots=True, kw_only=True)
class Thread:
    """The comments shown on a link's page, or under one comment on its permalink."""

    nodes: tuple[CommentNode, ...]
    more: MoreNode | None
    sort: str
    # The comment whose permalink this is, when it is one.
    focus: str | None

    sorts: ClassVar[tuple[tuple[str, str], ...]] = (
        ('confidence', 'best'),
        ('top', 'top'),
        ('new', 'new'),
        ('controversial', 'controversial'),
        ('old', 'old'),
        ('qa', 'q&a'),
    )

    def to_json(self, modhash: str) -> JSON:
        children: list[JSON] = [node.to_json(modhash) for node in self.nodes]
        if self.more is not None:
            children.append(self.more.to_json())
        return listing_json(children, after=None, before=None, modhash=modhash)


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadContext:
    """Everything that decides how one comment is drawn, other than the comment."""

    link: Link
    community: Community
    likes: dict[int, int]
    viewer_id: int | None
    is_moderator: bool
    now: datetime
    link_permalink: str
    flair: FlairDisplay = NO_FLAIR
    saved: frozenset[Fullname] = frozenset()
    friends: frozenset[int] = frozenset()

    def node(
        self,
        comment: Comment,
        shown: Shown | None,
        comments: dict[int, Comment],
        depth_offset: int = 0,
    ) -> CommentNode:
        children: tuple[CommentNode, ...] = ()
        more = None
        continued = False
        if shown is not None:
            children = tuple(
                self.node(comments[kid.id], kid, comments, depth_offset)
                for kid in shown.children
                if kid.id in comments
            )
            more = self.more_node(shown.more, depth_offset)
            continued = shown.continued
        hide_for = timedelta(minutes=self.community.settings.comment_score_hide_mins)
        hidden_score = (
            not self.is_moderator
            and comment.author_id != self.viewer_id
            and (self.link.contest_mode or self.now - comment.created < hide_for)
        )
        return CommentNode(
            comment=comment,
            depth=(shown.depth if shown else 0) + depth_offset,
            likes={1: True, -1: False}.get(self.likes.get(comment.id, 0)),
            children=children,
            more=more,
            continued=continued,
            permalink=f'{self.link_permalink}{comment.fullname.id36}/',
            is_submitter=comment.author_id == self.link.author_id,
            score_hidden=hidden_score,
            moderator_view=self.is_moderator,
            stickied=comment.id == self.link.sticky_comment_id,
            author_flair=self.flair.author(comment.community_id, comment.author_id),
            saved=comment.fullname in self.saved,
            friend=comment.author_id in self.friends and not comment.deleted,
        )

    def more_node(self, more: More | None, depth_offset: int = 0) -> MoreNode | None:
        if more is None:
            return None
        if more.parent_id is None:
            parent = str(self.link.fullname)
            permalink = self.link_permalink
        else:
            parent = str(Fullname(Kind.COMMENT, more.parent_id))
            permalink = f'{self.link_permalink}{Fullname(Kind.COMMENT, more.parent_id).id36}/'
        return MoreNode(
            parent=parent,
            depth=more.depth + depth_offset,
            ids=tuple(Fullname(Kind.COMMENT, i).id36 for i in more.ids),
            count=more.count,
            permalink=permalink,
        )

    def thread(
        self,
        selection: Selection,
        comments: dict[int, Comment],
        *,
        sort: str,
        focus: str | None = None,
        depth_offset: int = 0,
    ) -> Thread:
        nodes = tuple(
            self.node(comments[root.id], root, comments, depth_offset)
            for root in selection.roots
            if root.id in comments
        )
        return Thread(
            nodes=nodes, more=self.more_node(selection.more, depth_offset), sort=sort, focus=focus
        )
