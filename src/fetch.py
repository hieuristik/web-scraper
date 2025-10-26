import httpx
from .config import SETTINGS

"""
DEFINES AND RETURNS AN HTTPX CLIENT USING THE
SETTINGS CLASS DEFINED IN CONFIG.PY
"""
def get_client():
    return httpx.Client(
        headers={"User-Agent": SETTINGS.user_agent},
        timeout=httpx.Timeout(SETTINGS.read_timeout, connect=SETTINGS.connection_timeout),
        http2=True,
        follow_redirects=True
    )


"""
USES THE HTTPX CLIENT IN ORDER TO FETCH WEB PAGES
AND RETRIEVE TEXT INFORMATION FROM SPECIFIED PAGES
"""
def fetch_text(url):
    with get_client() as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text
