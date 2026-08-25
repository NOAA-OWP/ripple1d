import pystac


def load_stac_item(test_data: str) -> pystac.Item:
    item = pystac.Item.from_file(test_data)
    item.set_self_href(test_data)
    return item
