from dataclasses import dataclass

"""
Standard Class defining general aspects of webscraper user agent
"""
@dataclass(frozen=True)
class Settings:
    user_agent: str = "WebScraper/0.1 (+https://example.com)"
    connection_timeout: float = 5.0
    read_timeout: float = 10.0
    base_url: str = "https://example.org"

SETTINGS = Settings()