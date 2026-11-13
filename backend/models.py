from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class Extraction(BaseModel):
    player: str
    set_name: str
    year: int | None
    team: str
    parallel: str
    card_number: str
    print_run: int | None
    is_rookie: bool
    is_auto: bool
    is_relic: bool
    images_match: bool | None
    warnings: list[str]


class Listing(BaseModel):
    model_config = ConfigDict(extra='forbid')
    player: str = ''
    set_name: str = ''
    year: int | None = None
    team: str = ''
    parallel: str = ''
    card_number: str = ''
    print_run: int | None = Field(default=None, gt=0)
    is_rookie: bool = False
    is_auto: bool = False
    is_relic: bool = False
    title: str = Field(default='', max_length=80)
    description: str = ''
    price: Decimal | None = Field(default=None, gt=0, le=99999999, decimal_places=2)
    category_id: str = Field(default='261328', pattern=r'^\d+$')
    condition_id: str = Field(default='4000', pattern=r'^\d+$')
    reviewed: bool = False


def issues(card):
    listing = card['listing']
    result = []
    for field in ('player', 'set_name', 'title', 'description'):
        if not listing.get(field, '').strip():
            result.append(f'Missing {field}')
    if listing.get('price') is None:
        result.append('Set a manual price')
    if not listing.get('reviewed'):
        result.append('Review listing and photo pairing')
    if not card.get('front_url') or (card.get('back') and not card.get('back_url')):
        result.append('Upload all photos')
    return result
