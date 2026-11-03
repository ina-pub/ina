"""The fields of each kind of thing in the API's JSON, as its ``data`` carries them.

The shape test checks the API against these, and the OpenAPI document lists them.
"""

from __future__ import annotations

LINK = frozenset({
    'approved', 'archived', 'author', 'author_flair_css_class', 'author_flair_text',
    'author_fullname', 'clicked', 'community', 'community_id', 'community_name_prefixed',
    'contest_mode', 'created', 'created_utc', 'distinguished', 'domain', 'downs', 'edited',
    'hidden', 'hide_score', 'id', 'is_self', 'likes', 'link_flair_css_class', 'link_flair_text',
    'locked', 'media', 'name', 'num_comments', 'num_reports', 'over_18', 'permalink', 'removed',
    'report_reasons', 'saved', 'score', 'selftext', 'selftext_html', 'spam', 'spoiler',
    'stickied', 'suggested_sort', 'thumbnail', 'thumbnail_height', 'thumbnail_width', 'title',
    'ups', 'upvote_ratio', 'url', 'visited',
})  # fmt: skip
COMMENT = frozenset({
    'approved', 'archived', 'author', 'author_flair_css_class', 'author_flair_text',
    'author_fullname', 'body', 'body_html', 'community', 'community_id',
    'community_name_prefixed', 'controversiality', 'created', 'created_utc', 'depth',
    'distinguished', 'downs', 'edited', 'gilded', 'id', 'is_submitter', 'likes', 'link_id',
    'name', 'num_reports', 'parent_id', 'permalink', 'removed', 'replies', 'saved', 'score',
    'score_hidden', 'send_replies', 'spam', 'stickied', 'ups',
})  # fmt: skip
PROFILE_COMMENT = frozenset({
    'author', 'author_flair_css_class', 'author_flair_text', 'author_fullname', 'body',
    'body_html', 'community', 'community_id', 'community_name_prefixed', 'created',
    'created_utc', 'distinguished', 'downs', 'edited', 'id', 'likes', 'link_author', 'link_id',
    'link_permalink', 'link_title', 'link_url', 'name', 'parent_id', 'permalink', 'saved',
    'score', 'ups',
})  # fmt: skip
COMMUNITY = frozenset({
    'banner_img', 'collapse_deleted_comments', 'comment_score_hide_mins', 'community_type',
    'created', 'created_utc', 'description', 'description_html', 'display_name',
    'display_name_prefixed', 'header_size', 'icon_img', 'id', 'lang', 'name', 'over18',
    'public_description', 'public_description_html', 'public_traffic', 'quarantine',
    'show_media', 'show_media_preview', 'submission_type', 'submit_link_label', 'submit_text',
    'submit_text_html', 'submit_text_label', 'subscribers', 'suggested_comment_sort', 'title',
    'url', 'user_is_moderator', 'user_is_subscriber', 'wiki_enabled',
})  # fmt: skip
ACCOUNT = frozenset({
    'comment_karma', 'created', 'created_utc', 'has_mail', 'has_mod_mail', 'has_verified_email',
    'hide_from_robots', 'id', 'inbox_count', 'is_mod', 'is_suspended', 'link_karma', 'modhash',
    'name', 'over_18',
})  # fmt: skip
MESSAGE = frozenset({
    'author', 'body', 'body_html', 'community', 'community_name_prefixed', 'context', 'created',
    'created_utc', 'dest', 'distinguished', 'first_message', 'first_message_name', 'id', 'name',
    'new', 'parent_id', 'replies', 'subject', 'was_comment',
})  # fmt: skip

# Each by the name the OpenAPI document gives it, with its kind.
KINDS = {
    'Link': ('t3', LINK),
    'Comment': ('t1', COMMENT),
    'ProfileComment': ('t1', PROFILE_COMMENT),
    'Community': ('t5', COMMUNITY),
    'Account': ('t2', ACCOUNT),
    'Message': ('t4', MESSAGE),
}
