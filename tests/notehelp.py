"""A folder page as a person sees it: the list, then the note that notes-lazy.js fetches and puts under it."""
import re


def with_note(client, url, headers=None):
    """The page's HTML followed by the HTML of its lazily loaded note boxes (nothing is added when the page has no note place)."""
    html = client.get(url, headers=headers).text
    found = re.search(r"id='folder-notes' data-src='([^']+)'", html)
    if found:
        html += "\n" + client.get(found.group(1).replace("&amp;", "&"), headers=headers).json()["html"]
    return html
