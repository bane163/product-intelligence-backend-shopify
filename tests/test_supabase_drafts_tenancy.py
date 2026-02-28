from services.supabase_service import SupabaseService


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table_name: str, rows: dict[str, list[dict]], eq_calls: list[tuple]):
        self._table_name = table_name
        self._rows = rows
        self._eq_calls = eq_calls

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key: str, value):
        self._eq_calls.append((self._table_name, key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _Result(self._rows.get(self._table_name, []))


class _FakeClient:
    def __init__(self, rows: dict[str, list[dict]]):
        self._rows = rows
        self.eq_calls: list[tuple] = []

    def table(self, table_name: str):
        return _Query(table_name=table_name, rows=self._rows, eq_calls=self.eq_calls)


def test_tenant_filtered_reads_exclude_null_shop_domain(monkeypatch):
    service = SupabaseService()
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)

    service.save_product_draft(
        draft_id="draft-null",
        run_id=None,
        import_mode="auto",
        draft_name="No Tenant",
        shop_domain=None,
        products=[{"title": "No Tenant Product"}],
    )
    service.save_product_draft(
        draft_id="draft-tenant",
        run_id=None,
        import_mode="auto",
        draft_name="Tenant Draft",
        shop_domain="shop-a.myshopify.com",
        products=[{"title": "Tenant Product"}],
    )

    drafts = service.list_product_drafts(shop_domain="shop-a.myshopify.com")
    draft_ids = {item.get("draft_id") for item in drafts}
    assert "draft-tenant" in draft_ids
    assert "draft-null" not in draft_ids
    assert (
        service.get_product_draft("draft-null", shop_domain="shop-a.myshopify.com")
        is None
    )


def test_tenant_filtered_list_queries_include_shop_domain_predicate(monkeypatch):
    rows = {
        "product_drafts": [
            {
                "draft_id": "draft-tenant",
                "shop_domain": "shop-a.myshopify.com",
                "created_at": "2026-02-28T00:00:00Z",
            }
        ],
        "submitted_documents": [],
    }
    fake_client = _FakeClient(rows)
    service = SupabaseService()
    monkeypatch.setattr(service, "_get_supabase_client", lambda: fake_client)

    service.list_product_drafts(shop_domain="shop-a.myshopify.com")
    assert (
        "product_drafts",
        "shop_domain",
        "shop-a.myshopify.com",
    ) in fake_client.eq_calls
    assert (
        "submitted_documents",
        "shop_domain",
        "shop-a.myshopify.com",
    ) in fake_client.eq_calls


def test_tenant_filtered_submitted_query_includes_shop_domain_predicate(monkeypatch):
    rows = {
        "submitted_documents": [
            {
                "submitted_id": "submitted-tenant",
                "shop_domain": "shop-a.myshopify.com",
                "submitted_at": "2026-02-28T00:00:00Z",
            }
        ]
    }
    fake_client = _FakeClient(rows)
    service = SupabaseService()
    monkeypatch.setattr(service, "_get_supabase_client", lambda: fake_client)

    service.list_submitted_documents(shop_domain="shop-a.myshopify.com")
    assert (
        "submitted_documents",
        "shop_domain",
        "shop-a.myshopify.com",
    ) in fake_client.eq_calls
