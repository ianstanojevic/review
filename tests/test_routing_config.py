import pytest

from review.config import parse_dotenv, parse_rate_limits
from review.riot_client import get_region, parse_riot_id


def test_parse_riot_id():
    assert parse_riot_id("flintgnuggaren#ian") == ("flintgnuggaren", "ian")
    assert parse_riot_id("  Some Name #EUW ") == ("Some Name", "EUW")
    with pytest.raises(ValueError):
        parse_riot_id("noTag")
    with pytest.raises(ValueError):
        parse_riot_id("#tag")


@pytest.mark.parametrize("code,platform,regional", [
    ("EUW", "euw1", "europe"), ("eune", "eun1", "europe"),
    ("NA", "na1", "americas"), ("KR", "kr", "asia"),
])
def test_regions(code, platform, regional):
    r = get_region(code)
    assert (r.platform, r.regional) == (platform, regional)


def test_sea_account_host_is_asia():
    assert get_region("OCE").account_host == "asia"


def test_unknown_region():
    with pytest.raises(ValueError):
        get_region("MARS")


def test_dotenv_and_limits():
    env = parse_dotenv('# c\nA=1\nexport B="two"\n\nC = \'3\'\nbad line')
    assert env == {"A": "1", "B": "two", "C": "3"}
    assert parse_rate_limits("20:1, 100:120") == ((20, 1.0), (100, 120.0))
