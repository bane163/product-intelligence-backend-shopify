from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from application.use_cases.intelligence_get_product_overview import execute
from infrastructure.adapters.supabase_namespaces import SupabaseDomainAccessors
from services.supabase_service import SupabaseService
from shopify import ShopifyClient


def test_product_history_survives_more_than_fifty_audits_and_is_tenant_scoped():
    db = SupabaseService()
    db._get_supabase_client = lambda: None
    for i in range(70):
        db.product_intelligence_audits[str(i)] = {'audit_id': str(i), 'shop_domain': 'a', 'status': 'success', 'created_at': str(i)}
        db.product_intelligence_suggestions[str(i)] = {'suggestion_id': str(i), 'audit_id': str(i), 'shop_domain': 'a', 'product_id': 'p', 'status': 'applied', 'patch_payload': {'title': str(i)}}
    db.product_intelligence_suggestions['other'] = {'suggestion_id': 'other', 'shop_domain': 'b', 'product_id': 'p', 'status': 'applied'}
    db.product_intelligence_suggestions['pending'] = {'suggestion_id': 'pending', 'shop_domain': 'a', 'product_id': 'p', 'status': 'pending'}
    db.product_intelligence_suggestions['failed'] = {'suggestion_id': 'failed', 'shop_domain': 'a', 'product_id': 'p', 'status': 'failed'}
    result = execute(supabase=SupabaseDomainAccessors(db), product_ids=['p'], shop_domain='a', view='review')[0]
    assert len(result['history']) == 70
    assert result['pending_reviews'][0]['suggestion_id'] == 'pending'
    assert all(row['shop_domain'] == 'a' for row in result['history'])


def test_history_explicitly_pages_even_when_database_caps_responses():
    db = SupabaseService()
    rows = [{'suggestion_id': str(i)} for i in range(1205)]
    scopes = []
    class Query:
        def select(self, *_): return self
        def order(self, *_): return self
        def eq(self, *args): scopes.append(args); return self
        def range(self, start, end): self.start = start; self.end = end; return self
        def execute(self): return SimpleNamespace(data=rows[self.start:min(self.end+1, self.start+137)])
    db._get_supabase_client = lambda: SimpleNamespace(table=lambda _: Query())
    assert db.list_product_intelligence_product_suggestions(product_id='p', shop_domain='a') == rows
    assert ('shop_domain', 'a') in scopes and ('product_id', 'p') in scopes


@pytest.mark.asyncio
async def test_live_fields_include_all_canonical_attributes_and_complete_structures():
    client = ShopifyClient.__new__(ShopifyClient)
    client.get_product = AsyncMock(return_value={'data': {'node': {'id': 'p', 'title': 'Live', 'descriptionHtml': '<p>Full</p>', 'productType': 'Coat', 'options': [{'name': 'Size'}], 'seo': {'title': 'SEO'}}}})
    client.graphql = AsyncMock(side_effect=[
        {'data': {'node': {'metafields': {'nodes': [{'key': 'a', 'value': '1'}], 'pageInfo': {'hasNextPage': True, 'endCursor': 'next'}}}}},
        {'data': {'node': {'metafields': {'nodes': [{'key': 'b', 'value': '2'}], 'pageInfo': {'hasNextPage': False}}}}},
        {'data': {'node': {'variants': {'nodes': [{'id': 'v'}], 'pageInfo': {'hasNextPage': False}}}}},
    ])
    fields = {row['field']: row['value'] for row in await client.get_product_review_fields('p')}
    assert set(fields) == {'title', 'body_html', 'vendor', 'product_type', 'seo_title', 'seo_description', 'tags', 'metafields', 'variant_operations'}
    assert fields['title'] == 'Live' and fields['product_type'] == 'Coat'
    assert len(fields['metafields']) == 2
    assert fields['variant_operations'] == {'options': [{'name': 'Size'}], 'variants': [{'id': 'v'}]}
    assert client.graphql.call_args_list[1].args[1]['after'] == 'next'


@pytest.mark.asyncio
async def test_live_snapshot_errors_are_actionable_instead_of_empty_values():
    client = ShopifyClient.__new__(ShopifyClient)
    client.get_product = AsyncMock(return_value={'errors': [{'message': 'denied'}]})
    with pytest.raises(RuntimeError, match='Refresh to retry'):
        await client.get_product_review_fields('p')


@pytest.mark.asyncio
async def test_review_endpoint_reads_live_fields_with_authenticated_tenant_client(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app_context import get_app_context
    from main import app
    import api.agents.intelligence as route

    db = get_app_context().services.supabase._service
    monkeypatch.setattr(db, '_get_supabase_client', lambda: None)
    shop = 'live-review.myshopify.com'
    gid = 'gid://shopify/Product/123456789'
    fields = [{'field': 'title', 'value': 'Live title'}, {'field': 'metafields', 'value': [{'key': 'material', 'value': 'cotton'}]}]
    live = SimpleNamespace(get_product_review_fields=AsyncMock(return_value=fields))
    clients = []
    def resolve(**kwargs):
        clients.append(kwargs)
        return live
    monkeypatch.setattr(route, '_resolve_shopify_client', resolve)
    monkeypatch.setattr(route, 'resolve_shop_access_token', lambda request, **kwargs: 'authenticated-token')
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        response = await client.post('/agents/intelligence/products/overview', headers={'x-shop-domain': shop}, json={'view': 'review', 'product_ids': [gid]})
    assert response.status_code == 200
    result = response.json()['products'][0]
    assert result['current_fields'] == fields and result['product_title'] == 'Live title'
    assert result['pending_reviews'] == [] and result['history'] == []
    assert clients[0]['shop_domain'] == shop and clients[0]['shop_access_token'] == 'authenticated-token'
    live.get_product_review_fields.assert_awaited_once_with(gid)


@pytest.mark.asyncio
async def test_review_endpoint_does_not_fall_back_to_another_shops_client(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app_context import get_app_context
    from main import app
    import api.agents.intelligence as route

    monkeypatch.setattr(get_app_context().services.supabase._service, '_get_supabase_client', lambda: None)
    monkeypatch.setattr(route, 'resolve_shop_access_token', lambda request, **kwargs: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        response = await client.post('/agents/intelligence/products/overview', headers={'x-shop-domain': 'no-token.myshopify.com'}, json={'view': 'review', 'product_ids': ['gid://shopify/Product/123456789']})
    assert response.status_code == 401
    assert 'Reopen the app' in response.json()['detail']
