from bs4 import BeautifulSoup


"""
GIVEN HTML CONTENT, FUNCTION WILL PARSE APPROPRIATE
TITLES AND LINKS, RETURNING A CORRESPONDING DICT
"""
def parse_titles_and_links(html):
    soup  = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else "" # parsing title if present, otherwise empty
    links = [a.get("href") for a in soup.select("a[href]")]
    return {"title": title, "links": links}