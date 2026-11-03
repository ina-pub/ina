"""``/prefs/apps``: registering API apps, and taking back what was granted to others.

r2's /prefs/apps with /api/updateapp, /api/deleteapp and /api/revokeapp. A new secret is shown
on the page that answers the form, never put in a redirect's address.
"""

from __future__ import annotations

from dataclasses import dataclass

from litestar import get, post
from litestar.params import URLEncodedBody

from app.controllers.auth import WHILE_SUSPENDED, to_viewer
from app.controllers.deps import AppsDep, UserDep
from app.controllers.render import Rendered
from app.errors import ErrorCode, FieldError, InaError
from app.repos.base import AppKind, OAuthRepo
from app.services import oauth
from app.services.sessions import Authenticated
from app.views.apps import AppsPage
from app.views.forms import FormResult

APPS = '/prefs/apps'


async def apps_page(
    apps: OAuthRepo,
    user: Authenticated,
    *,
    secret: tuple[str, str] | None = None,
    editing: str | None = None,
    errors: tuple[FieldError, ...] = (),
) -> AppsPage:
    return AppsPage(
        viewer=to_viewer(user),
        apps=tuple(await apps.owned(user.account.id)),
        authorized=tuple(await apps.authorizations(user.account.id)),
        secret=secret,
        editing=editing,
        errors=errors,
    )


@get(APPS)
async def show_apps(apps: AppsDep, user: UserDep) -> AppsPage:
    return await apps_page(apps, user)


@dataclass
class AppForm:
    # Empty for a new app.
    client_id: str = ''
    app_type: str = ''
    name: str = ''
    description: str = ''
    about_url: str = ''
    redirect_uri: str = ''


@post('/api/updateapp')
async def update_app(data: URLEncodedBody[AppForm], apps: AppsDep, user: UserDep) -> Rendered:
    """Register an app, or change one's details."""
    client = await oauth.owned(apps, user.account, data.client_id) if data.client_id else None
    try:
        if client is not None:
            kind = client.kind
        elif data.app_type in AppKind:
            kind = AppKind(data.app_type)
        else:
            raise InaError.of(ErrorCode.INVALID_OPTION, 'app_type')
        fields = oauth.app_fields(
            kind,
            name=data.name,
            description=data.description,
            about_url=data.about_url,
            redirect_uri=data.redirect_uri,
        )
    except InaError as error:
        page = await apps_page(apps, user, editing=data.client_id, errors=error.errors)
        return Rendered(content=FormResult(errors=error.errors, page=page))
    if client is not None:
        await apps.update_client(client.id, fields)
        return Rendered(content=FormResult(redirect=APPS))
    client, secret = await oauth.register(apps, user.account, kind, fields)
    shown = (client.id, secret) if secret else None
    return Rendered(
        content=FormResult(
            data={'client_id': client.id, 'client_secret': secret},
            page=await apps_page(apps, user, secret=shown),
        )
    )


@dataclass
class ClientForm:
    client_id: str = ''


@post('/api/new_app_secret')
async def new_app_secret(
    data: URLEncodedBody[ClientForm], apps: AppsDep, user: UserDep
) -> Rendered:
    client = await oauth.owned(apps, user.account, data.client_id)
    secret = await oauth.new_secret(apps, client)
    return Rendered(
        content=FormResult(
            data={'client_id': client.id, 'client_secret': secret},
            page=await apps_page(apps, user, secret=(client.id, secret)),
        )
    )


@post('/api/deleteapp')
async def delete_app(data: URLEncodedBody[ClientForm], apps: AppsDep, user: UserDep) -> Rendered:
    client = await oauth.owned(apps, user.account, data.client_id)
    await apps.delete_client(client.id)
    return Rendered(content=FormResult(redirect=APPS))


@post('/api/revokeapp', opt={WHILE_SUSPENDED: True})
async def revoke_app(data: URLEncodedBody[ClientForm], apps: AppsDep, user: UserDep) -> Rendered:
    """Take back everything the account granted an app."""
    await apps.revoke_client(user.account.id, data.client_id)
    return Rendered(content=FormResult(redirect=APPS))
