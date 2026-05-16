"""Pyzotero wrapper with exponential backoff and retry."""

import time
import json
from pyzotero import zotero
from pyzotero.zotero_errors import HTTPError


class ZoteroClient:
    def __init__(self, config):
        self.zot = zotero.Zotero(
            config["zotero_user_id"],
            "user",
            config["ZOTERO_API_KEY"],
        )
        self._max_retries = 3
        self._base_delay = 1.0

    def _retry(self, func, *args, **kwargs):
        """Call func with exponential backoff on 429/5xx errors."""
        for attempt in range(self._max_retries + 1):
            try:
                return func(*args, **kwargs)
            except HTTPError as e:
                status = getattr(e, "status_code", None) or _extract_status(e)
                if status == 429 or (status and status >= 500):
                    if attempt == self._max_retries:
                        raise
                    retry_after = _extract_retry_after(e)
                    delay = retry_after if retry_after else self._base_delay * (2 ** attempt)
                    time.sleep(delay)
                else:
                    raise
            except Exception:
                raise

    def search(self, query, limit=25):
        """Search library by query string. Returns list of item dicts."""
        items = self._retry(self.zot.top, q=query, limit=limit)
        return items

    def search_by_doi(self, doi, title_hint=None):
        """Search for an item by DOI. Returns matching item or None.

        Zotero's q= search doesn't index DOI fields, so we search by title
        (if provided) or by DOI string, then filter by exact DOI match.
        """
        # Strategy 1: if we have a title hint, search by that (most reliable)
        if title_hint:
            # Use first few significant words of title
            words = [w for w in title_hint.split() if len(w) > 3][:4]
            query = " ".join(words) if words else title_hint[:30]
            items = self._retry(self.zot.top, q=query, limit=25)
            for item in items:
                item_doi = item["data"].get("DOI", "")
                if item_doi and item_doi.lower() == doi.lower():
                    return item

        # Strategy 2: search by DOI string with qmode=everything
        items = self._retry(self.zot.top, q=doi, qmode="everything", limit=10)
        for item in items:
            item_doi = item["data"].get("DOI", "")
            if item_doi and item_doi.lower() == doi.lower():
                return item

        # Strategy 3: search by DOI suffix (last part after /)
        doi_suffix = doi.split("/")[-1] if "/" in doi else doi
        items = self._retry(self.zot.top, q=doi_suffix, limit=10)
        for item in items:
            item_doi = item["data"].get("DOI", "")
            if item_doi and item_doi.lower() == doi.lower():
                return item

        return None

    def count_items(self):
        """Return total number of items in library."""
        return self._retry(self.zot.count_items)

    def collections(self):
        """Return all collections (paginated to handle libraries with 100+)."""
        all_colls = []
        start = 0
        page_size = 100
        while True:
            batch = self._retry(self.zot.collections, start=start, limit=page_size)
            if not batch:
                break
            all_colls.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return all_colls

    def create_collection(self, name, parent_key=None):
        """Create a new collection. Returns the created collection."""
        payload = {"name": name}
        if parent_key:
            payload["parentCollection"] = parent_key
        result = self._retry(self.zot.create_collections, [payload])
        if result and "successful" in result:
            return list(result["successful"].values())[0]
        return result

    def create_item(self, item_data, collection_keys=None):
        """Create a new library item. Returns the created item."""
        if collection_keys:
            item_data["collections"] = collection_keys
        template = self.zot.item_template(item_data.get("itemType", "journalArticle"))
        for key, val in item_data.items():
            if key in template:
                template[key] = val
        result = self._retry(self.zot.create_items, [template])
        if result and "successful" in result:
            return list(result["successful"].values())[0]
        return result

    def get_item(self, key):
        """Get a single item by key."""
        return self._retry(self.zot.item, key)

    def children(self, parent_key):
        """Get child items of a parent."""
        return self._retry(self.zot.children, parent_key)

    def delete_item(self, item):
        """Permanently delete an item. Requires the item dict (for version)."""
        return self._retry(self.zot.delete_item, item)

    def update_item(self, item):
        """Update an existing item."""
        return self._retry(self.zot.update_item, item)

    def collection_items(self, collection_key, limit=100):
        """Get items in a collection."""
        return self._retry(self.zot.collection_items, collection_key, limit=limit)

    def trash_item(self, item):
        """Move an item to trash (sets deleted=1)."""
        item["data"]["deleted"] = 1
        return self._retry(self.zot.update_item, item)

    def remove_from_collection(self, collection_key, item):
        """Remove an item from a collection without trashing it."""
        return self._retry(self.zot.deletefrom_collection, collection_key, item)

    def list_trash(self, limit=50):
        """List items in the trash."""
        return self._retry(self.zot.trash, limit=limit)

    def empty_trash(self):
        """Permanently delete all items in the trash."""
        trashed = self.list_trash(limit=500)
        if not trashed:
            return 0
        self._retry(self.zot.delete_item, trashed)
        return len(trashed)


def _extract_status(exc):
    """Try to extract HTTP status code from exception."""
    msg = str(exc)
    for code in [429, 500, 502, 503, 504]:
        if str(code) in msg:
            return code
    return None


def _extract_retry_after(exc):
    """Try to extract Retry-After value from exception."""
    msg = str(exc)
    if "Retry-After" in msg:
        try:
            parts = msg.split("Retry-After")
            val = "".join(c for c in parts[1][:10] if c.isdigit())
            if val:
                return int(val)
        except (IndexError, ValueError):
            pass
    return None
