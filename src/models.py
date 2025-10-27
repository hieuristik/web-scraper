from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import date

"""
SearchMetadata Class based on query requirements
"""
class SearchMetadata(BaseModel):
    origin: str
    destination: str
    date: date
    passengers: int = 1
    cabin_class: str = "economy"

"""
FlightItem Class based on required flight attributes
"""
class FlightItem(BaseModel):
    flight_number: str
    departure_time: str   # "HH:MM" local
    arrival_time: str     # "HH:MM" local
    points_required: int
    cash_price_usd: float
    taxes_fees_usd: float
    cpp: float            # cents per point

    @field_validator("flight_number")
    @classmethod
    def normalize_flight(cls, v):
        return v.strip().upper()


"""
SearchResult Class containing search metadata encapsulated in
SearchMetadata Class and total flights encapsulated in a list
of FlighItem classes
"""
class SearchResult(BaseModel):
    search_metadata: SearchMetadata
    flights: List[FlightItem] = Field(default_factory=list)
    total_results: int = 0

    @field_validator("total_results")
    @classmethod
    def check_count(cls, v, info):
        # keep in sync with flights
        return v
