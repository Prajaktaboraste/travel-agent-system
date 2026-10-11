import pytest

from app.models import CityCreate


@pytest.fixture
def r(seeded):
    return seeded.reports


def test_country_summary(r):
    rows = r.country_summary()
    india = rows[0]
    assert india["country"] == "India" and india["city_count"] == 2 and india["rank_by_cities"] == 1
    assert india["avg_budget"] == 55.0 and india["pct_of_all_cities"] == 40.0
    assert sum(x["city_count"] for x in rows) == 5
    assert {x["rank_by_cities"] for x in rows[1:]} == {2}                # ties share rank


def test_tag_popularity(r):
    rows = {x["tag"]: x for x in r.tag_popularity()}
    assert rows["food"]["city_count"] == 4 and rows["food"]["popularity_rank"] == 1
    assert rows["beach"]["city_count"] == 2
    assert set(rows["romantic"]["cities"].split(",")) == {"Paris", "Bali"}


def test_cities_with_all_tags_relational_division(r):
    assert [c["name"] for c in r.cities_with_all_tags(["food", "culture"])] == ["Jaipur", "Paris", "Tokyo"]
    assert [c["name"] for c in r.cities_with_all_tags(["beach", "food"])] == ["Goa"]
    assert r.cities_with_all_tags(["beach", "museums"]) == []
    assert r.cities_with_all_tags([]) == []


def test_budget_buckets(r):
    rows = {b["bucket"]: b for b in r.budget_buckets()}
    assert rows["1. budget (<60)"]["city_count"] == 1                    # Jaipur 50
    assert rows["2. mid (60-119)"]["city_count"] == 2                    # Goa 60, Bali 70
    assert rows["3. premium (120+)"]["city_count"] == 2                  # Paris, Tokyo


def test_similar_cities_jaccard(r, seeded):
    goa = seeded.cities.resolve("Goa")["id"]                     # {beach, food, nightlife}
    seeded.cities.add(CityCreate(name="Rio", country="Brazil", tags=["beach", "nightlife", "food", "samba"]))
    rows = r.similar_cities(goa)
    by = {x["name"]: x for x in rows}
    assert "Goa" not in by
    assert rows[0]["name"] == "Rio" and rows[0]["shared_tags"] == 3
    assert rows[0]["jaccard"] == 0.75                              # 3 shared / (3 + 4 - 3)
    assert by["Bali"]["jaccard"] == pytest.approx(1 / 6, abs=0.001)  # beach shared; union = 3 + 4 - 1
    assert len(r.similar_cities(goa, limit=2)) == 2


def test_activity_report_and_audit_log(container):
    container.cities.add(CityCreate(name="A"))
    container.cities.add(CityCreate(name="B"))
    container.cities.delete(1)
    day = container.reports.activity_by_day()[0]
    assert (day["inserts"], day["deletes"], day["running_city_total"]) == (2, 1, 1)
    log = container.reports.audit_log()
    assert [e["action"] for e in log] == ["DELETE", "INSERT", "INSERT"]
    assert [e["action"] for e in container.reports.audit_log(action="insert")] == ["INSERT", "INSERT"]


def test_email_stats(container):
    container.cities.add(CityCreate(name="A"))
    assert container.reports.email_stats() == [{"status": "simulated", "emails": 2}]
