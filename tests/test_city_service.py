import pytest
from pydantic import ValidationError

from app.models import CityCreate, CityUpdate
from app.services import AmbiguousCityError, CityNotFoundError, DuplicateCityError


def test_add_and_get(container):
    c = container.cities.add(CityCreate(name="  lisbon ", country="Portugal", tags=["Beach", "beach", " Food "],
                                        avg_daily_budget_usd=90))
    assert c["id"] == 1 and c["name"] == "lisbon" and c["tags"] == ["beach", "food"]
    assert container.cities.get(1)["country"] == "Portugal"


def test_duplicate_rejected(container):
    container.cities.add(CityCreate(name="Rome", country="Italy"))
    with pytest.raises(DuplicateCityError):
        container.cities.add(CityCreate(name="rome", country="ITALY"))
    assert container.cities.count() == 1


@pytest.mark.parametrize("kwargs", [{"name": ""}, {"name": "   "}, {"name": "X", "avg_daily_budget_usd": -5},
                                    {"name": "x" * 101}])
def test_validation(kwargs):
    with pytest.raises(ValidationError):
        CityCreate(**kwargs)


def test_list_filters(seeded):
    s = seeded.cities
    assert [c["name"] for c in s.list(country="india")] == ["Goa", "Jaipur"]
    assert [c["name"] for c in s.list(tag="beach")] == ["Bali", "Goa"]
    assert [c["name"] for c in s.list(tag="beaches")] == ["Bali", "Goa"]       # plural tolerated
    assert [c["name"] for c in s.list(q="ar")] == ["Paris"]
    assert len(s.list(limit=2)) == 2 and len(s.list(limit=2, offset=4)) == 1
    assert s.count() == 5


def test_update_fields_and_tags(container):
    container.cities.add(CityCreate(name="Goa", country="India", tags=["beach"]))
    c = container.cities.update(1, CityUpdate(avg_daily_budget_usd=75, tags=["beach", "party"]))
    assert c["avg_daily_budget_usd"] == 75 and c["tags"] == ["beach", "party"]
    c = container.cities.update(1, CityUpdate(best_season="Winter"))        # tags untouched
    assert c["tags"] == ["beach", "party"] and c["best_season"] == "Winter"
    c = container.cities.update(1, CityUpdate(tags=[]))                       # explicit clear
    assert c["tags"] == []


def test_update_missing_and_conflict(container):
    container.cities.add(CityCreate(name="A", country="X"))
    container.cities.add(CityCreate(name="B", country="X"))
    with pytest.raises(CityNotFoundError):
        container.cities.update(99, CityUpdate(region="r"))
    with pytest.raises(DuplicateCityError):
        container.cities.update(2, CityUpdate(name="A"))


def test_delete(container):
    container.cities.add(CityCreate(name="A"))
    assert container.cities.delete(1)["name"] == "A"
    with pytest.raises(CityNotFoundError):
        container.cities.delete(1)


def test_resolve_ambiguous_and_missing(container):
    container.cities.add(CityCreate(name="Paris", country="France"))
    container.cities.add(CityCreate(name="Paris", country="USA"))
    with pytest.raises(AmbiguousCityError):
        container.cities.resolve("Paris")
    assert container.cities.resolve("paris", "usa")["country"] == "USA"
    with pytest.raises(CityNotFoundError):
        container.cities.resolve("Nowhere")


def test_sql_injection_is_inert(container):
    container.cities.add(CityCreate(name="x'; DROP TABLE cities;--"))
    assert container.cities.count() == 1
    assert container.cities.list(q="'; DROP")[0]["name"].startswith("x'")
