from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def _same_link(one: str, other: str) -> bool:
    """Whether two URLs point at the same page.

    Compared after collapsing repeated slashes in the path and dropping a
    trailing one, because the caption's link is assembled by string
    concatenation in the crew portal and a package slug that already starts
    with '/' yields 'example.com//tours/...'. That is the same page, and the
    caption should not keep a redundant link over a stray character.
    """
    def normalise(url: str) -> str:
        scheme, _, rest = url.strip().rstrip('/').partition('://')
        return scheme + '://' + re.sub(r'/{2,}', '/', rest)

    return bool(one.strip()) and normalise(one) == normalise(other)


def drop_trailing_link(caption: str, product_url: str) -> str:
    """Remove the caption's final line when it is exactly the product's link.

    Once the product is tagged, the tag is the link - tappable, priced, and in
    the image itself - while a URL in an Instagram caption cannot even be
    clicked. So the caption keeps its call to action and loses the dead text.

    Deliberately narrow: only the last line, only when that whole line is that
    URL. The caption is written by a model, and anything looser would risk
    eating a line it was never meant to touch. When the link is not exactly
    there, the caption is returned untouched.
    """
    if not product_url.strip():
        return caption

    lines = caption.rstrip().split('\n')

    while lines and not lines[-1].strip():
        lines.pop()

    if not lines or not _same_link(lines[-1], product_url):
        return caption

    lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()

    return '\n'.join(lines)


class _ComposioToolExecutor:
    """Adapts the current Composio SDK (`client.tools.execute`) to the
    `.execute(tool_name, kwargs)` shape the rest of this module expects.

    The Composio SDK's `Composio(...).create(...).tools()` call now returns
    Tool Router meta-tool schemas (for LLM-driven agents) rather than
    directly callable per-action objects, so toolkit actions like
    INSTAGRAM_CREATE_CAROUSEL_CONTAINER have to be invoked through
    `client.tools.execute` instead.
    """

    def __init__(self, client: Any, user_id: str) -> None:
        self._client = client
        self._user_id = user_id

    def execute(self, tool_name: str, kwargs: dict[str, Any]) -> Any:
        response = self._client.tools.execute(
            tool_name,
            kwargs,
            user_id=self._user_id,
            dangerously_skip_version_check=True,
        )
        if isinstance(response, dict):
            if response.get('successful') is False:
                raise RuntimeError(response.get('error') or f'{tool_name} failed')
            if 'data' in response:
                return response['data']
        return response


class ComposioPublisher:
    """Publishes carousels to Instagram via Composio.

    Auth is handled entirely by the Composio CLI (`composio link instagram`)
    ahead of time - no local auth-state file is written here. See
    drive_ingestion.ComposioConnector for why the old DIY state-file approach
    was removed rather than patched.
    """

    def __init__(self, api_key: str | None = None, user_id: str | None = None, project_root: Path | None = None) -> None:
        self.api_key = api_key or os.getenv('COMPOSIO_API_KEY')
        self.user_id = user_id or os.getenv('COMPOSIO_USER_ID') or 'jvto_automation'
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2])

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get_session(self) -> Any | None:
        if not self.api_key:
            return None
        try:
            from composio import Composio
        except Exception:
            return None

        try:
            client = Composio(api_key=self.api_key)
            return _ComposioToolExecutor(client, self.user_id)
        except Exception:
            return None

    def _invoke_tool(self, tools: Any, tool_names: tuple[str, ...], kwargs: dict[str, Any]) -> Any:
        if tools is None:
            raise RuntimeError('Composio tools are not available')

        # The loop exists to tolerate SDK shape differences, so a failure of one
        # spelling has to keep trying the next. But the LAST real error is kept
        # and re-raised: reporting only 'no call succeeded' turned a plainly
        # worded Meta rejection ("Only photo or video can be accepted as media
        # type") into an unexplained failure, and cost an afternoon.
        last_error: Exception | None = None

        for tool_name in tool_names:
            for method_name in ('execute', 'run', 'invoke'):
                method = getattr(tools, method_name, None)
                if not callable(method):
                    continue
                try:
                    return method(tool_name, kwargs)
                except TypeError:
                    try:
                        return method(tool_name, **kwargs)
                    except Exception as exc:
                        last_error = exc
                        continue
                except Exception as exc:
                    last_error = exc
                    continue

        if last_error is not None:
            raise RuntimeError(str(last_error)) from last_error

        raise RuntimeError('No compatible Instagram tool call succeeded')


    def _connected_account_id(self) -> str | None:
        """The Instagram account linked under this user id.

        Looked up rather than configured: the id belongs to the connection, not
        to this deployment, and hard-coding it would break silently the first
        time the account is relinked.
        """
        from composio import Composio

        client = Composio(api_key=self.api_key).client
        page = client.connected_accounts.list(user_ids=[self.user_id])

        for account in getattr(page, 'items', []) or []:
            toolkit = getattr(account, 'toolkit', None)
            slug = getattr(toolkit, 'slug', None) or (toolkit or {}).get('slug') if toolkit else None
            if slug == 'instagram' and getattr(account, 'status', None) == 'ACTIVE':
                return getattr(account, 'id', None)

        return None

    # Meta's fetcher pulls each photo from several IPs at once, which trips the
    # crew portal host's burst rate limit; the 429 it gets back is reported as
    # this, with no mention of throttling. The limiter recovers in seconds, so
    # the photo is fine and only the timing was wrong.
    _MEDIA_URL_REFUSED = 'Only photo or video can be accepted as media type'

    def _waiting_out_throttling(
        self, image_url: str, create, attempts: int = 4, backoff_seconds: float = 8.0
    ) -> Any:
        """Run a container creation, waiting out a throttled fetch.

        Retried only for the rejection above. Anything else - a genuinely
        broken URL, a revoked token, a product that cannot be tagged - fails on
        the first try, because retrying those just delays the same answer.

        Both paths that create containers go through here. Guarding only the
        untagged fallback, as the first version of this did, meant a throttled
        fetch during tagging gave up instantly and the post lost its product
        tag to a hiccup the fallback then rode out two retries later.
        """
        import time

        for attempt in range(1, attempts + 1):
            try:
                return create()
            except Exception as exc:
                if self._MEDIA_URL_REFUSED not in str(exc) or attempt == attempts:
                    raise
                print(
                    f'Meta could not fetch {image_url} on attempt {attempt} '
                    f'(the host throttled it); retrying in {backoff_seconds:.0f}s.'
                )
                time.sleep(backoff_seconds)

    def _create_child_container(self, tools: Any, payload: dict[str, Any]) -> Any:
        return self._waiting_out_throttling(
            payload.get('image_url'),
            lambda: self._invoke_tool(
                tools,
                ('INSTAGRAM_CREATE_MEDIA_CONTAINER', 'instagram_create_media_container'),
                payload,
            ),
        )

    def _shopping_account_id(self) -> str | None:
        """The Facebook connection that is allowed to tag catalog products.

        Composio's *managed* Facebook OAuth grants only Page and WhatsApp
        scopes - no instagram_shopping_tag_products, no catalog_management - so
        a managed connection can never tag a product however it is called. Only
        one on a custom auth config (the JVTO Meta app) can. That is the filter
        rather than a hard-coded connection id, which would go stale the first
        time the account is relinked.
        """
        from composio import Composio

        client = Composio(api_key=self.api_key).client
        page = client.connected_accounts.list(user_ids=[self.user_id])

        for account in getattr(page, 'items', []) or []:
            toolkit = getattr(account, 'toolkit', None)
            if getattr(toolkit, 'slug', None) != 'facebook':
                continue
            if getattr(account, 'status', None) != 'ACTIVE':
                continue

            auth_config = getattr(account, 'auth_config', None)
            if getattr(auth_config, 'is_composio_managed', True):
                continue

            return getattr(account, 'id', None)

        return None

    def _get(self, account_id: str, endpoint: str) -> dict[str, Any]:
        from composio import Composio

        client = Composio(api_key=self.api_key).client
        response = client.tools.proxy(endpoint=endpoint, method='GET', connected_account_id=account_id)
        data = response.data if isinstance(response.data, dict) else {}

        if 'error' in data:
            raise RuntimeError((data['error'] or {}).get('message', str(data)))

        return data

    def _business_account_id(self, account_id: str) -> str:
        """The Instagram Business Account id, as graph.facebook.com knows it.

        Not the same number as INSTAGRAM_USER_ID, which the publish path uses:
        that one is the Instagram-Login id and the shopping edges do not exist
        on it, so asking it for available_catalogs answers "nonexisting field".
        Discovered from the Page rather than added as another secret, because
        two ids for one account in configuration is a trap someone will fall
        into again.
        """
        pages = (self._get(account_id, '/me/accounts?fields=instagram_business_account') or {}).get('data') or []

        for page in pages:
            business_account = page.get('instagram_business_account') or {}
            if business_account.get('id'):
                return business_account['id']

        raise RuntimeError('no Instagram business account is linked to these Pages')

    def _product_for(
        self, account_id: str, instagram_user_id: str, retailer_id: str
    ) -> tuple[str, str] | None:
        """The catalog product for this package code, as (id, landing page).

        Three things make the lookup indirect. The shop's catalog is discovered
        rather than configured, because pinning a catalog id here would break
        the day the shop is pointed at another one. The id that works is not
        the one the catalog's own /products edge returns - that one is refused
        with "Cannot tag product" - so it comes from catalog_product_search.
        And catalog_product_search does not carry the product's url, which the
        caption needs in order to drop a link the tag has made redundant, so
        that comes from the /products edge instead.
        """
        catalogs = (self._get(account_id, f'/{instagram_user_id}/available_catalogs') or {}).get('data') or []
        if not catalogs:
            raise RuntimeError('this Instagram account has no shop catalog')

        catalog_id = catalogs[0].get('catalog_id')

        # The roster is 16 packages; one page covers it with room to spare.
        found = self._get(
            account_id,
            f'/{instagram_user_id}/catalog_product_search?catalog_id={catalog_id}&limit=100',
        )

        product_id = None
        for product in found.get('data') or []:
            if product.get('retailer_id') == retailer_id:
                product_id = product.get('product_id')
                break

        if not product_id:
            return None

        listed = self._get(account_id, f'/{catalog_id}/products?fields=retailer_id,url&limit=100')
        product_url = ''
        for product in listed.get('data') or []:
            if product.get('retailer_id') == retailer_id:
                product_url = product.get('url') or ''
                break

        return product_id, product_url

    def _tagged_children(
        self, account_id: str, instagram_user_id: str, image_urls: list[str], product_id: str
    ) -> list[str]:
        """Carousel children carrying the product tag.

        The tag goes on each child, never on the parent: Meta refuses
        product_tags on a CAROUSEL container with "The media type 8 is
        unknown". These children are created through the shopping connection
        while the parent that assembles them is not - a container belongs to
        the Instagram user rather than to the token that made it, so the proven
        publish path stays untouched.
        """
        import json
        import urllib.parse

        from composio import Composio

        client = Composio(api_key=self.api_key).client
        tags = json.dumps([{'product_id': product_id, 'x': 0.5, 'y': 0.5}])
        children: list[str] = []

        def create(image_url: str) -> str:
            query = urllib.parse.urlencode({
                'image_url': image_url,
                'is_carousel_item': 'true',
                'product_tags': tags,
            })
            response = client.tools.proxy(
                endpoint=f'/{instagram_user_id}/media?{query}',
                method='POST',
                connected_account_id=account_id,
            )
            data = response.data if isinstance(response.data, dict) else {}

            if 'id' not in data:
                raise RuntimeError((data.get('error') or {}).get('message', str(data)))

            return data['id']

        for image_url in image_urls:
            children.append(
                self._waiting_out_throttling(image_url, lambda url=image_url: create(url))
            )

        return children

    def _product_tagged_children(
        self, instagram_user_id: str | None, image_urls: list[str], package_code: str
    ) -> tuple[list[str] | None, str | None, str]:
        """Children with the product tagged, or (None, reason, '') to post without it.

        Every failure here is deliberately non-fatal. A missing shop
        connection, a package the catalog has never heard of, a product pulled
        from the shop, a Meta outage on the shopping endpoints - none of those
        are reasons to lose the trip's post, which is the part that took a crew
        a whole day to produce. The tag is the garnish; the carousel is the
        meal. Containers abandoned on this path are never published, and
        Instagram expires them on its own.
        """
        try:
            account_id = self._shopping_account_id()
            if not account_id:
                return None, 'no Meta shop connection is linked', ''

            # Deliberately ignores the instagram_user_id the publish path uses:
            # the shopping edges live on the business account id instead.
            business_id = self._business_account_id(account_id)

            product = self._product_for(account_id, business_id, package_code)
            if not product:
                return None, f'{package_code} is not in the shop catalog', ''

            product_id, product_url = product
            children = self._tagged_children(account_id, business_id, image_urls, product_id)

            return children, None, product_url
        except Exception as exc:  # noqa: BLE001 - the post must survive any of these
            return None, str(exc), ''

    # Meta answers this when it cannot resolve a collaborator, and it means the
    # username is wrong or the account is private. It groups both causes, so the
    # only safe response is to drop the tags and still publish.
    _COLLABORATOR_REJECTED = 'private profile or invalid'

    def _create_carousel_container(
        self, instagram_user_id: str | None, caption: str, children: list[str], collaborators: list[str]
    ) -> tuple[dict[str, Any], list[str]]:
        """Create the parent container, giving up the collaborator tags before
        giving up the post.

        A crew member who renamed their Instagram account should cost the trip
        its collaborator credit, not its entire post.
        """
        import json

        from composio import Composio

        account_id = self._connected_account_id()
        if not account_id:
            raise RuntimeError('No ACTIVE Instagram connection found for this user id')

        client = Composio(api_key=self.api_key).client

        def attempt(tags: list[str]) -> dict[str, Any]:
            body: dict[str, Any] = {
                'media_type': 'CAROUSEL',
                'children': ','.join(children),
                'caption': caption,
            }
            if tags:
                body['collaborators'] = json.dumps(tags)

            response = client.tools.proxy(
                endpoint=f'/{instagram_user_id}/media',
                method='POST',
                body=body,
                connected_account_id=account_id,
            )
            data = response.data if isinstance(response.data, dict) else {}

            if 'id' not in data:
                message = (data.get('error') or {}).get('message', str(data))
                raise RuntimeError(message)

            return data

        if not collaborators:
            return attempt([]), []

        try:
            return attempt(collaborators), []
        except RuntimeError as exc:
            if self._COLLABORATOR_REJECTED not in str(exc):
                raise

        # Meta refuses the whole set without saying which handle it could not
        # resolve, so ask about them one at a time. Dropping all of them would
        # cost the crew who did nothing wrong their credit too, and on this
        # roster most trips have a working handle alongside a stale one. The
        # extra calls happen only on this path, and the containers they create
        # are never published - Instagram discards an unpublished one itself.
        accepted: list[str] = []
        rejected: list[str] = []

        for handle in collaborators:
            try:
                attempt([handle])
                accepted.append(handle)
            except RuntimeError as probe_exc:
                if self._COLLABORATOR_REJECTED not in str(probe_exc):
                    raise
                rejected.append(handle)

        print(
            f'Instagram could not resolve {rejected}: those usernames are wrong or '
            'those accounts are private. Fix the instagram_username values so the '
            'next post credits them.'
        )

        return attempt(accepted), rejected

    def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
        instagram_user_id: str | None = None,
        collaborators: list[str] | None = None,
        package_code: str | None = None,
    ) -> dict[str, Any]:
        if not image_urls:
            return {'status': 'dry_run', 'message': 'No image URLs were supplied for carousel publishing.'}
        if not self.is_configured():
            return {'status': 'missing_api_key', 'message': 'COMPOSIO_API_KEY is not configured'}

        try:
            tools = self._get_session()
        except Exception as exc:  # pragma: no cover - optional dependency path
            return {'status': 'error', 'message': str(exc)}
        if tools is None:
            return {
                'status': 'not_authorized',
                'message': "Composio SDK unavailable or Instagram not linked. Run 'composio link instagram' first.",
            }

        # Tagging is attempted first because it needs its own containers, but a
        # refusal only costs the tag: on any failure this falls through to the
        # plain child-creation path below and the carousel still goes out.
        product_tag_skipped: str | None = None
        child_creation_ids: list[str] = []

        if package_code:
            tagged, product_tag_skipped, product_url = self._product_tagged_children(
                instagram_user_id, image_urls, package_code
            )
            if tagged:
                child_creation_ids = tagged
                # The tag now carries the link, so the caption's copy of it is
                # dead text. Dropped only on success: a post that lost its tag
                # must keep the only pointer it has left.
                caption = drop_trailing_link(caption, product_url)
            else:
                print(f'Posting without a product tag ({package_code}): {product_tag_skipped}')

        # Skipped entirely when the tagged children above already succeeded.
        for image_url in ([] if child_creation_ids else image_urls):
            child_payload = {'ig_user_id': instagram_user_id, 'image_url': image_url, 'is_carousel_item': True}
            try:
                # INSTAGRAM_CREATE_MEDIA_CONTAINER is the confirmed real Composio
                # action name for creating a single carousel-child container -
                # INSTAGRAM_CREATE_CAROUSEL_CONTAINER only accepts already-created
                # child creation_ids, not raw image URLs.
                child_container = self._create_child_container(tools, child_payload)
            except Exception as exc:
                return {'status': 'error', 'message': f'Failed to create carousel child container for {image_url}: {exc}'}

            child_id = child_container.get('id') if isinstance(child_container, dict) else getattr(child_container, 'id', None)
            if not child_id:
                return {'status': 'error', 'message': f'Child container created for {image_url} but no id was returned.', 'data': child_container}
            child_creation_ids.append(child_id)

        # The carousel container goes through Composio's HTTP proxy rather than
        # its Instagram tool, because the tool accepts only caption/children and
        # collaborator tagging needs a parameter it does not expose. The proxy
        # still uses the same managed connection, so no Meta token is handled
        # here.
        try:
            container, dropped = self._create_carousel_container(
                instagram_user_id, caption, child_creation_ids, collaborators or []
            )
        except Exception as exc:
            return {'status': 'error', 'message': f'Failed to create carousel container: {exc}'}

        creation_id = container.get('id') if isinstance(container, dict) else getattr(container, 'id', None)
        if not creation_id:
            return {'status': 'error', 'message': 'Carousel container created but no creation_id was returned.', 'data': container}

        publish_payload = {'ig_user_id': instagram_user_id, 'creation_id': creation_id}
        try:
            publish_result = self._invoke_tool(
                tools,
                ('INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH', 'instagram_publish_media', 'instagram_post'),
                publish_payload,
            )
        except Exception as exc:
            return {'status': 'error', 'message': f'Carousel container created but publish failed: {exc}', 'creation_id': creation_id}

        return {
            'status': 'published',
            'data': publish_result,
            'creation_id': creation_id,
            'dropped_collaborators': dropped,
            'product_tagged': bool(package_code) and product_tag_skipped is None,
            'product_tag_skipped': product_tag_skipped,
        }
