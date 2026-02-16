import pytest

from ai.models import ProductsList, ProductInput, ProductOption
from ai.product_mapper import (
    productinput_to_create_args,
    create_products_from_productslist,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    async def create_product(
        self, title, body_html="", vendor=None, product_options=None
    ):
        self.calls.append(
            {
                "title": title,
                "body_html": body_html,
                "vendor": vendor,
                "product_options": product_options,
            }
        )
        return {"data": {"productCreate": {"product": {"title": title}}}}


@pytest.mark.asyncio
async def test_mapping_and_create_calls():
    p = ProductInput(
        title="T",
        body_html="Desc",
        vendor="V",
        options=[ProductOption(name="Color", values=["Red", "Blue"])],
    )
    pl = ProductsList(products=[p])

    args = productinput_to_create_args(p)
    assert args["title"] == "T"
    assert args["body_html"] == "Desc"
    assert args["vendor"] == "V"
    assert args["product_options"] == [
        {"name": "Color", "values": [{"name": "Red"}, {"name": "Blue"}]}
    ]

    client = FakeClient()
    res = await create_products_from_productslist(client, pl)
    assert len(res) == 1
    assert client.calls[0]["title"] == "T"
